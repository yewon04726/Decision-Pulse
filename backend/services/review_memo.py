"""Decision Review Memo: REVIEW_DUE 결정에 대해 Hermes가 검토 메모를 만든다 (advisory 산출물).

흐름: POST → 현재 REVIEW_DUE 확인 → generating 행 → (background) Hermes -z → completed | failed

원칙
- Decision / decision_review_state / review_events / candidates는 바꾸지 않는다. memo 테이블에만 쓴다.
- Hermes는 `-t decision-debt,gbrain`으로 두 MCP toolset만 받는다 (terminal/file/browser/memory 없음).
  Decision Debt MCP는 SQLite read-only, gbrain MCP는 `gbrain serve --access read-only` (Hermes 설정)로 쓰기가 막혀 있다.
- gbrain은 Hermes gateway가 띄운 MCP를 그대로 쓴다. 여기서 gbrain serve/import/embed나 gbrain DB를 직접 열지 않는다.
- 실행 후 Hermes session 기록(state.db, 읽기 전용)에서 실제 호출된 tool을 확인한다.
  허용 외 tool이 하나라도 있거나 필수 tool(list_review_due, get_decision_detail, gbrain 조회)이 빠지면 failed.
- 실패해도 기존 memo는 지우지 않는다. 재생성은 새 행.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

import db
from config import (
    BACKEND_ENV_KEYS,
    HERMES_BIN,
    HERMES_HOME,
    HERMES_REVIEW_MEMO_ENABLED,
    HERMES_REVIEW_MEMO_MODEL,
    HERMES_REVIEW_MEMO_PROVIDER,
    HERMES_REVIEW_MEMO_REASONING,
    HERMES_REVIEW_MEMO_TIMEOUT_SECONDS,
)
from schemas import ReviewMemo
from services import decision_read
from services.review_trigger import format_review_trigger_v2

log = logging.getLogger("review_memo")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [review_memo] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

ENABLED = HERMES_REVIEW_MEMO_ENABLED
PROMPT_VERSION = "v4"

# Hermes -t 값 (Hermes config의 MCP server 이름). 이 두 toolset 외에는 주지 않는다
TOOLSETS = ("decision-debt", "gbrain")
# Hermes가 deferred tool을 다루는 bridge (tool_call은 session에 열린 tool만 실행할 수 있다)
BRIDGE_TOOLS = frozenset({"tool_search", "tool_describe", "tool_call"})
DECISION_DEBT_TOOLS = frozenset({"mcp__decision_debt__list_review_due", "mcp__decision_debt__get_decision_detail"})
GBRAIN_PREFIX = "mcp__gbrain__"  # gbrain serve --access read-only가 노출하는 조회 tool만 존재
REQUIRED_TOOLS = ("mcp__decision_debt__list_review_due", "mcp__decision_debt__get_decision_detail")

MEMO_HEADER = "# Decision Review Memo"
FINAL_STATUS_LINE = "REVIEW_DUE — 사람의 재검토 필요"
DETECTION_LINES = {
    "review_event": "감지 방식: 상태 전환 — 이전에 ACTIVE였으나 조건 충족 후 REVIEW_DUE로 전환됨",
    "review_state_baseline": "감지 방식: baseline — 첫 모니터링 시점부터 조건이 이미 충족된 상태였음",
}
REQUIRED_SECTIONS = (
    "## 대상 Decision", "## 당시 결정", "## 저장된 Rationale", "## Evidence에서 확인되는 추가 맥락", "## 당시 전제",
    "## 재검토 조건", "## 현재 관측값", "## 조건 충족 판단", "## 원래 근거", "## 관련 과거 지식", "### 직접 관련",
    "### 간접적으로 유사", "## AI 검토 의견", "### 현재도 확인되는 사실", "### 달라진 점", "### 아직 확인되지 않은 정보",
    "### 사람이 추가로 확인할 사항", "## 최종 상태",
)

PROMPT_V1 = """{decision_id}에 대한 Decision Review Memo를 작성해줘.

너는 조회 전용 검토 보조자다. Decision Debt MCP(list_review_due, get_decision_detail)와 gbrain MCP(검색/조회)만 사용한다.
Memo는 참고용(advisory)이며 최종 판단은 사람이 한다.

절차 (반드시 이 순서로 tool을 호출한다):
1. list_review_due를 호출해 {decision_id}가 현재 REVIEW_DUE인지 확인한다. 목록에 없으면 memo를 쓰지 말고 "NOT_REVIEW_DUE"라고만 답한다.
2. get_decision_detail(decision_id="{decision_id}")로 decision, rationale, assumptions, review trigger, 현재 관측값(review_state), sources, evidence를 조회한다.
3. gbrain에서 이 Decision과 직접 관련된 과거 결정/근거를 검색한다 (예: 같은 topic, 같은 기술 선택, 같은 Decision ID).
4. 아래 형식으로 memo를 작성한다.

작성 규칙:
- tool 결과에 있는 사실만 쓴다. 없는 metric, 없는 관측값, 없는 전제를 추측하지 마라.
- "저장된 Rationale"에는 get_decision_detail의 rationale 필드 내용만 쓴다.
  evidence 발언에서만 확인되는 이유·맥락은 rationale처럼 쓰지 말고 "Evidence에서 확인되는 추가 맥락"에 따로 쓴다.
- assumptions가 비어 있으면 "당시 전제"에 "명시적으로 기록된 전제 없음"이라고 쓴다.
- "조건 충족 판단"의 첫 줄에는 list_review_due 항목의 detected_by 값에 따라 아래 문장 중 하나를 그대로 쓴다.
  - detected_by = review_event          → "{line_event}"
  - detected_by = review_state_baseline → "{line_baseline}"
  baseline인 경우 "최근에 조건을 새로 넘었다"거나 "사용자가 최근 증가했다"고 쓰지 마라. 언제 넘었는지는 기록에 없다.
