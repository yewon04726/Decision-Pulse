"""테스트용 가짜 Hermes CLI (LLM/네트워크 없음). services/review_memo가 실제 Hermes와 같은 방식으로 다루는지 확인한다.

- argv: -z PROMPT -t TOOLSETS --ignore-rules --usage-file PATH (받은 argv는 FAKE_HERMES_ARGV_LOG에 기록)
- usage report(session_id 포함)를 쓰고, $HERMES_HOME/state.db의 messages에 tool 호출 기록을 남긴다 (Hermes와 같은 형태)
- sessions.tool_names → system_prompts에 제공한 tool 목록(tools 배열)을 남긴다 (Hermes와 같은 형태)
- FAKE_HERMES_MODE: ok | terminal | exposed_terminal | no_gbrain | fail | slow | wrong_detection | not_due | invented_premise
  | bad_evidence_id | quoted_evidence | timestamp_outside | dup_ids | tamper_facts | baseline_increase
- v4 prompt(AI Analysis)면 AI 섹션 + '원래 근거' ID만 쓴다. tamper_facts는 Authoritative 섹션을 바꿔 써 본다 (무시돼야 함)
- v3 prompt면 prompt의 Evidence 목록에서 ID를 골라 '원래 근거'에 ID만 쓴다 (실제 Hermes에 기대하는 출력 형태)
"""

import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.review_memo import (  # noqa: E402
    DETECTION_LINES, FINAL_STATUS_LINE, MEMO_HEADER, NO_ASSUMPTIONS_TEXT, REQUIRED_SECTIONS,
)

args = sys.argv[1:]
opt = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i] in ("-z", "-t", "--usage-file", "-m", "--provider")}
mode = os.environ.get("FAKE_HERMES_MODE", "ok")
if os.environ.get("FAKE_HERMES_ARGV_LOG"):
    Path(os.environ["FAKE_HERMES_ARGV_LOG"]).write_text(json.dumps(sys.argv[1:], ensure_ascii=False))

session = f"fake-{uuid.uuid4().hex[:8]}"
usage = {"session_id": session, "model": "z-ai/glm-5.3", "provider": "openrouter", "failed": False}


def tool_rows() -> list[tuple]:
    def call(name):
        return ("assistant", None, json.dumps([{"type": "function", "function": {
            "name": "tool_call", "arguments": json.dumps({"calls": [{"name": name, "arguments": {}}]})}}]))

    rows = [("assistant", None, json.dumps([{"type": "function", "function": {"name": "tool_describe", "arguments": "{}"}}])),
            ("tool", "tool_describe", None)]
    names = ["mcp__decision_debt__list_review_due", "mcp__decision_debt__get_decision_detail", "mcp__gbrain__search"]
    if mode == "no_gbrain":
        names.remove("mcp__gbrain__search")
    for n in names:
        rows += [call(n), ("tool", n, None)]
    if mode == "terminal":
        rows += [("assistant", None, json.dumps([{"type": "function", "function": {"name": "terminal", "arguments": "{}"}}])),
                 ("tool", "terminal", None)]
    return rows


def offered_tools() -> str:
    catalog = ("Deferred tool catalog:\ndecision-debt tools (3):\n- mcp__decision_debt__list_review_due: ...\n"
               "- mcp__decision_debt__get_decision_detail: ...\n- mcp__decision_debt__read_resource: ...\n"
               "gbrain tools (2):\n- mcp__gbrain__search: ...\n- mcp__gbrain__query: ...")
    names = ["tool_search", "tool_describe", "tool_call"] + (["terminal"] if mode == "exposed_terminal" else [])
    tools = [{"type": "function", "function": {"name": n, "description": catalog if n == "tool_search" else ""}} for n in names]
    return json.dumps({"version": "0.23.0", "tools": tools})


def record(rows):
    path = Path(os.environ["HERMES_HOME"]) / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
                 "content TEXT, tool_name TEXT, tool_calls TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, tool_names TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS system_prompts (hash TEXT PRIMARY KEY, prompt TEXT NOT NULL)")
    tools_hash = f"h-{session}"
    conn.execute("INSERT INTO system_prompts (hash, prompt) VALUES (?, ?)", (tools_hash, offered_tools()))
    conn.execute("INSERT INTO sessions (id, tool_names) VALUES (?, ?)", (session, tools_hash))
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)", (session, opt.get("-z", "")))
    conn.executemany("INSERT INTO messages (session_id, role, tool_name, tool_calls) VALUES (?, ?, ?, ?)",
                     [(session, *r) for r in rows])
    conn.commit()
    conn.close()


