"""조회 전용 Decision read service (Hermes MCP 등 외부 agent용).

SQLite를 새로 해석하지 않고 기존 db 함수(get_decision, get_review_states, list_review_events, get_source …)와
review_trigger formatter를 조합만 한다. 쓰기/승인/metric 조회(check_metric)/LLM 호출은 하지 않는다.

'현재 REVIEW_DUE' 기준 = review monitor가 저장한 decision_review_state.last_review_status.
- 이벤트는 상태가 '바뀔 때만' 생기므로(승인 시점에 이미 조건을 넘은 결정은 baseline만 있고 이벤트가 없다),
  이벤트 목록이 아니라 review state를 기준으로 하고, 열려 있는 REVIEW_DUE 이벤트가 있으면 함께 붙인다.
- 관측값은 monitor가 마지막으로 저장한 값이다 (조회할 때마다 metric을 새로 확인하지 않는다).
"""

import json

import db
from schemas import Decision
from services.review_trigger import format_review_trigger_v2

EVIDENCE_EXCERPT_CHARS = 600  # 근거 인용 길이 상한 (원문 전체를 돌려주지 않는다)
_EVENT_SCAN_LIMIT = 500  # 최신 이벤트부터 본다


def _flatten_observed(observed: dict | None) -> dict | list | None:
    """{metric_key: {value, unit, source, observed_at}} → {"metric": key, value, unit, source, observed_at}"""
    if not observed:
        return None
    items = [{"metric": key, **(value or {})} for key, value in observed.items()]
    return items[0] if len(items) == 1 else items


def _trigger(decision: Decision) -> dict | None:
    t = decision.review_trigger
    return t.model_dump(mode="json", exclude_none=True) if t is not None else None


def _open_due_events(project_id: int) -> dict[str, dict]:
    """결정별 가장 최근의 '해소되지 않은' REVIEW_DUE 이벤트 (최신순으로 조회하므로 처음 본 것이 최신)"""
    found: dict[str, dict] = {}
    for e in db.list_review_events(project_id, new_status="REVIEW_DUE", limit=_EVENT_SCAN_LIMIT):
        if e.resolved_at is None and e.decision_id not in found:
            found[e.decision_id] = {
                "event_id": e.id,
                "previous_status": e.previous_status,
                "detected_at": e.detected_at,
                "acknowledged": e.acknowledged,
                "acknowledged_at": e.acknowledged_at,
            }
    return found


def _review_state(row) -> dict:
    observed = json.loads(row["last_observed_json"]) if row["last_observed_json"] else None
    return {
        "last_review_status": row["last_review_status"],  # 확정 판정 (ACTIVE | REVIEW_DUE)
        "last_check_status": row["last_check_status"],  # 마지막 확인 결과 (UNKNOWN이면 관측값은 이전 값)
        "last_observed": _flatten_observed(observed),
        "last_error": row["last_error"],
        "last_checked_at": row["last_checked_at"],
        "last_changed_at": row["last_changed_at"],
    }


def list_review_due(project_id: int | None = None) -> dict:
    """현재 REVIEW_DUE인 결정 (결정당 1개). project_id가 없으면 모든 프로젝트."""
    if project_id is not None:
        project = db.get_project(project_id)
        if project is None:
            return {"error": "project_not_found", "project_id": project_id}
        projects = [project]
    else:
        projects = db.list_projects()

    items = []
    for project in projects:
        states = db.get_review_states(project.id)
        events = _open_due_events(project.id)
        for decision_id, row in states.items():
            if row["last_review_status"] != "REVIEW_DUE":
                continue
            decision = db.get_decision(decision_id)
            if decision is None or decision.status != "active":  # 정리/대체된 결정의 남은 state는 제외
                continue
            state = _review_state(row)
            event = events.get(decision_id)
            items.append({
                "decision_id": decision.id,
                "project_id": project.id,
                "project": project.name,
                "title": decision.title,
                "topic": decision.topic,
                "decision": decision.decision,
                "review_status": "REVIEW_DUE",
                "trigger": _trigger(decision),
                "trigger_text": format_review_trigger_v2(decision.review_trigger),
                "observed": state["last_observed"],
                "last_check_status": state["last_check_status"],
                "last_checked_at": state["last_checked_at"],
                # 이벤트가 있으면 감지 시각, 없으면(monitor 시작/승인 시점부터 due) REVIEW_DUE로 확정된 시각
                "detected_at": event["detected_at"] if event else state["last_changed_at"],
                "detected_by": "review_event" if event else "review_state_baseline",
                "event": event,
            })
    items.sort(key=lambda i: (i["detected_at"] or "", i["decision_id"]), reverse=True)
    return {"count": len(items), "items": items}


def get_decision_detail(decision_id: str) -> dict:
    decision = db.get_decision(decision_id)
    if decision is None:
        return {"error": "decision_not_found", "decision_id": decision_id}
    project = db.get_project(decision.project_id)

    sources = {}
    for source_id in dict.fromkeys(e.source_id for e in decision.evidence):
        s = db.get_source(source_id)
        if s is not None and s.project_id == decision.project_id:
            sources[source_id] = s
    evidence = []
    for e in decision.evidence:
        s = sources.get(e.source_id)
        quote = e.quote.strip()
        evidence.append({
            "source_id": e.source_id,
            "source_title": s.title if s else None,
            "excerpt": quote[:EVIDENCE_EXCERPT_CHARS],
            "truncated": len(quote) > EVIDENCE_EXCERPT_CHARS,
        })

    state_row = db.get_review_states(decision.project_id).get(decision.id)
    event = _open_due_events(decision.project_id).get(decision.id)
    return {
        "decision_id": decision.id,
        "project_id": decision.project_id,
        "project": project.name if project else None,
        "topic": decision.topic,
        "title": decision.title,
        "decision": decision.decision,
        "rationale": decision.rationale,
        "assumptions": decision.assumptions,
        "notes": [n.model_dump() for n in decision.notes],
        "review_trigger": _trigger(decision),
        "review_trigger_text": format_review_trigger_v2(decision.review_trigger),
        "status": decision.status,
        "origin": decision.origin,
        "human_verified": decision.human_verified,
        "superseded_by": decision.superseded_by,
        "created_at": decision.created_at,
        "updated_at": decision.updated_at,
        "sources": [
            {"source_id": s.id, "title": s.title, "type": s.type, "date": s.date, "human_authored": s.human_authored}
            for s in sources.values()
        ],
        "evidence": evidence,
        # monitor가 확인하지 않는 결정(metric 조건 없음/미매핑)은 null
        "review_state": _review_state(state_row) if state_row else None,
        "open_review_event": event,
    }