- 현재 관측값은 review monitor가 저장한 마지막 값이다. 값, 단위, source, observed_at을 그대로 쓴다.
- "관련 과거 지식 / 직접 관련"에는 이 Decision의 판단에 직접 관련된 gbrain 기록만 쓴다.
  재검토 패턴만 비슷한 다른 Decision(예: "규모가 커지면 재검토")은 직접 근거로 쓰지 말고 "간접적으로 유사"에 따로 쓴다. 없으면 "없음".

하지 말 것:
- Decision 수정, 새 Decision 생성, Candidate 생성·승인, Review Event acknowledge를 하지 마라.
- 기술 전환(예: PostgreSQL 전환)을 결정하지 마라. 선택지는 사람이 판단한다.
- gbrain에 쓰지 마라.

형식:

{header}

## 대상 Decision
- Decision ID:
- Project:
- Topic:
- 현재 상태:

## 당시 결정

## 저장된 Rationale

## Evidence에서 확인되는 추가 맥락

## 당시 전제

## 재검토 조건

## 현재 관측값

## 조건 충족 판단

## 원래 근거
Decision Debt의 source/evidence 내용만 쓴다 (source 제목, 유형, 인용).

## 관련 과거 지식
### 직접 관련

### 간접적으로 유사

## AI 검토 의견
### 현재도 확인되는 사실

### 달라진 점

### 아직 확인되지 않은 정보

### 사람이 추가로 확인할 사항

## 최종 상태
{final_status}

memo 본문만 출력한다 (앞뒤 설명 없이 "{header}"로 시작).
"""


# v2: 기록에 없는 전제를 만들지 않게 하는 규칙 (v1 memo #1이 assumptions=[]인데 "당시 전제였던 '사용자가 아직 적은 MVP 단계'"를 씀)
_V1_ASSUMPTION_RULE = '- assumptions가 비어 있으면 "당시 전제"에 "명시적으로 기록된 전제 없음"이라고 쓴다.\n'
_V2_FACT_RULES = """- 전제(assumptions) 규칙:
  - assumptions가 비어 있으면 "당시 전제"에 "명시적으로 기록된 전제 없음"이라고 쓴다.
  - assumptions나 evidence에 명시되지 않은 내용을 전제로 만들지 마라. "당시 전제", "현재도 확인되는 사실", "달라진 점",
    "AI 검토 의견", "사람이 추가로 확인할 사항" 모두에 적용된다.
  - 예: "사용자가 적었다", "MVP 초기였다", "부하가 낮았다", "운영 규모가 작았다"는 assumptions나 evidence에 명시되어 있지 않으면
    당시 전제처럼 쓰지 마라. "당시 전제였던 ~", "~라는 전제가 더 이상 성립하지 않는다" 같은 문장도 쓰지 마라.
- evidence 해석 규칙: 발언은 발언으로만 옮긴다.
  - 가능: "지금은 운영 작업보다 기능 만드는 쪽이 더 중요" → "기능 개발을 운영 작업보다 우선한다는 발언이 있었다."
  - 금지: "당시 사용자가 적어서 운영 작업이 필요 없었다"처럼 발언에 없는 인과관계를 새로 만드는 것.
- "달라진 점"에는 관측 가능한 변화만 쓴다. 예: "재검토 threshold 1000, 현재 user_count 1400 → 조건 충족".
  - detected_by = review_state_baseline이면 이전 값과 threshold 통과 시점이 기록에 없다. "1000 이하에서 1400으로 증가했다"처럼
    증가·변화를 단정하지 말고 이렇게 쓴다: "첫 모니터링 시점부터 <metric>=<현재값>으로 threshold를 초과한 상태가 확인되었다.
    그 이전 값이나 threshold 통과 시점은 기록되어 있지 않다."
"""
assert _V1_ASSUMPTION_RULE in PROMPT_V1
PROMPT_V2 = PROMPT_V1.replace(_V1_ASSUMPTION_RULE, _V2_FACT_RULES)

# v3: Evidence 원문을 LLM이 다시 쓰지 못하게 한다 (v2 memo #2가 "프로그레스 SQL"을 "프그레스 SQL"로 바꿔 인용).
# Hermes는 Evidence ID(E1, E2 …)만 고르고, '원래 근거' 문장과 재검토 조건 원문은 backend가 snapshot 원문으로 채운다.
_V2_SOURCE_SECTION = "## 원래 근거\nDecision Debt의 source/evidence 내용만 쓴다 (source 제목, 유형, 인용).\n"
_V3_SOURCE_SECTION = "## 원래 근거\n아래 Evidence 목록의 ID만 한 줄에 하나씩 쓴다 (예: - E1). 문장은 backend가 기록 원문으로 채운다.\n"
EVIDENCE_LIST_MARK = "<<EVIDENCE_LIST>>"  # format() 이후에 채운다 (원문에 중괄호가 있어도 안전)
_V3_EVIDENCE_RULES = """- Evidence 인용 규칙:
  - Evidence 문장을 다시 작성하거나 요약하거나 교정하지 마라. 원문 문장을 memo 어디에도 옮겨 쓰지 마라.
  - "원래 근거" 섹션에는 아래 Evidence 목록의 ID(E1, E2 …)만 쓴다. ID 외의 글자는 쓰지 마라.
  - 다른 섹션에서 특정 발언을 가리킬 때는 원문 대신 ID로 참조한다. 예: "기능 개발을 운영 작업보다 우선한다는 발언이 있었다 (E4)"
  - [hh:mm:ss] 타임스탬프가 붙은 원문 줄을 쓰지 마라.
