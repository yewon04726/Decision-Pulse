"""Deep Review handoff contract (순수 함수. DB에 쓰지 않는다).

backend(오케스트레이션 bridge)와 Paperclip worker가 같은 검증 코드를 쓴다.
- ticket 본문의 입력 블록 / comment의 출력 블록 형식
- 역할별 출력 schema 검증 (schemas.DeepReview*) + reference 검증 (입력에 실제로 있는 E / FACT / DEC만)
- 검증에 실패한 출력은 저장되거나 다음 단계로 넘어가지 않는다 (ContractError)
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from schemas import (
    DEEP_REVIEW_OUTPUT_MODELS,
    DeepReviewAnalysis,
    DeepReviewCritique,
    DeepReviewResearch,
    ResearchFindings,
)

INPUT_BEGIN, INPUT_END = "<!-- deep-review-input:begin -->", "<!-- deep-review-input:end -->"
OUTPUT_BEGIN, OUTPUT_END = "<!-- deep-review-output:begin -->", "<!-- deep-review-output:end -->"
ROLE_LABELS = {"researcher": "Evidence Researcher", "analyst": "Decision Analyst", "critic": "Review Critic"}
STAGE_TITLES = {
    "researcher": "Collect authoritative evidence",
    "analyst": "Analyze decision assumptions vs current state",
    "critic": "Critically verify analysis",
}


class ContractError(Exception):
    """LLM 출력이 contract를 통과하지 못함 (JSON 오류 / 필드 누락 / 잘못된 verdict / 없는 reference …)"""


# ---------------------------------------------------------------- ticket 입력 / comment 출력 블록


def _block(begin: str, end: str, data: dict) -> str:
    return f"{begin}\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```\n{end}"


def _parse_block(text: str, begin: str, end: str) -> dict | None:
    m = re.search(re.escape(begin) + r"\s*```json\s*(.*?)\s*```\s*" + re.escape(end), text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def render_input(role: str, iteration: int, data: dict, intro: str) -> str:
    payload = {"role": role, "iteration": iteration, **data}
    return f"{intro}\n\n{_block(INPUT_BEGIN, INPUT_END, payload)}\n"


def parse_input(description: str) -> dict | None:
    return _parse_block(description, INPUT_BEGIN, INPUT_END)


def render_output_comment(envelope: dict) -> str:
    role = envelope.get("role", "")
    head = f"**{ROLE_LABELS.get(role, role)}** — "
    head += "schema-validated output" if envelope.get("ok") else f"failed: {envelope.get('error', '')}"
    return f"{head}\n\n{_block(OUTPUT_BEGIN, OUTPUT_END, envelope)}\n"


def parse_output_comment(body: str) -> dict | None:
    return _parse_block(body, OUTPUT_BEGIN, OUTPUT_END)


def extract_json(text: str) -> dict:
    """LLM 응답 → JSON object. ```json 코드 블록과 앞뒤 군더더기만 벗긴다 (내용은 바꾸지 않음)."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    elif not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ContractError("JSON parse failure: 응답에 JSON object가 없음")
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ContractError(f"JSON parse failure: {e}") from e
    if not isinstance(data, dict):
        raise ContractError("JSON parse failure: object가 아님")
    return data


def _model(model, payload: dict):
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in e.errors()[:6])
        raise ContractError(f"schema validation failed ({model.__name__}): {problems}") from e


# ---------------------------------------------------------------- snapshot (backend가 DB 기록 그대로 만든다)


def build_snapshot(item: dict, detail: dict, evidence_items: list[dict], detection_text: str, trigger_text: str,
                   context: dict | None = None) -> dict:
    """Authoritative Facts (Review Memo v4와 같은 read service 값). LLM이 만들지 않는다.
    context(실행 사유 / 실행 시점 상태 / mapping 상태)도 backend가 정한 사실로 고정한다.
    Deep Review 실행 자체는 trigger 충족의 근거가 아니다: review_reason=MANUAL이면 trigger_condition_met은 False/None."""
    trigger = item.get("trigger") or {}
    ctx = context or {"review_reason": "TRIGGERED", "review_state": "REVIEW_DUE", "mapping_status": "CONNECTED",
                      "current_value": {"available": True}, "required_unknowns": []}
    facts = {
        "decision_id": detail.get("decision_id"),
        "title": detail.get("title"),
        "project": detail.get("project"),
        "topic": detail.get("topic"),
        "decision": detail.get("decision"),
        "rationale": detail.get("rationale"),
        "assumptions": list(detail.get("assumptions") or []),
        "trigger": trigger_text,
        "raw_condition": trigger.get("raw_condition") or trigger.get("description"),
        "detection": detection_text,
        "detected_by": item.get("detected_by"),
        "review_state": detail.get("review_state"),
        "status": detail.get("status"),
        "human_verified": detail.get("human_verified"),
        "review_reason": ctx.get("review_reason"),
        "review_reason_note": ctx.get("review_reason_note"),
        "current_review_state": ctx.get("review_state"),
        "trigger_condition_met": ctx.get("trigger_condition_met"),
        "mapping_status": ctx.get("mapping_status"),
        "mapping_reason": ctx.get("mapping_reason"),
        "canonical_metric": ctx.get("canonical_metric"),
    }
    observed = {k: item.get(k) for k in ("review_status", "observed", "last_check_status", "last_checked_at",
                                         "detected_at", "event")}
    observed["current_value"] = ctx.get("current_value")
    return {"decision_id": facts["decision_id"], "review_reason": ctx.get("review_reason"),
            "review_state": ctx.get("review_state"), "mapping_status": ctx.get("mapping_status"),
            "authoritative_facts": facts, "observed_state": observed,
            "required_unknowns": list(ctx.get("required_unknowns") or []), "evidence_items": evidence_items}


def is_triggered(snapshot_or_research: dict) -> bool:
    """실행 사유가 TRIGGERED(REVIEW_DUE)인가. v8 이전 snapshot에는 사유가 없다 → 그때는 REVIEW_DUE에서만 실행 가능했다."""
    facts = snapshot_or_research.get("authoritative_facts") or {}
    return (snapshot_or_research.get("review_reason") or facts.get("review_reason") or "TRIGGERED") == "TRIGGERED"


def current_value_available(observed_state: dict) -> bool:
    """현재 관측값이 있는가 (없으면 FACT:observed를 근거로 쓸 수 없다). v8 이전 snapshot은 항상 있었다."""
    current = (observed_state or {}).get("current_value")
    return True if current is None else bool(current.get("available"))


# 실행 맥락 사실은 값이 null이어도 인용할 수 있다 (예: trigger_condition_met = null → "충족 여부를 알 수 없다"는 사실)
CONTEXT_FACT_KEYS = ("review_reason", "current_review_state", "trigger_condition_met", "mapping_status")


def fact_refs(facts: dict, observed_available: bool = True) -> set[str]:
    refs = {f"FACT:{k}" for k, v in facts.items() if k != "assumptions" and v not in (None, "", [])}
    refs |= {f"FACT:{k}" for k in CONTEXT_FACT_KEYS if k in facts}
    refs |= {f"FACT:assumptions[{i}]" for i in range(len(facts.get("assumptions") or []))}
    if observed_available:  # 현재 관측값이 없으면 (NEEDS_MAPPING / MANUAL / 읽기 실패) 관측값을 근거로 쓸 수 없다
        refs.add("FACT:observed")
    return refs


def known_refs(research: dict) -> set[str]:
    refs = fact_refs(research.get("authoritative_facts") or {}, current_value_available(research.get("observed_state")))
    refs |= {e["ref"] for e in research.get("evidence") or []}
    refs |= {d["decision_id"] for d in research.get("related_decisions") or []}
    return refs


# ---------------------------------------------------------------- 역할별 검증


def assemble_research(findings: dict, snapshot: dict) -> dict:
    """Researcher(Hermes) findings + backend snapshot → DeepReviewResearch. evidence 원문은 snapshot에서 그대로 가져온다."""
    f = _model(ResearchFindings, findings)
    if f.decision_id != snapshot["decision_id"]:
        raise ContractError(f"decision_id 불일치: {f.decision_id} != {snapshot['decision_id']}")
    # confirmed_review_due = Researcher가 list_review_due로 확인한 결과. backend 실행 사유와 같아야 한다
    if is_triggered(snapshot) and not f.confirmed_review_due:
        raise ContractError("Researcher가 대상 결정을 REVIEW_DUE로 확인하지 못함")
    if not is_triggered(snapshot) and f.confirmed_review_due:
        raise ContractError("manual Deep Review인데 Researcher가 대상 결정을 REVIEW_DUE(trigger 충족)로 보고함")
    by_id = {e["id"]: e for e in snapshot.get("evidence_items") or []}
    unknown = [e.ref for e in f.evidence if e.ref not in by_id]
    if unknown:
        raise ContractError(f"invalid evidence reference: {', '.join(unknown)} (snapshot에 없는 ID)")
    seen: set[str] = set()
    evidence = []
    for e in f.evidence:
        if e.ref in seen:
            continue
        seen.add(e.ref)
        item = by_id[e.ref]
        evidence.append({"ref": e.ref, "source_id": item["source_id"], "source_title": item.get("source_title"),
                         "text": item["text"], "relevance": e.relevance})
    related = [d.model_dump() for d in f.related_decisions if d.decision_id != snapshot["decision_id"]]
    research = {
        "decision_id": snapshot["decision_id"],
        "authoritative_facts": snapshot["authoritative_facts"],
        "observed_state": snapshot["observed_state"],
        "evidence": evidence,
        "related_decisions": related,
        # 현재 상태가 없다는 사실(metric not mapped / manual trigger …)은 backend가 결정적으로 앞에 넣는다
        "unknowns": list(dict.fromkeys([*(snapshot.get("required_unknowns") or []), *f.unknowns])),
    }
    return validate_research(research, snapshot)


def validate_research(payload: dict, snapshot: dict, decision_exists=None) -> dict:
    r = _model(DeepReviewResearch, payload)
    if r.decision_id != snapshot["decision_id"]:
        raise ContractError("decision_id 불일치")
    # 사실 부분은 snapshot과 정확히 같아야 한다 (LLM/worker가 바꿀 수 없다)
    if r.authoritative_facts != snapshot["authoritative_facts"] or r.observed_state != snapshot["observed_state"]:
        raise ContractError("authoritative_facts / observed_state가 backend snapshot과 다름")
    missing = [u for u in snapshot.get("required_unknowns") or [] if u not in r.unknowns]
    if missing:
        raise ContractError(f"현재 상태 부재가 unknowns에 없음: {missing[0]}")
    by_id = {e["id"]: e for e in snapshot.get("evidence_items") or []}
    for e in r.evidence:
        src = by_id.get(e.ref)
        if src is None or src["text"] != e.text or src["source_id"] != e.source_id:
            raise ContractError(f"invalid evidence reference: {e.ref} (원문과 다름)")
    for d in r.related_decisions:
        if d.decision_id == r.decision_id:
            raise ContractError("related_decisions에 대상 Decision 자신이 있음")
        if decision_exists is not None and not decision_exists(d.decision_id):
            raise ContractError(f"invalid evidence reference: {d.decision_id} (Decision Pulse에 없는 Decision)")
    return r.model_dump()


def _check_refs(refs: list[str], known: set[str], where: str):
    bad = sorted({r for r in refs if r not in known})
    if bad:
        raise ContractError(f"invalid evidence reference in {where}: {', '.join(bad)} (research에 없는 reference)")


def validate_analysis(payload: dict, research: dict) -> dict:
    a = _model(DeepReviewAnalysis, payload)
    if a.decision_id != research["decision_id"]:
        raise ContractError("decision_id 불일치")
    known = known_refs(research)
    for i, c in enumerate(a.known_changes):
        _check_refs(c.evidence_refs, known, f"known_changes[{i}]")
    for i, c in enumerate(a.implications):
        _check_refs(c.evidence_refs, known, f"implications[{i}]")
    _check_refs(a.evidence_refs, known, "evidence_refs")
    if not a.known_changes and not a.implications:
        raise ContractError("known_changes와 implications가 모두 비어 있음")
    if not current_value_available(research.get("observed_state")) and not (a.unknowns and a.recommended_checks):
        # 현재 상태가 없으면 실패가 아니라 unknown + 사람이 확인할 항목으로 넘긴다 (추측 금지)
        raise ContractError("현재 관측값이 없는데 unknowns / recommended_checks가 비어 있음")
    return a.model_dump()


def validate_critique(payload: dict, research: dict, analysis: dict) -> dict:
    c = _model(DeepReviewCritique, payload)
    if c.decision_id != research["decision_id"] or analysis.get("decision_id") != research["decision_id"]:
        raise ContractError("decision_id 불일치")
    known = known_refs(research)
    _check_refs([x.evidence_ref for x in c.contradictions], known, "contradictions")
    return c.model_dump()


def output_schema(role: str) -> dict:
    """LLM response_format용 JSON schema (Researcher는 findings만)"""
    model = ResearchFindings if role == "researcher" else DEEP_REVIEW_OUTPUT_MODELS[role]
    return model.model_json_schema()