def memo_v4(prompt: str) -> str:
    ids = re.findall(r"^- (E\d+) \[", prompt, re.M)
    body = {
        "## 원래 근거": "\n".join(f"- {i}" for i in ids[:2]) or "없음",
        "## Evidence에서 확인되는 추가 맥락": "- 기능 개발을 운영 작업보다 우선한다는 발언이 있었다 (E2).",
        "## 관련 과거 지식": "", "### 직접 관련": "- 없음", "### 간접적으로 유사": "- 없음",
        "## AI 검토 의견": "", "### 현재도 확인되는 사실": "- Authoritative Facts의 현재 관측값 참조: 조건 충족.",
        "### 달라진 점": "- 재검토 조건 충족 (Authoritative Facts 참조).",
        "### 아직 확인되지 않은 정보": "- 동시 요청량은 기록에 없다.",
        "### 사람이 추가로 확인할 사항": "- 전환 여부는 사람이 판단한다.",
    }
    if mode == "bad_evidence_id":
        body["## 원래 근거"] += "\n- E99"
    if mode == "dup_ids" and len(ids) >= 2:
        body["## 원래 근거"] = f"- {ids[1]}\n- {ids[1]}, {ids[0]}"
    if mode == "quoted_evidence":
        body["## 원래 근거"] = "- [00:01:00] 지금 굳이 프그레스 SQL로 옮기지는 않는 걸로요."
    if mode == "timestamp_outside":
        body["### 현재도 확인되는 사실"] = "- [00:00:52] 지금은 운영 작업보다 기능 만드는 쪽이 더 중요하다고 봐요."
    if mode == "invented_premise":
        body["### 달라진 점"] = '- 당시 전제였던 "사용자가 아직 적은 MVP 단계"는 관측값 기준으로 더 이상 성립하지 않는다.'
    if mode == "baseline_increase":
        body["### 달라진 점"] = "- 사용자가 1000 이하에서 1400으로 증가했다."
    out = "\n\n".join(f"{k}\n{v}".rstrip() for k, v in body.items())
    if mode == "tamper_facts":  # 모델이 저장된 사실을 바꿔 쓴 경우 (최종 memo에 들어가면 안 됨)
        out = ("## 당시 결정\n지금 굳이 프그레스 SQL로 옮기지는 않는 걸로요.\n\n## 저장된 Rationale\n사용자가 적었기 때문\n\n"
               "## 현재 관측값\n- user_count = 9999\n\n" + out + "\n\n## 최종 상태\nACTIVE")
    return "메모입니다.\n\n" + out


def memo(detected_by: str) -> str:
    if "AI Analysis" in opt.get("-z", ""):
        return memo_v4(opt["-z"])
    body = {s: "- (내용)" for s in REQUIRED_SECTIONS}
    body["## 조건 충족 판단"] = DETECTION_LINES[detected_by] + "\nuser_count 1400 > 1000"
    body["## 당시 전제"] = NO_ASSUMPTIONS_TEXT
    body["## 최종 상태"] = FINAL_STATUS_LINE
    prompt = opt.get("-z", "")
    ids = re.findall(r"^- (E\d+) \[", prompt, re.M)
    if "Evidence 목록" in prompt:  # v3: 원래 근거에는 ID만
        body["## 원래 근거"] = "\n".join(f"- {i}" for i in ids[:2]) or "없음"
        if mode == "bad_evidence_id":
            body["## 원래 근거"] += "\n- E99"
        if mode == "dup_ids" and len(ids) >= 2:
            body["## 원래 근거"] = f"- {ids[1]}\n- {ids[1]}, {ids[0]}"
        if mode == "quoted_evidence":  # v2 memo #2에서 실제로 나온 변형 인용
            body["## 원래 근거"] = "- [00:01:00] 지금 굳이 프그레스 SQL로 옮기지는 않는 걸로요."
        if mode == "timestamp_outside":
            body["### 현재도 확인되는 사실"] = "- [00:00:52] 지금은 운영 작업보다 기능 만드는 쪽이 더 중요하다고 봐요."
        body["## 재검토 조건"] = "- user_count > 1000"
    if mode == "invented_premise":  # v1 memo #1에서 실제로 나온 문장
        body["### 달라진 점"] = '- 당시 전제였던 "사용자가 아직 적은 MVP 단계"는 관측값 기준으로 더 이상 성립하지 않는다.'
    return "메모입니다.\n\n" + MEMO_HEADER + "\n\n" + "\n\n".join(f"{k}\n{v}" for k, v in body.items())


def write_usage():
    Path(opt["--usage-file"]).write_text(json.dumps(usage))


if mode == "slow":
    time.sleep(30)
if mode == "fail":
    usage.update(failed=True, failure="OpenRouter HTTP 502 (key sk-abcdefghijklmnopqrstuvwxyz)")
    record([])
    write_usage()
    print("Traceback (most recent call last):\n  ...\nRuntimeError: OpenRouter HTTP 502", file=sys.stderr)
    sys.exit(1)

record(tool_rows())
write_usage()
detected = os.environ.get("FAKE_DETECTED_BY", "review_state_baseline")
if mode == "wrong_detection":
    detected = "review_event" if detected == "review_state_baseline" else "review_state_baseline"
print("NOT_REVIEW_DUE" if mode == "not_due" else memo(detected))