- "재검토 조건" 섹션에는 조건식만 쓴다 (예: user_count > 1000). 원문 문장은 backend가 기록 그대로 덧붙인다.

Evidence 목록 (backend snapshot. ID로만 참조한다):
""" + EVIDENCE_LIST_MARK + """

하지 말 것:
"""
assert _V2_SOURCE_SECTION in PROMPT_V2 and PROMPT_V2.count("\n하지 말 것:\n") == 1
PROMPT_V3 = PROMPT_V2.replace(_V2_SOURCE_SECTION, _V3_SOURCE_SECTION).replace("\n하지 말 것:\n", "\n" + _V3_EVIDENCE_RULES)
# v4: memo를 Authoritative Facts(backend가 기록 그대로 렌더링)와 AI Analysis(Hermes)로 나눈다.
# Hermes는 저장된 사실(결정·rationale·전제·조건 원문·관측값·evidence 원문)을 다시 쓰지 않고 해석만 쓴다.
FACTS_MARK = "<<FACTS>>"
AI_SECTIONS = (
    "## Evidence에서 확인되는 추가 맥락", "## 관련 과거 지식", "### 직접 관련", "### 간접적으로 유사", "## AI 검토 의견",
    "### 현재도 확인되는 사실", "### 달라진 점", "### 아직 확인되지 않은 정보", "### 사람이 추가로 확인할 사항",
)
PROMPT_V4 = """{decision_id}에 대한 Decision Review Memo의 AI Analysis 부분만 작성해줘.

너는 조회 전용 검토 보조자다. Decision Debt MCP(list_review_due, get_decision_detail)와 gbrain MCP(검색/조회)만 사용한다.
Memo는 참고용(advisory)이며 최종 판단은 사람이 한다.

Memo는 두 부분으로 나뉜다.
- Authoritative Facts: 결정, rationale, 전제, 재검토 조건, 관측값, 감지 방식, evidence 원문. backend가 Decision Debt 기록을 그대로 표시한다 (아래에 미리 보여 준다). 너는 이 부분을 쓰지 않는다.
- AI Analysis: 너는 아래 출력 형식의 섹션만 쓴다.

절차 (반드시 이 순서로 tool을 호출한다):
1. list_review_due를 호출해 {decision_id}가 현재 REVIEW_DUE인지 확인한다. 목록에 없으면 memo를 쓰지 말고 "NOT_REVIEW_DUE"라고만 답한다.
2. get_decision_detail(decision_id="{decision_id}")로 기록을 확인한다.
3. gbrain에서 이 Decision과 직접 관련된 과거 결정/근거를 검색한다 (예: 같은 topic, 같은 기술 선택, 같은 Decision ID).
4. 아래 출력 형식으로 AI Analysis만 쓴다.

Authoritative Facts (backend가 memo에 그대로 표시한다. 다시 쓰지 마라):
""" + FACTS_MARK + """

Evidence 목록 (backend snapshot. ID로만 참조한다):
""" + EVIDENCE_LIST_MARK + """

작성 규칙:
- Authoritative Facts의 값(결정 문장, rationale, 전제, 조건 원문, 관측값 숫자, evidence 원문)을 다시 옮겨 쓰거나 요약·교정하지 마라.
  필요하면 "Authoritative Facts의 현재 관측값 참조", "(E4)"처럼 참조만 한다.
- [hh:mm:ss] 타임스탬프가 붙은 원문 줄을 쓰지 마라.
- "원래 근거"에는 Evidence 목록의 ID만 한 줄에 하나씩 쓴다 (예: - E1). ID 외의 글자는 쓰지 마라. evidence가 없으면 "없음".
- 전제 규칙: assumptions에 없는 전제를 만들지 마라. "사용자가 적었다", "MVP 초기였다", "부하가 낮았다", "운영 규모가 작았다" 같은 내용이
  기록에 명시되어 있지 않으면 당시 전제처럼 쓰지 마라. "당시 전제였던 ~", "~라는 전제가 더 이상 성립하지 않는다" 같은 문장도 쓰지 마라.
- evidence 해석: 발언은 발언으로만 다룬다. 가능: "기능 개발을 운영 작업보다 우선한다는 발언이 있었다 (E4)".
  금지: "당시 사용자가 적어서 운영 작업이 필요 없었다"처럼 발언에 없는 인과관계를 새로 만드는 것.
- "달라진 점"에는 관측 가능한 변화만 쓴다. 감지 방식이 baseline이면 이전 값과 threshold 통과 시점이 기록에 없으므로
  "증가했다", "늘어났다"처럼 변화를 단정하지 마라.
- 없는 metric, 없는 관측값을 추측하지 마라.
- "관련 과거 지식 / 직접 관련"에는 이 Decision의 판단에 직접 관련된 gbrain 기록만 쓴다.
  재검토 패턴만 비슷한 다른 Decision(예: "규모가 커지면 재검토")은 직접 근거로 쓰지 말고 "간접적으로 유사"에 따로 쓴다. 없으면 "없음".

하지 말 것:
- Decision 수정, 새 Decision 생성, Candidate 생성·승인, Review Event acknowledge를 하지 마라.
- 기술 전환(예: PostgreSQL 전환)을 결정하지 마라. 선택지는 사람이 판단한다.
- gbrain에 쓰지 마라.

출력 형식 (이 섹션들만, 이 순서로. 앞뒤 설명 없이 "## 원래 근거"로 시작):

## 원래 근거

## Evidence에서 확인되는 추가 맥락

## 관련 과거 지식
### 직접 관련

### 간접적으로 유사

## AI 검토 의견
### 현재도 확인되는 사실

### 달라진 점

### 아직 확인되지 않은 정보

