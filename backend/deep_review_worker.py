"""Paperclip process adapter worker — Decision Pulse Deep Review의 세 역할 실행기.

Paperclip이 ticket을 agent에게 배정하면 이 프로세스를 실행한다 (adapterType=process).
    python deep_review_worker.py researcher | analyst | critic

역할별 권한 (실제 실행 경로로 강제)
- researcher: Hermes oneshot `-t decision-debt,gbrain --ignore-rules` (두 read-only MCP만). 실행 후 Hermes state.db로
              tool 사용 경로를 감사한다 (Review Memo와 같은 audit). 허용 외 tool이 쓰였거나 필수 조회가 없으면 실패.
- analyst / critic: tool이 없는 chat completion 한 번 (요청에 tools 자체가 없음). 입력은 ticket 본문 JSON뿐.
공통
- Decision Pulse SQLite에 쓰지 않는다 (import는 schema / 검증 코드뿐). gbrain에 쓰지 않는다.
- Paperclip에는 자기 ticket에 대해서만: checkout → 결과 comment → status(done | blocked) → cost 보고.
- 항상 exit 0 (실패도 ticket comment로 보고). Paperclip의 transient retry로 LLM이 다시 호출되지 않게 한다.
- API key / token은 출력·comment·로그에 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    DEEP_REVIEW_LLM_TIMEOUT_SECONDS,
    DEEP_REVIEW_MODEL,
    DEEP_REVIEW_PROVIDER_SORT,
    DEEP_REVIEW_REASONING_EFFORT,
    DEEP_REVIEW_RESEARCH_TIMEOUT_SECONDS,
    HERMES_BIN,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)
from services import deep_review_contract as contract  # noqa: E402
from services.paperclip_client import PaperclipClient, PaperclipError  # noqa: E402

ROLES = ("researcher", "analyst", "critic")
INSTRUCTIONS_DIR = Path(__file__).resolve().parent.parent / "agents" / "deep-review"
RESEARCH_TOOLSETS = ("decision-debt", "gbrain")


def instructions(role: str) -> str:
    return (INSTRUCTIONS_DIR / f"{role}.md").read_text(encoding="utf-8")


def _mask(text: str) -> str:
    return text.replace(OPENROUTER_API_KEY, "***") if OPENROUTER_API_KEY else text


# ---------------------------------------------------------------- researcher: Hermes + read-only MCP


def research_prompt(inp: dict) -> str:
    snapshot = {k: inp[k] for k in ("decision_id", "authoritative_facts", "observed_state", "evidence_items")}
    return (
        instructions("researcher")
        + "\n\n---\n대상 ticket 입력 (deep-review-input, backend snapshot):\n```json\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + "\n```\n\n출력 JSON만 쓴다 (설명 없이 '{'로 시작)."
    )


async def _run_hermes(prompt: str) -> tuple[int | None, str, str, dict]:
    import tempfile

    from services import review_memo

    with tempfile.TemporaryDirectory(prefix="deep-review-research-") as tmp:
        usage_path = Path(tmp) / "usage.json"
        argv = [HERMES_BIN, "-z", prompt, "-t", ",".join(RESEARCH_TOOLSETS), "--ignore-rules", "--usage-file", str(usage_path)]
        env = review_memo._hermes_env()  # backend/.env 값은 넘기지 않는다
        env = {k: v for k, v in env.items() if not k.startswith("PAPERCLIP_")}  # Paperclip run token도 넘기지 않는다
        proc = await asyncio.create_subprocess_exec(*argv, cwd=tmp, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=DEEP_REVIEW_RESEARCH_TIMEOUT_SECONDS)
            code = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            code = None
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            usage = {}
    return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), usage


def run_researcher(inp: dict) -> tuple[dict, dict]:
    from services import review_memo

    t0 = time.monotonic()
    code, out, err, usage = asyncio.run(_run_hermes(research_prompt(inp)))
    audit = review_memo.audit_session(usage.get("session_id"))
    audit["toolsets"] = list(RESEARCH_TOOLSETS)
    meta = {"runtime": "hermes", "model": usage.get("model"), "provider": usage.get("provider"),
            "session_id": usage.get("session_id"), "cost_usd": usage.get("estimated_cost_usd"),
            "cost_status": usage.get("cost_status"), "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"), "tool_audit": audit,
            "elapsed_seconds": round(time.monotonic() - t0, 1)}
    if code is None:
        raise contract.ContractError(f"Hermes 시간 초과 ({DEEP_REVIEW_RESEARCH_TIMEOUT_SECONDS:g}s)")
    if code != 0 or usage.get("failed"):
        raise contract.ContractError(f"Hermes 실행 실패 (exit {code}): {review_memo._summarize_error(str(usage.get('failure') or err or out))}")
    if not audit["ok"]:
        problems = audit.get("error") or "; ".join(
            ([f"허용되지 않은 tool: {', '.join(audit['disallowed'])}"] if audit["disallowed"] else [])
            + ([f"필수 조회 누락: {', '.join(audit['missing_required'])}"] if audit["missing_required"] else []))
        raise contract.ContractError(f"tool 사용 경로 검증 실패: {problems}")
    findings = contract.extract_json(out)
    return contract.assemble_research(findings, inp), meta


# ---------------------------------------------------------------- analyst / critic: tool 없는 LLM 호출


def llm_messages(role: str, inp: dict) -> list[dict]:
    data = {k: v for k, v in inp.items() if k not in ("role", "iteration")}
    return [
        {"role": "system", "content": instructions(role)},
        {"role": "user", "content": "deep-review-input:\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2)
                                    + "\n```\n출력 JSON만 쓴다."},
    ]


def call_llm(role: str, inp: dict, client: httpx.Client | None = None) -> tuple[dict, dict]:
    if not OPENROUTER_API_KEY or not DEEP_REVIEW_MODEL:
        raise contract.ContractError("LLM 설정 없음 (OPENROUTER_API_KEY / DEEP_REVIEW_MODEL)")
    body = {
        "model": DEEP_REVIEW_MODEL,
        "temperature": 0.1,
        "messages": llm_messages(role, inp),
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": f"deep_review_{role}", "schema": contract.output_schema(role)}},
        "provider": {"require_parameters": True, **({"sort": DEEP_REVIEW_PROVIDER_SORT} if DEEP_REVIEW_PROVIDER_SORT else {})},
        "usage": {"include": True},
        # tools 없음: 이 역할은 입력 JSON 외의 정보원을 쓸 수 없다
    }
    if DEEP_REVIEW_REASONING_EFFORT == "off":
        body["reasoning"] = {"enabled": False}
    elif DEEP_REVIEW_REASONING_EFFORT:
        body["reasoning"] = {"effort": DEEP_REVIEW_REASONING_EFFORT}
    t0 = time.monotonic()
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(DEEP_REVIEW_LLM_TIMEOUT_SECONDS, connect=10))
    try:
        res = client.post(f"{OPENROUTER_BASE_URL}/chat/completions", json=body,
                          headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"})
    except httpx.TransportError as e:
        raise contract.ContractError(f"LLM 연결 실패: {type(e).__name__}") from e
    finally:
        if own:
            client.close()
    if res.status_code != 200:
        raise contract.ContractError(_mask(f"LLM HTTP {res.status_code}: {res.text[:200]}"))
    try:
        data = res.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, ValueError, TypeError, IndexError) as e:
        raise contract.ContractError(f"LLM 응답 해석 실패: {e}") from e
    usage = data.get("usage") or {}
    meta = {"runtime": "chat_completion_no_tools", "model": data.get("model") or DEEP_REVIEW_MODEL, "provider": "openrouter",
            "served_by": data.get("provider"), "cost_usd": usage.get("cost"), "cost_status": "reported" if usage.get("cost") is not None else None,
            "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
            "finish_reason": choice.get("finish_reason"), "elapsed_seconds": round(time.monotonic() - t0, 1)}
    if choice.get("finish_reason") == "length":
        raise contract.ContractError("LLM 출력이 max_tokens에서 잘림")
    return contract.extract_json(content), meta


def run_analyst(inp: dict, client=None) -> tuple[dict, dict]:
    payload, meta = call_llm("analyst", inp, client)
    return contract.validate_analysis(payload, inp["research"]), meta


def run_critic(inp: dict, client=None) -> tuple[dict, dict]:
    payload, meta = call_llm("critic", inp, client)
    return contract.validate_critique(payload, inp["research"], inp["analysis"]), meta


RUNNERS = {"researcher": run_researcher, "analyst": run_analyst, "critic": run_critic}


# ---------------------------------------------------------------- Paperclip ticket 처리


def _cost_event(company_id: str, agent_id: str, issue_id: str, run_id: str, role: str, meta: dict) -> dict | None:
    cost = meta.get("cost_usd")
    if not isinstance(cost, (int, float)):
        return None
    return {
        "agentId": agent_id, "issueId": issue_id, "heartbeatRunId": run_id,
        "provider": meta.get("provider") or "openrouter", "model": meta.get("model") or "unknown",
        "costCents": max(1, math.ceil(cost * 100)) if cost > 0 else 0,
        "costStatus": "estimated" if meta.get("runtime") == "hermes" else "reported",
        "inputTokens": meta.get("input_tokens") or 0, "outputTokens": meta.get("output_tokens") or 0,
        "occurredAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",  # Paperclip은 Z 형식만
        "billingCode": f"deep-review:{role}",
        "idempotencyKey": f"deep-review:{run_id}:{role}",
    }


def main(role: str) -> int:
    if role not in ROLES:
        print(f"unknown role: {role}")
        return 0
    env = os.environ
    run_id, agent_id, company_id = env.get("PAPERCLIP_RUN_ID"), env.get("PAPERCLIP_AGENT_ID"), env.get("PAPERCLIP_COMPANY_ID")
    api = (env.get("PAPERCLIP_API_URL") or "").rstrip("/")
    api = api if api.endswith("/api") else api + "/api"
    if not (run_id and agent_id and company_id and env.get("PAPERCLIP_API_KEY")):
        print("Paperclip run 환경이 아님 (PAPERCLIP_RUN_ID / AGENT_ID / COMPANY_ID / API_KEY 필요)")
        return 0
    with PaperclipClient(api, token=env["PAPERCLIP_API_KEY"], run_id=run_id) as pc:
        issue_id = env.get("PAPERCLIP_TASK_ID") or ((pc.get_run(run_id).get("contextSnapshot") or {}).get("issueId"))
        if not issue_id:
            print("이 run에 연결된 ticket이 없음 (할 일 없음)")
            return 0
        try:  # atomic checkout: 다른 agent/run이 잡고 있으면 409 → 아무것도 하지 않는다
            pc.checkout_issue(issue_id, agent_id, ["todo", "in_progress"])
        except PaperclipError as e:
            print(f"checkout 실패 → 처리하지 않음: {e}")
            return 0
        issue = pc.get_issue(issue_id)
        inp = contract.parse_input(issue.get("description") or "")
        t0 = time.monotonic()
        meta: dict = {}
        try:
            if inp is None:
                raise contract.ContractError("ticket에 deep-review-input 블록이 없음")
            if inp.get("role") != role:
                raise contract.ContractError(f"이 ticket은 {inp.get('role')} 역할용 (현재 agent: {role})")
            output, meta = RUNNERS[role](inp)
            envelope = {"ok": True, "role": role, "iteration": inp.get("iteration", 0), "output": output, "meta": meta}
        except contract.ContractError as e:
            envelope = {"ok": False, "role": role, "iteration": (inp or {}).get("iteration", 0), "error": _mask(str(e))[:600],
                        "meta": meta}
        except Exception as e:  # noqa: BLE001 — 예상 못 한 오류도 ticket으로 보고하고 exit 0
            envelope = {"ok": False, "role": role, "iteration": (inp or {}).get("iteration", 0),
                        "error": f"worker 오류: {type(e).__name__}", "meta": meta}
        envelope["meta"] = {**(envelope.get("meta") or {}), "worker_seconds": round(time.monotonic() - t0, 1),
                            "paperclip_run_id": run_id}
        try:
            pc.add_comment(issue_id, contract.render_output_comment(envelope))
            cost = _cost_event(company_id, agent_id, issue_id, run_id, role, envelope["meta"])
            if cost:
                try:
                    pc.report_cost(company_id, cost)
                except PaperclipError as e:
                    print(f"cost 보고 실패 (무시): {e}")
            if envelope["ok"]:
                pc.patch_issue(issue_id, {"status": "done"})
            else:  # 실패도 ticket을 닫는다 (결과 판정은 backend가 output의 ok로 한다)
                try:
                    pc.patch_issue(issue_id, {"status": "cancelled"})
                except PaperclipError:
                    pc.patch_issue(issue_id, {"status": "done"})
        except PaperclipError as e:  # 보고 실패 → backend가 stage timeout으로 FAILED 처리 (LLM 재호출 없음)
            print(f"Paperclip 보고 실패: {e}")
            return 0
        print(json.dumps({"role": role, "issue": issue.get("identifier"), "ok": envelope["ok"],
                          "error": envelope.get("error")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
