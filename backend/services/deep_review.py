"""Deep Review: REVIEW_DUE 결정을 Paperclip 조직(Researcher → Analyst → Critic)이 검토한다 (advisory).

기존 Review Memo(single agent, Hermes)와 별개 기능이며 대체하지 않는다.

역할 분담
- Paperclip = orchestration / control plane: 조직·보고선, ticket(작업 계보), 배정 → agent 실행(wake), atomic checkout,
  budget, 실행 기록(heartbeat run / comment / cost).
- 이 모듈 = deterministic handoff gate (AI 아님): 각 ticket 결과를 schema 검증한 뒤에만 다음 ticket을 만든다.
  revision loop 상한(1회)과 종료 조건을 코드로 강제한다.
- SQLite = Decision Pulse의 원본. 여기서는 deep_review_runs / deep_review_artifacts에만 쓴다.
  Decision / decision_review_state / review_events / candidates / review memo는 바꾸지 않는다.

흐름
  POST → (REVIEW_DUE 확인, 진행 중 run 없음) → run PENDING + snapshot
       → Paperclip parent ticket "Deep Review: <id>" + research ticket(Researcher) → RESEARCHING
  monitor(run_forever, 3초) → ticket done → output comment 검증 → artifact 저장 → 다음 ticket
       research → analysis(ANALYZING) → critique(CRITIQUING)
       critique PASS → COMPLETED (final_verdict PASS)
       critique REVISE, revision 0회 → analysis 재작성(REVISING) → critique 한 번 더
       그래도 REVISE → HUMAN_REVIEW_REQUIRED
  모든 종료 상태의 결론은 사람에게 넘어간다 (Decision 자동 변경 없음).
  Paperclip down / 검증 실패 / timeout / budget 차단 → FAILED (재시도 가능, 기존 기능 영향 없음)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import db
from config import (
    DEEP_REVIEW_ENABLED,
    DEEP_REVIEW_MAX_REVISIONS,
    DEEP_REVIEW_ORG_FILE,
    DEEP_REVIEW_POLL_SECONDS,
    DEEP_REVIEW_STAGE_TIMEOUT_SECONDS,
    PAPERCLIP_API_URL,
    PAPERCLIP_UI_URL,
)
from services import deep_review_contract as contract
from services import review_memo
from services.paperclip_client import PaperclipClient, PaperclipError, PaperclipUnavailable
from services.review_trigger import format_review_trigger_v2

log = logging.getLogger("deep_review")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [deep_review] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

ENABLED = DEEP_REVIEW_ENABLED
STATUS: dict = {"enabled": ENABLED, "last_error": None, "last_tick_at": None}

# 단계 → (역할, run status)
ROLE_STATUS = {"researcher": "RESEARCHING", "analyst": "ANALYZING", "critic": "CRITIQUING"}
STAGES = (("research", "Evidence Research", "researcher"), ("analysis", "Decision Analysis", "analyst"),
          ("critique", "Critical Review", "critic"))
FAILED_RUN_STATES = ("failed", "timed_out", "cancelled", "error")
LIVE_RUN_STATES = ("queued", "running", "scheduled_retry")


class DeepReviewError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": str(self), **self.extra}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _client() -> PaperclipClient:
    return PaperclipClient(PAPERCLIP_API_URL)


def load_org(path: Path | None = None) -> dict:
    """setup_paperclip_org.py가 만든 company / goal / project / agent id"""
    p = path or DEEP_REVIEW_ORG_FILE
    try:
        org = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DeepReviewError(503, "paperclip_org_missing",
                              f"Paperclip 조직 설정이 없습니다 ({p}). backend/scripts/setup_paperclip_org.py를 먼저 실행하세요.") from e
    for key in ("company_id", "project_id", "goal_id", "agents"):
        if not org.get(key):
            raise DeepReviewError(503, "paperclip_org_invalid", f"Paperclip 조직 설정에 {key}가 없습니다 ({p})")
    if set(org["agents"]) != {"researcher", "analyst", "critic"}:
        raise DeepReviewError(503, "paperclip_org_invalid", "Paperclip 조직 설정의 agents는 researcher / analyst / critic 3개여야 합니다")
    return org


# ---------------------------------------------------------------- 시작


def snapshot_for(decision_id: str) -> tuple[dict, dict, dict]:
    """(list_review_due 항목, detail, snapshot). Review Memo와 같은 read service를 쓴다. REVIEW_DUE가 아니면 409."""
    try:
        item, detail = review_memo.review_context(decision_id)
    except review_memo.ReviewMemoError as e:
        raise DeepReviewError(e.status, e.code, str(e) if e.code != "not_review_due"
                              else "현재 REVIEW_DUE인 결정이 아니어서 Deep Review를 시작하지 않습니다.", **e.extra) from e
    items = review_memo.evidence_items(decision_id, detail.get("sources", []))
    detection = review_memo.DETECTION_TEXT_V4.get(item.get("detected_by"), str(item.get("detected_by")))
    snap = contract.build_snapshot(item, detail, items, detection, format_review_trigger_v2(item.get("trigger")))
    return item, detail, snap


def start(decision_id: str, project_id: int | None = None) -> dict:
    """run(PENDING) 생성. 실제 Paperclip ticket 생성은 kickoff(background)."""
    if not ENABLED:
        raise DeepReviewError(503, "deep_review_disabled", "Deep Review가 꺼져 있습니다 (DEEP_REVIEW_ENABLED=false)")
    decision = db.get_decision(decision_id)
    if decision is None:
        raise DeepReviewError(404, "decision_not_found", f"Decision not found: {decision_id}")
    if project_id is not None and decision.project_id != project_id:
        raise DeepReviewError(404, "decision_not_found", f"Decision not found in project {project_id}: {decision_id}")
    load_org()  # 설정 오류는 run을 만들기 전에 알린다
    active = next((r for r in db.list_deep_review_runs(decision_id) if r["status"] in db.DEEP_REVIEW_ACTIVE), None)
    if active:
        raise DeepReviewError(409, "deep_review_running", "이 결정의 Deep Review가 이미 진행 중입니다.", run_id=active["id"])
    item, detail, snap = snapshot_for(decision_id)
    event_id = item["event"]["event_id"] if item.get("event") else None
    changed_at = (detail.get("review_state") or {}).get("last_changed_at")
    try:
        return db.create_deep_review_run(detail["project_id"], decision_id, event_id, changed_at, snap)
    except db.DeepReviewInProgress as e:
        raise DeepReviewError(409, "deep_review_running", "이 결정의 Deep Review가 이미 진행 중입니다.") from e


def _intro(run: dict, role: str, iteration: int) -> str:
    lines = [
        f"Decision Pulse Deep Review #{run['id']} · {run['decision_id']}",
        f"역할: {contract.ROLE_LABELS[role]}" + (f" (revision {iteration})" if iteration else ""),
        "아래 deep-review-input JSON만 입력으로 사용한다. 출력은 schema 검증 후에만 다음 단계로 넘어간다.",
        "이 ticket은 Decision을 수정하지 않는다. 최종 판단은 사람이 한다.",
    ]
    return "\n".join(lines)


def _create_stage_issue(pc: PaperclipClient, org: dict, run: dict, role: str, iteration: int, data: dict) -> dict:
    title = f"{contract.STAGE_TITLES[role]} — {run['decision_id']}" + (f" (revision {iteration})" if iteration else "")
    body = {
        "title": title,
        "description": contract.render_input(role, iteration, data, _intro(run, role, iteration)),
        "status": "todo",
        "priority": "medium",
        "projectId": org["project_id"],
        "goalId": org["goal_id"],
        "parentId": run["paperclip"]["parent_issue_id"],
        "assigneeAgentId": org["agents"][role],  # 역할당 한 agent에게만 배정 (단일 assignee)
    }
    return pc.create_issue(org["company_id"], body)


def _stage_ref(issue: dict, role: str, iteration: int) -> dict:
    return {"issue_id": issue["id"], "identifier": issue.get("identifier"), "role": role, "iteration": iteration}


def kickoff(run_id: int, client: PaperclipClient | None = None) -> dict | None:
    """parent ticket + research ticket 생성 → RESEARCHING. Paperclip이 없으면 FAILED (예외를 밖으로 내지 않음)."""
    run = db.get_deep_review_run(run_id)
    if run is None or run["status"] != "PENDING":
        return run
    pc = client or _client()
    try:
        org = load_org()
        pc.health()
        parent = pc.create_issue(org["company_id"], {
            "title": f"Deep Review: {run['decision_id']}",
            "description": (f"Decision Pulse Deep Review #{run['id']}\n\n"
                            f"REVIEW_DUE 결정 {run['decision_id']}을 Evidence Researcher → Decision Analyst → Review Critic 순서로 검토한다.\n"
                            "결과는 사람의 재검토로 넘어간다. Decision은 자동으로 바뀌지 않는다."),
            "status": "todo", "priority": "medium", "projectId": org["project_id"], "goalId": org["goal_id"],
        })
        paperclip = {"company_id": org["company_id"], "project_id": org["project_id"], "goal_id": org["goal_id"],
                     "parent_issue_id": parent["id"], "parent_identifier": parent.get("identifier"), "stages": []}
        run = db.update_deep_review_run(run_id, expect_status="PENDING", paperclip=paperclip) or run
        snap = run["input_snapshot"]
        issue = _create_stage_issue(pc, org, run, "researcher", 0, snap)
        paperclip["stages"].append(_stage_ref(issue, "researcher", 0))
        now = _now()
        run = db.update_deep_review_run(run_id, expect_status="PENDING", status="RESEARCHING", paperclip=paperclip,
                                        started_at=now, stage_started_at=now)
        log.info("run #%s %s → RESEARCHING (parent %s, research %s)", run_id, run["decision_id"],
                 parent.get("identifier"), issue.get("identifier"))
        return run
    except DeepReviewError as e:
        return fail(run_id, str(e), pc)
    except PaperclipError as e:
        return fail(run_id, f"Paperclip 오류: {e}", pc)
    except Exception as e:  # noqa: BLE001
        log.exception("run #%s kickoff 오류", run_id)
        return fail(run_id, f"Deep Review 시작 오류: {type(e).__name__}", pc)
    finally:
        if client is None:
            pc.close()


# ---------------------------------------------------------------- 진행 (monitor)


def _current_stage(run: dict) -> dict | None:
    stages = (run.get("paperclip") or {}).get("stages") or []
    return stages[-1] if stages else None


def _artifact_payload(run_id: int, role: str, iteration: int | None = None) -> dict | None:
    arts = [a for a in db.list_deep_review_artifacts(run_id) if a["role"] == role
            and (iteration is None or a["iteration"] == iteration)]
    return arts[-1]["payload"] if arts else None


def _find_output(pc: PaperclipClient, issue_id: str, agent_id: str) -> tuple[dict | None, dict | None]:
    """담당 agent가 쓴 마지막 output comment만 인정한다 (다른 작성자 comment는 무시)."""
    found, comment = None, None
    for c in pc.list_comments(issue_id):
        if c.get("authorAgentId") != agent_id:
            continue
        env = contract.parse_output_comment(c.get("body") or "")
        if env is not None:
            found, comment = env, c
    return found, comment


def _paperclip_run_problem(pc: PaperclipClient, company_id: str, issue_id: str) -> str | None:
    """이 ticket의 Paperclip 실행이 실패로 끝났고 더 실행될 것이 없으면 이유. (budget 차단 / adapter 오류 / timeout)"""
    runs = [r for r in pc.list_runs(company_id, limit=200) if r.get("issueId") == issue_id
            or (r.get("contextSnapshot") or {}).get("issueId") == issue_id]
    if not runs or any(r.get("status") in LIVE_RUN_STATES for r in runs):
        return None
    last = max(runs, key=lambda r: r.get("createdAt") or "")
    if last.get("status") in FAILED_RUN_STATES:
        return f"Paperclip run {last.get('status')}: {(last.get('error') or last.get('errorCode') or '')[:200]}"
    return None


def advance(run: dict, client: PaperclipClient | None = None) -> dict | None:
    """진행 중 run 하나를 한 단계 진행시킨다. 실패는 FAILED로 기록하고 예외를 내지 않는다."""
    if run["status"] not in db.DEEP_REVIEW_ACTIVE:
        return run
    pc = client or _client()
    try:
        return _advance(run, pc)
    except PaperclipUnavailable as e:
        # 잠깐 끊긴 것일 수 있다: stage timeout까지는 기다린다
        if _stage_elapsed(run) > DEEP_REVIEW_STAGE_TIMEOUT_SECONDS:
            return fail(run["id"], f"Paperclip에 연결할 수 없습니다: {e}", pc)
        return run
    except contract.ContractError as e:
        return fail(run["id"], f"출력 검증 실패: {e}", pc)
    except PaperclipError as e:
        return fail(run["id"], f"Paperclip 오류: {e}", pc)
    except Exception as e:  # noqa: BLE001
        log.exception("run #%s advance 오류", run["id"])
        return fail(run["id"], f"Deep Review 진행 오류: {type(e).__name__}", pc)
    finally:
        if client is None:
            pc.close()


def _stage_elapsed(run: dict) -> float:
    started = run.get("stage_started_at") or run.get("created_at")
    try:
        return (datetime.now() - datetime.fromisoformat(started)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def _advance(run: dict, pc: PaperclipClient) -> dict | None:
    if run["status"] == "PENDING":
        if _stage_elapsed(run) > 60:  # kickoff가 끝나지 못함 (backend 재시작 등) → 다시 시도
            return kickoff(run["id"], pc)
        return run
    org = load_org()
    stage = _current_stage(run)
    if stage is None:
        return fail(run["id"], "진행 중인 ticket 정보가 없습니다", pc)
    role, iteration, issue_id = stage["role"], stage["iteration"], stage["issue_id"]
    issue = pc.get_issue(issue_id)
    status = issue.get("status")
    if status not in ("done", "cancelled", "blocked"):
        problem = _paperclip_run_problem(pc, org["company_id"], issue_id)
        if problem:
            return fail(run["id"], f"{contract.ROLE_LABELS[role]} 실행 실패 — {problem}", pc)
        if _stage_elapsed(run) > DEEP_REVIEW_STAGE_TIMEOUT_SECONDS:
            return fail(run["id"], f"{contract.ROLE_LABELS[role]} 시간 초과 ({DEEP_REVIEW_STAGE_TIMEOUT_SECONDS:g}s)", pc)
        return run

    envelope, comment = _find_output(pc, issue_id, org["agents"][role])
    if envelope is None:
        if status == "cancelled":
            return fail(run["id"], f"Paperclip ticket {stage.get('identifier')}가 취소되었습니다", pc)
        note = next((c.get("body") for c in reversed(pc.list_comments(issue_id)) if not c.get("authorAgentId")), "")
        reason = f" — Paperclip: {note.strip()[:240]}" if note else ""
        return fail(run["id"], f"{contract.ROLE_LABELS[role]} ticket이 결과 없이 종료됨 ({status}){reason}", pc)
    if not envelope.get("ok"):
        return fail(run["id"], f"{contract.ROLE_LABELS[role]} 실패: {envelope.get('error')}", pc)
    if envelope.get("role") != role or envelope.get("iteration") != iteration:
        raise contract.ContractError("output의 role / iteration이 ticket과 다름")

    # backend가 다시 검증한다 (worker 검증을 믿지 않는다)
    snap = run["input_snapshot"]
    if role == "researcher":
        payload = contract.validate_research(envelope["output"], snap, decision_exists=lambda d: db.get_decision(d) is not None)
    elif role == "analyst":
        payload = contract.validate_analysis(envelope["output"], _artifact_payload(run["id"], "researcher"))
    else:
        payload = contract.validate_critique(envelope["output"], _artifact_payload(run["id"], "researcher"),
                                             _artifact_payload(run["id"], "analyst", iteration))
    meta = {k: v for k, v in (envelope.get("meta") or {}).items() if k != "tool_audit"}
    if (envelope.get("meta") or {}).get("tool_audit"):
        meta["tool_audit"] = envelope["meta"]["tool_audit"]
    try:
        db.add_deep_review_artifact(run["id"], role, iteration, payload, issue_id=issue_id,
                                    issue_identifier=stage.get("identifier"),
                                    paperclip_run_id=(envelope.get("meta") or {}).get("paperclip_run_id"), meta=meta)
    except Exception:  # noqa: BLE001 — 같은 artifact가 이미 있으면 (중복 tick) 다음 진행만
        if _artifact_payload(run["id"], role, iteration) is None:
            raise
    return _next(run, org, pc, role, iteration, payload)


def _open_stage(run: dict, org: dict, pc: PaperclipClient, role: str, iteration: int, data: dict,
                status: str, **fields) -> dict | None:
    issue = _create_stage_issue(pc, org, run, role, iteration, data)
    paperclip = dict(run["paperclip"])
    paperclip["stages"] = [*paperclip.get("stages", []), _stage_ref(issue, role, iteration)]
    updated = db.update_deep_review_run(run["id"], expect_status=run["status"], status=status, paperclip=paperclip,
                                        stage_started_at=_now(), **fields)
    log.info("run #%s → %s (%s %s)", run["id"], status, contract.ROLE_LABELS[role], issue.get("identifier"))
    return updated


def _next(run: dict, org: dict, pc: PaperclipClient, role: str, iteration: int, payload: dict) -> dict | None:
    research = _artifact_payload(run["id"], "researcher")
    if role == "researcher":
        return _open_stage(run, org, pc, "analyst", 0, {"research": payload}, "ANALYZING")
    if role == "analyst":
        return _open_stage(run, org, pc, "critic", iteration, {"research": research, "analysis": payload}, "CRITIQUING")
    # critic
    if payload["verdict"] == "PASS":
        return finish(run, pc, "COMPLETED", "PASS")
    if run["revision_count"] < DEEP_REVIEW_MAX_REVISIONS:
        data = {"research": research, "previous_analysis": _artifact_payload(run["id"], "analyst", iteration),
                "critique": payload}
        return _open_stage(run, org, pc, "analyst", iteration + 1, data, "REVISING",
                           revision_count=run["revision_count"] + 1)
    return finish(run, pc, "HUMAN_REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED")


def finish(run: dict, pc: PaperclipClient, status: str, verdict: str) -> dict | None:
    done = db.update_deep_review_run(run["id"], expect_status=run["status"], status=status, final_verdict=verdict,
                                     completed_at=_now())
    parent = (run.get("paperclip") or {}).get("parent_issue_id")
    if parent:
        note = ("Critic PASS — 분석이 검증되었습니다." if verdict == "PASS"
                else f"Critic이 {DEEP_REVIEW_MAX_REVISIONS}회 수정 후에도 PASS하지 않았습니다.")
        try:  # 사람에게 넘긴다: in_review. Decision은 바뀌지 않는다
            pc.patch_issue(parent, {"status": "in_review",
                                    "comment": f"{note}\n\n최종 상태: HUMAN REVIEW — Decision Pulse에서 사람이 판단합니다. "
                                               "Decision은 자동으로 변경되지 않았습니다."})
        except PaperclipError as e:
            log.warning("run #%s parent ticket 갱신 실패 (결과는 저장됨): %s", run["id"], e)
    log.info("run #%s %s → %s (%s)", run["id"], run["decision_id"], status, verdict)
    return done


def fail(run_id: int, error: str, pc: PaperclipClient | None = None) -> dict | None:
    """FAILED 기록 + 남은 Paperclip 작업 취소 (best effort). 기존 기능에는 영향 없음."""
    run = db.update_deep_review_run(run_id, status="FAILED", error=error, completed_at=_now())
    log.warning("run #%s failed: %s", run_id, error)
    if run and pc is not None:
        stage = _current_stage(run)
        try:
            if stage:
                issue = pc.get_issue(stage["issue_id"])
                if issue.get("status") not in ("done", "cancelled"):
                    pc.patch_issue(stage["issue_id"], {"status": "cancelled"})
                company = (run.get("paperclip") or {}).get("company_id")
                if company:
                    for r in pc.list_runs(company, limit=200):
                        if (r.get("issueId") == stage["issue_id"]) and r.get("status") in LIVE_RUN_STATES:
                            pc.cancel_run(r["id"])
            parent = (run.get("paperclip") or {}).get("parent_issue_id")
            if parent:
                pc.patch_issue(parent, {"status": "cancelled", "comment": f"Deep Review FAILED: {error[:400]}\n\n재시도 가능. Decision은 바뀌지 않았습니다."})
        except PaperclipError:
            pass
    return run or db.get_deep_review_run(run_id)


async def run_forever():
    log.info("deep review monitor started (poll %ss, Paperclip %s)", DEEP_REVIEW_POLL_SECONDS, PAPERCLIP_API_URL)
    while True:
        try:
            runs = await asyncio.to_thread(db.list_active_deep_review_runs)
            for run in runs:
                await asyncio.to_thread(advance, run)
            STATUS["last_error"] = None
            STATUS["last_tick_at"] = _now()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 다음 주기에 다시
            STATUS["last_error"] = f"{type(e).__name__}: {e}"
            log.exception("monitor tick 실패: %s", e)
        await asyncio.sleep(DEEP_REVIEW_POLL_SECONDS)


# ---------------------------------------------------------------- 화면용 view


def stages_view(run: dict, artifacts: list[dict]) -> list[dict]:
    done = {(a["role"]): a["iteration"] for a in artifacts}
    current = _current_stage(run)
    out = []
    for key, label, role in STAGES:
        iteration = max([a["iteration"] for a in artifacts if a["role"] == role] or [0])
        if current and current["role"] == role and run["status"] in db.DEEP_REVIEW_ACTIVE:
            # 현재 ticket이 이 역할: 이전 iteration 결과가 있어도 지금은 running
            state, iteration = "running", current["iteration"]
        elif role in done:
            state = "complete"
        elif run["status"] == "FAILED" and current and current["role"] == role:
            state = "failed"
        else:
            state = "waiting"
        out.append({"key": key, "label": label, "state": state, "iteration": iteration})
    return out


def view(run: dict) -> dict:
    artifacts = db.list_deep_review_artifacts(run["id"])
    pc = run.get("paperclip") or {}
    url = None
    if PAPERCLIP_UI_URL and pc.get("parent_identifier"):
        prefix = pc["parent_identifier"].split("-")[0]
        url = f"{PAPERCLIP_UI_URL}/{prefix}/issues/{pc['parent_identifier']}"
    return {
        **{k: run[k] for k in ("id", "project_id", "decision_id", "review_event_id", "review_state_changed_at", "status",
                               "revision_count", "final_verdict", "error", "created_at", "started_at", "completed_at")},
        "paperclip": pc,
        "paperclip_url": url,
        "input_snapshot": run.get("input_snapshot"),
        "stages": stages_view(run, artifacts),
        "artifacts": artifacts,
    }


def elapsed(run: dict) -> float | None:
    try:
        start = datetime.fromisoformat(run["created_at"])
        end = datetime.fromisoformat(run["completed_at"]) if run.get("completed_at") else datetime.now()
    except (TypeError, ValueError, KeyError):
        return None
    return (end - start).total_seconds()