### 사람이 추가로 확인할 사항
"""
PROMPTS = {"v1": PROMPT_V1, "v2": PROMPT_V2, "v3": PROMPT_V3, "v4": PROMPT_V4}

NO_RATIONALE_TEXT = "기록된 Rationale 없음"
DETECTION_TEXT_V4 = {
    "review_state_baseline": "첫 모니터링 시점부터 조건이 이미 충족된 상태였음.\n그 이전 값이나 threshold 통과 시점은 기록되어 있지 않음.",
    "review_event": "이전에 ACTIVE였으나 조건 충족 후 REVIEW_DUE로 전환됨.",
}
FACTS_NOTE = "> Authoritative Facts — Decision Debt 기록을 backend가 그대로 표시합니다 (AI가 작성하지 않음)."
ANALYSIS_NOTE = "> AI Analysis — 아래는 Hermes(AI)가 작성한 해석과 재검토 지원 내용입니다 (참고용)."


def _observed_lines(observed) -> list[str]:
    items = observed if isinstance(observed, list) else [observed] if observed else []
    lines = []
    for o in items:
        unit = f" {o['unit']}" if o.get("unit") else ""
        lines.append(f"- {o.get('metric')} = {o.get('value')}{unit}")
        if o.get("source"):
            lines.append(f"- source: {o['source']}")
        if o.get("observed_at"):
            lines.append(f"- observed_at: {o['observed_at']}")
    return lines or ["관측값 기록 없음"]


def render_facts(observed_snapshot: dict, source_snapshot: dict) -> str:
    """생성 시점 snapshot → Authoritative Facts Markdown. 저장값을 그대로 쓴다 (요약·교정 없음). LLM 출력은 쓰지 않는다."""
    rec = source_snapshot.get("decision_record") or {}
    obs = observed_snapshot or {}
    trigger = obs.get("trigger")
    raw = (trigger or {}).get("raw_condition") or (trigger or {}).get("description")
    state = obs.get("review_state") or {}
    assumptions = rec.get("assumptions") or []
    detection = DETECTION_TEXT_V4.get(obs.get("detected_by"), str(obs.get("detected_by")))
    event = obs.get("event")
    when = (f"REVIEW_DUE 전환 이벤트 #{event['event_id']} · 감지 {event['detected_at']}" if event
            else f"REVIEW_DUE 확정 시각(첫 모니터링): {state.get('last_changed_at') or obs.get('detected_at')}")
    sections = [
        ("## 대상 Decision", [f"- Decision ID: {rec.get('decision_id')}", f"- 제목: {rec.get('title')}",
                             f"- Project: {rec.get('project')}", f"- Topic: {rec.get('topic')}",
                             f"- 상태: {rec.get('status')} · human_verified={str(rec.get('human_verified')).lower()}",
                             f"- 재검토 상태: {obs.get('review_status')}"]),
        ("## 당시 결정", [rec.get("decision") or ""]),
        ("## 저장된 Rationale", [rec.get("rationale") or NO_RATIONALE_TEXT]),
        ("## 당시 전제", [f"- {a}" for a in assumptions] or [NO_ASSUMPTIONS_TEXT]),
        ("## 재검토 조건", [f"- 조건: {format_review_trigger_v2(trigger)}"]
         + ([f"- 원문 (기록 그대로): {raw}"] if raw else [])),
        ("## 현재 관측값", _observed_lines(obs.get("observed"))
         + ([f"- 마지막 확인: {obs['last_checked_at']}"] if obs.get("last_checked_at") else [])),
        ("## 감지 방식", [detection, "", when]),
    ]
    return "\n\n".join(h + "\n" + "\n".join(body) for h, body in sections)


def assemble_v4(hermes_text: str, observed_snapshot: dict, source_snapshot: dict) -> tuple[str | None, list[str]]:
    """최종 memo = backend facts + 원래 근거(evidence snapshot 원문) + Hermes AI 섹션 + 최종 상태.
    Hermes 출력에서는 '원래 근거'의 ID와 AI 섹션만 가져온다 (Authoritative 섹션을 썼더라도 버린다)."""
    text = (hermes_text or "").strip()
    if text.startswith("NOT_REVIEW_DUE"):
        return None, ["Hermes가 대상 결정을 REVIEW_DUE로 확인하지 못함 (NOT_REVIEW_DUE)"]
    problems = [f"섹션 누락: {h}" for h in ("## 원래 근거", *AI_SECTIONS) if not re.search(rf"^{re.escape(h)}\s*$", text, re.M)]
    if problems:
        return None, problems
    items = source_snapshot.get("evidence_items") or []
    evidence_part = "## 원래 근거\n" + _section(text, "## 원래 근거")
    ai_part = "\n\n".join(h + "\n" + _section(text, h).strip() for h in ("## Evidence에서 확인되는 추가 맥락",
                                                                       "## 관련 과거 지식", "## AI 검토 의견"))
    # 원래 근거: ID → snapshot 원문. 타임스탬프 원문은 AI 섹션에도 있으면 안 된다
    rendered, problems = render_evidence(evidence_part + "\n\n" + ai_part, items)
    evidence_body = _section(rendered, "## 원래 근거").strip()
    memo = "\n\n".join([
        MEMO_HEADER, FACTS_NOTE, render_facts(observed_snapshot, source_snapshot),
        "## 원래 근거\n" + evidence_body, ANALYSIS_NOTE, ai_part, "## 최종 상태\n" + FINAL_STATUS_LINE,
    ]) + "\n"
    problems += check_facts(memo, (observed_snapshot or {}).get("detected_by"), source_snapshot.get("assumptions"))
    return memo, problems


def _evidence_list(items: list[dict]) -> str:
    if not items:
        return "(evidence 없음 — '원래 근거'에는 '없음'이라고 쓴다)"
    return "\n".join(f"- {i['id']} [{i.get('source_title') or i['source_id']}] {i['text']}" for i in items)


def build_prompt(decision_id: str, version: str = PROMPT_VERSION, evidence_items: list[dict] | None = None,
                 facts: str = "") -> str:
    return PROMPTS[version].format(
        decision_id=decision_id,
        line_event=DETECTION_LINES["review_event"],
        line_baseline=DETECTION_LINES["review_state_baseline"],
        header=MEMO_HEADER,
        final_status=FINAL_STATUS_LINE,
    ).replace(EVIDENCE_LIST_MARK, _evidence_list(evidence_items or [])).replace(FACTS_MARK, facts)


def build_argv(prompt: str, usage_file: str) -> list[str]:
    """Hermes oneshot 명령 (shell 없이 argv로 실행). toolset은 두 MCP만. rules/memory 주입 없음."""
    argv = [HERMES_BIN, "-z", prompt, "-t", ",".join(TOOLSETS), "--ignore-rules", "--usage-file", usage_file]
    if HERMES_REVIEW_MEMO_MODEL:
        argv += ["-m", HERMES_REVIEW_MEMO_MODEL]
    if HERMES_REVIEW_MEMO_PROVIDER:
        argv += ["--provider", HERMES_REVIEW_MEMO_PROVIDER]
    if HERMES_REVIEW_MEMO_REASONING:
        argv += ["--reasoning", HERMES_REVIEW_MEMO_REASONING]
    return argv


def _hermes_env() -> dict[str, str]:
    # backend/.env 값(API Key 등)은 넘기지 않는다. Hermes는 자기 설정/자격 증명을 쓴다
    env = {k: v for k, v in os.environ.items() if k not in BACKEND_ENV_KEYS}
    env["HERMES_HOME"] = str(HERMES_HOME)
    return env


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+")


def _summarize_error(text: str, limit: int = 400) -> str:
    """오류 요약 (마지막 몇 줄, 길이 제한, key 형태 문자열 가림). traceback 전체는 저장하지 않는다."""
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    summary = " | ".join(lines[-3:])[-limit:]
    return _SECRET_RE.sub(r"\1***", summary)


# ---------------------------------------------------------------- 대상 확인


class ReviewMemoError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": str(self), **self.extra}


def review_context(decision_id: str) -> tuple[dict, dict]:
    """(list_review_due 항목, get_decision_detail) — MCP와 같은 read service로 현재 상태를 본다"""
    decision = db.get_decision(decision_id)
    if decision is None:
        raise ReviewMemoError(404, "decision_not_found", f"Decision not found: {decision_id}")
    due = decision_read.list_review_due(decision.project_id)
    item = next((i for i in due.get("items", []) if i["decision_id"] == decision_id), None)
    detail = decision_read.get_decision_detail(decision_id)
    if item is None:
        state = detail.get("review_state") or {}
        raise ReviewMemoError(
            409, "not_review_due", "현재 REVIEW_DUE인 결정이 아니어서 Review Memo를 만들지 않습니다.",
            review_status=state.get("last_review_status"), decision_status=decision.status,
        )
    return item, detail


def _snapshots(item: dict, detail: dict) -> tuple[dict, dict]:
    observed = {k: item.get(k) for k in (
        "decision_id", "review_status", "trigger", "trigger_text", "observed", "last_check_status",
        "last_checked_at", "detected_at", "detected_by", "event")}
    observed["review_state"] = detail.get("review_state")
    # assumptions도 함께 남긴다 (생성 후 사실성 검사: 기록에 없는 전제를 memo가 만들었는지)
    sources = {"sources": detail.get("sources", []), "evidence": detail.get("evidence", []),
               "assumptions": detail.get("assumptions", []),
               "evidence_items": evidence_items(detail["decision_id"], detail.get("sources", [])),
               # v4: backend가 그대로 렌더링할 저장값 (decision_read = MCP와 같은 read service)
               "decision_record": {k: detail.get(k) for k in (
                   "decision_id", "title", "project", "topic", "decision", "rationale", "assumptions", "status",
                   "human_verified")}}
    return observed, sources


def evidence_items(decision_id: str, sources: list[dict]) -> list[dict]:
    """저장된 evidence 원문(잘리지 않은 전체)을 줄 단위로 나눠 E1, E2 … ID를 붙인다.
    text는 decision_sources.evidence_text의 정확한 부분 문자열이다 (앞뒤 공백만 제외, 내용은 바꾸지 않는다)."""
    decision = db.get_decision(decision_id)
    titles = {s["source_id"]: s.get("title") for s in sources}
    items = []
    for e in decision.evidence if decision else []:
        for line in e.quote.splitlines():
            text = line.strip()
            if text:
                items.append({"id": f"E{len(items) + 1}", "source_id": e.source_id,
                              "source_title": titles.get(e.source_id), "text": text})
    return items


def start(decision_id: str, regenerate: bool = False) -> tuple[ReviewMemo, bool]:
    """(memo, 새로 생성 시작했는지). 같은 review 상태의 완료 memo가 있고 regenerate가 아니면 그것을 돌려준다."""
    if not ENABLED:
        raise ReviewMemoError(503, "review_memo_disabled", "Review Memo가 꺼져 있습니다 (HERMES_REVIEW_MEMO_ENABLED=false)")
    item, detail = review_context(decision_id)
    event_id = item["event"]["event_id"] if item.get("event") else None
    changed_at = (detail.get("review_state") or {}).get("last_changed_at")
    existing = db.list_review_memos(decision_id)
    generating = next((m for m in existing if m.status == "generating"), None)
    if generating:
        raise ReviewMemoError(409, "memo_generating", "이 결정의 Review Memo를 이미 생성 중입니다.", memo_id=generating.id)
    if not regenerate:
        same = next((m for m in existing if m.status == "completed" and m.review_event_id == event_id
                     and m.review_state_changed_at == changed_at), None)
        if same:
            return same, False
    observed, sources = _snapshots(item, detail)
    try:
        memo = db.create_review_memo(
            detail["project_id"], decision_id, event_id, changed_at, item["detected_by"], PROMPT_VERSION, observed, sources
        )
    except db.MemoInProgress:  # 동시에 들어온 요청
        raise ReviewMemoError(409, "memo_generating", "이 결정의 Review Memo를 이미 생성 중입니다.")
    return memo, True


# ---------------------------------------------------------------- Hermes 실행 + 검증


def _offered_tools(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Hermes가 이 session에서 모델에 제공한 tool 목록 (sessions.tool_names → system_prompts에 저장된 tools 배열).
    top-level: 모델이 바로 부를 수 있는 tool / deferred: tool_search 목록에 실린 tool. 기록이 없으면 None."""
    try:
        row = conn.execute(
            "SELECT p.prompt FROM sessions s JOIN system_prompts p ON p.hash = s.tool_names WHERE s.id = ?", (session_id,)
        ).fetchone()
        tools = json.loads(row[0])["tools"] if row else None
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return None
    if not isinstance(tools, list):
        return None
    top = [((t or {}).get("function") or {}).get("name") for t in tools]
    search = next((((t or {}).get("function") or {}).get("description") or "" for t in tools
                   if ((t or {}).get("function") or {}).get("name") == "tool_search"), "")
    deferred = sorted(set(re.findall(r"mcp__[a-z0-9_]+", search)))
    return {"top_level": [n for n in top if n], "deferred": deferred}


def audit_session(session_id: str | None, state_db: Path | None = None) -> dict:
    """Hermes session 기록(state.db, 읽기 전용)으로 tool 경로를 확인한다.

    - offered: 모델에 제공된 tool. top-level은 bridge(tool_search/describe/call)만, deferred는 두 MCP의 tool만 허용
    - requested / executed: 모델이 요청한 tool과 실제 실행된 tool (bridge 안의 대상 tool 포함)
    - 허용 외 tool이 제공·요청·실행되었거나 필수 tool이 실행되지 않았으면 ok=false
    """
    audit = {"session_id": session_id, "tools_used": [], "executed_counts": {}, "requested_counts": {}, "offered": None,
             "disallowed": [], "missing_required": [], "ok": False}
    path = state_db or HERMES_HOME / "state.db"
    if not session_id:
        audit["error"] = "Hermes session id 없음 (tool 사용 경로를 확인할 수 없음)"
        return audit
    rows, offered = [], None
    for _ in range(5):  # 종료 직후 기록이 늦게 보일 수 있다
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT role, tool_name, tool_calls FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
                ).fetchall()
                offered = _offered_tools(conn, session_id)
            finally:
                conn.close()
        except sqlite3.Error as e:
            audit["error"] = f"Hermes session 기록을 읽지 못함: {type(e).__name__}"
            return audit
        if rows:
            break
        time.sleep(0.5)

    executed = [tool_name for role, tool_name, _ in rows if role == "tool" and tool_name]
    requested: list[str] = []
    for role, _, tool_calls in rows:
        if role != "assistant" or not tool_calls:
            continue
        try:
            calls = json.loads(tool_calls)
        except ValueError:
            continue
        for call in calls if isinstance(calls, list) else []:
            fn = (call or {}).get("function") or {}
            if fn.get("name"):
                requested.append(fn["name"])
            if fn.get("name") == "tool_call":  # bridge 안의 실제 대상 tool
                try:
                    inner = json.loads(fn.get("arguments") or "{}").get("calls") or []
                except (ValueError, AttributeError):
                    inner = []
                requested += [c.get("name") for c in inner if isinstance(c, dict) and c.get("name")]

    def _count(names):
        counts: dict[str, int] = {}
        for n in names:
            counts[n] = counts.get(n, 0) + 1
        return counts

    def _allowed(name: str) -> bool:
        return name in BRIDGE_TOOLS or name in DECISION_DEBT_TOOLS or name.startswith(GBRAIN_PREFIX)

    audit["tools_used"] = list(dict.fromkeys(executed))
    audit["executed_counts"] = _count(executed)
    audit["requested_counts"] = _count(requested)
    disallowed = {n for n in executed + requested if not _allowed(n)}
    if offered is not None:
        audit["offered"] = {
            "top_level": offered["top_level"],
            "deferred_counts": {
                "decision_debt": sum(n.startswith("mcp__decision_debt__") for n in offered["deferred"]),
                "gbrain": sum(n.startswith(GBRAIN_PREFIX) for n in offered["deferred"]),
            },
        }
        disallowed |= {f"offered:{n}" for n in offered["top_level"] if n not in BRIDGE_TOOLS}
        disallowed |= {f"offered:{n}" for n in offered["deferred"]
                       if not (n.startswith("mcp__decision_debt__") or n.startswith(GBRAIN_PREFIX))}
    audit["disallowed"] = sorted(disallowed)
    missing = [t for t in REQUIRED_TOOLS if t not in executed]
    if not any(t.startswith(GBRAIN_PREFIX) for t in executed):
        missing.append("mcp__gbrain__* (search/query)")
    audit["missing_required"] = missing
    audit["ok"] = not audit["disallowed"] and not missing
    return audit


# 사실성 검사 (LLM 없이 문장 패턴만): 전제가 있었다고 단정하는 표현 / baseline인데 증가를 단정하는 표현
_ASSERTED_PREMISE = re.compile(
    r"전제(였던|였다|였음|였으나)|전제(가|는|도)\s*(더\s*이상|깨졌|깨진|무너|성립하지\s*않|유효하지\s*않|바뀌었|달라졌)"
)
# 증가를 '단정'하는 표현만. "증가했는지", "늘었는지"(의문) / "증가한 시점"(시점 언급)은 단정이 아니다
_ASSERTED_INCREASE = re.compile(r"(증가했|증가하였|늘어났|늘었)(?!는지|을지|던지|는가)|증가한\s(?!시점|시기|때)")
# 기록이 없다고 밝히는 줄은 변화를 단정한 것이 아니다 (v4 memo #4: "…언제 증가했는지는 기록에 없다"가 오탐으로 실패)
_NO_RECORD = re.compile(r"기록(에|되어|돼)?\s*(없|있지\s*않|되지\s*않)|알\s*수\s*없|확인되지\s*않|확인할\s*수\s*없|단정할\s*수\s*없|단정하지\s*않")
NO_ASSUMPTIONS_TEXT = "명시적으로 기록된 전제 없음"


def _section(memo: str, heading: str) -> str:
    """heading부터 같은 수준 이상의 다음 heading 전까지 (## 섹션은 그 아래 ### 포함)"""
    m = re.search(rf"^{re.escape(heading)}\s*$", memo, re.M)
    if not m:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    end = re.compile(rf"^#{{1,{level}}}\s", re.M).search(memo, m.end())
    return memo[m.end(): end.start() if end else len(memo)]


def check_facts(memo: str, detected_by: str, assumptions: list | None) -> list[str]:
    """기록에 없는 전제/변화를 단정했는지. assumptions=None(모름)이면 전제 검사는 건너뛴다."""
    problems = []
    if assumptions is not None and not assumptions:
        premise = _section(memo, "## 당시 전제")
        if NO_ASSUMPTIONS_TEXT not in premise:
            problems.append(f"assumptions가 비어 있는데 '당시 전제'에 '{NO_ASSUMPTIONS_TEXT}'가 없음")
        seen = set()  # '달라진 점'은 'AI 검토 의견' 안에 있으므로 같은 줄은 한 번만
        for heading in ("## 당시 전제", "### 달라진 점", "## AI 검토 의견"):
            for line in _section(memo, heading).splitlines():
                if _ASSERTED_PREMISE.search(line) and line not in seen:
                    seen.add(line)
                    problems.append(f"기록에 없는 전제를 단정함 ({heading.lstrip('# ')}): {line.strip()[:80]}")
    if detected_by == "review_state_baseline":
        for line in _section(memo, "### 달라진 점").splitlines():
            if _ASSERTED_INCREASE.search(line) and not _NO_RECORD.search(line):
                problems.append(f"baseline인데 증가를 단정함 (달라진 점): {line.strip()[:80]}")
    return problems


def validate_memo(text: str, detected_by: str, assumptions: list | None = None) -> tuple[str | None, list[str]]:
    """(정리된 memo, 문제 목록). 머리말 앞의 군더더기는 잘라낸다."""
    text = (text or "").strip()
    if text.startswith("NOT_REVIEW_DUE"):
        return None, ["Hermes가 대상 결정을 REVIEW_DUE로 확인하지 못함 (NOT_REVIEW_DUE)"]
    start_at = text.find(MEMO_HEADER)
    if start_at < 0:
        return None, [f"'{MEMO_HEADER}' 형식이 아님"]
    memo = text[start_at:].strip()
    problems = [f"섹션 누락: {s}" for s in REQUIRED_SECTIONS if not re.search(rf"^{re.escape(s)}\s*$", memo, re.M)]
    if FINAL_STATUS_LINE not in memo.split("## 최종 상태", 1)[-1]:
        problems.append(f"최종 상태 누락: {FINAL_STATUS_LINE}")
    expected = DETECTION_LINES.get(detected_by)
    if expected and expected not in memo:
        problems.append(f"감지 방식 문장 누락 ({detected_by})")
    for other, line in DETECTION_LINES.items():
        if other != detected_by and line in memo:
            problems.append(f"감지 방식이 실제({detected_by})와 다름")
    return memo, problems + check_facts(memo, detected_by, assumptions)


_EVIDENCE_ID_LINE = re.compile(r"[-*]?\s*(E\d+(?:\s*[,/]?\s*E\d+)*)\s*[.,]?")
_TIMESTAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")


def _replace_section(memo: str, heading: str, body: str) -> str:
    m = re.search(rf"^{re.escape(heading)}\s*$", memo, re.M)
    old = _section(memo, heading)
    start = m.end()
    return memo[:start] + "\n" + body.rstrip() + "\n\n" + memo[start + len(old):].lstrip("\n")


def render_evidence(memo: str, items: list[dict], raw_condition: str | None = None) -> tuple[str, list[str]]:
    """v3: '원래 근거'의 Evidence ID를 snapshot 원문(exact string)으로 바꾸고, 재검토 조건 원문을 기록 그대로 덧붙인다.
    LLM이 쓴 인용문은 저장하지 않는다. ID 외 텍스트 / 없는 ID / 원래 근거 밖의 타임스탬프 원문은 문제로 돌려준다."""
    problems: list[str] = []
    by_id = {i["id"]: i for i in items}
    ids: list[str] = []
    for line in _section(memo, "## 원래 근거").splitlines():
        text = line.strip()
        if not text:
            continue
        if not items and text.strip("-* ") in ("없음", "evidence 없음"):
            continue
        m = _EVIDENCE_ID_LINE.fullmatch(text)
        if not m:
            problems.append(f"원래 근거에 Evidence ID가 아닌 텍스트: {text[:60]}")
            continue
        ids += re.findall(r"E\d+", m.group(1))
    unknown = [i for i in dict.fromkeys(ids) if i not in by_id]
    if unknown:
        problems.append(f"없는 Evidence ID: {', '.join(unknown)}")
    ids = [i for i in dict.fromkeys(ids) if i in by_id]  # 중복 제거, 순서 유지
    if items and not ids:
        problems.append("원래 근거에 Evidence ID가 없음")

    lines, current = [], None
    for eid in ids:
        item = by_id[eid]
        if item["source_id"] != current:
            current = item["source_id"]
            lines.append(f"출처: {item.get('source_title') or item['source_id']} (source_id: {item['source_id']})")
        lines.append(f"- {eid} · {item['text']}")  # snapshot 원문 그대로
    if not items:
        lines = ["없음"]
    outside = memo.replace(_section(memo, "## 원래 근거"), "")
    if _TIMESTAMP.search(outside):
        problems.append("원래 근거 밖에 타임스탬프 원문 인용이 있음 (Evidence ID로 참조해야 함)")
    rendered = _replace_section(memo, "## 원래 근거", "\n".join(lines))
    if raw_condition:
        body = _section(rendered, "## 재검토 조건").rstrip()
        rendered = _replace_section(rendered, "## 재검토 조건", f"{body}\n- 원문 (기록 그대로): {raw_condition}".lstrip("\n"))
    return rendered, problems


async def _run_hermes(prompt: str) -> tuple[int | None, str, str, dict]:
    """(returncode | None=timeout, stdout, stderr, usage report)"""
    with tempfile.TemporaryDirectory(prefix="review-memo-") as tmp:
        usage_path = Path(tmp) / "usage.json"
        proc = await asyncio.create_subprocess_exec(
            *build_argv(prompt, str(usage_path)),
            cwd=tmp,  # 빈 작업 폴더 (AGENTS.md 등 프로젝트 파일을 읽지 않게)
            env=_hermes_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=HERMES_REVIEW_MEMO_TIMEOUT_SECONDS)
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


async def run(memo_id: int) -> ReviewMemo | None:
    """background: Hermes 실행 → completed | failed. 예외를 밖으로 내지 않는다. memo 행 외에는 아무것도 쓰지 않는다."""
    memo = db.get_review_memo(memo_id)
    if memo is None or memo.status != "generating":
        return memo
    t0 = time.monotonic()
    log.info("memo #%s %s 생성 시작 (prompt %s, toolsets %s)", memo_id, memo.decision_id, memo.prompt_version,
             ",".join(TOOLSETS))
    try:
        items = (memo.source_snapshot or {}).get("evidence_items")
        facts = render_facts(memo.observed_snapshot or {}, memo.source_snapshot or {}) if memo.prompt_version == "v4" else ""
        code, out, err, usage = await _run_hermes(build_prompt(memo.decision_id, memo.prompt_version, items, facts))
    except FileNotFoundError:
        return _fail(memo_id, f"Hermes 실행 파일을 찾을 수 없습니다 (HERMES_BIN={HERMES_BIN})")
    except Exception as e:  # noqa: BLE001
        log.exception("memo #%s Hermes 실행 오류", memo_id)
        return _fail(memo_id, f"Hermes 실행 오류: {type(e).__name__}")

    meta = {"model": usage.get("model"), "provider": usage.get("provider"), "agent_session_id": usage.get("session_id")}
    audit = await asyncio.to_thread(audit_session, usage.get("session_id"))
    audit["elapsed_seconds"] = round(time.monotonic() - t0, 1)
    audit["toolsets"] = list(TOOLSETS)
    if code is None:
        return _fail(memo_id, f"Hermes 시간 초과 ({HERMES_REVIEW_MEMO_TIMEOUT_SECONDS:g}s)", audit=audit, **meta)
    if "ignoring unknown --toolsets" in err or "ignoring disabled MCP" in err:
        return _fail(memo_id, f"Hermes toolset 설정 오류: {_summarize_error(err)}", audit=audit, **meta)
    if code != 0 or usage.get("failed"):
        reason = usage.get("failure") or err or out
        return _fail(memo_id, f"Hermes 실행 실패 (exit {code}): {_summarize_error(str(reason))}", audit=audit, **meta)
    if not audit["ok"]:
        problems = audit.get("error") or "; ".join(
            ([f"허용되지 않은 tool 사용: {', '.join(audit['disallowed'])}"] if audit["disallowed"] else [])
            + ([f"필수 tool 미사용: {', '.join(audit['missing_required'])}"] if audit["missing_required"] else [])
        )
        return _fail(memo_id, f"tool 사용 경로 검증 실패: {problems}", audit=audit, **meta)
    if memo.prompt_version == "v4":
        content, problems = assemble_v4(out, memo.observed_snapshot or {}, memo.source_snapshot or {})
    else:
        content, problems = validate_memo(out, memo.detected_by, (memo.source_snapshot or {}).get("assumptions"))
    if content is not None and memo.prompt_version == "v3":
        raw_condition = ((memo.observed_snapshot or {}).get("trigger") or {}).get("raw_condition")
        content, render_problems = render_evidence(content, items or [], raw_condition)
        problems += render_problems
    if problems:
        return _fail(memo_id, f"memo 검증 실패: {'; '.join(problems[:5])}", audit=audit, **meta)
    done = db.finish_review_memo(memo_id, "completed", content=content, tool_audit=audit, **meta)
    log.info("memo #%s completed (%.1fs, %s, tools %s)", memo_id, audit["elapsed_seconds"], meta["model"], audit["tools_used"])
    return done


def _fail(memo_id: int, error: str, audit: dict | None = None, **meta) -> ReviewMemo:
    log.warning("memo #%s failed: %s", memo_id, error)
    return db.finish_review_memo(memo_id, "failed", error=error, tool_audit=audit, **meta)
