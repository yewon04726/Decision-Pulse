"""Decision → Dify Knowledge 동기화.

- decision_to_rag_text(): 승인된 Decision을 RAG가 읽기 좋은 텍스트로 변환
- sync_decision_to_dify(): 그 텍스트로 Dify Knowledge에 문서 1건을 생성
  (POST /datasets/{dataset_id}/document/create-by-text)
- sync_after_approve(): Approve API가 SQLite 저장 성공 후 호출. 실패해도 예외를 내지 않고 결과만 돌려준다.

수동 실행: scripts/sync_decision_to_dify.py

출력은 Knowledge v4 문서처럼 '결정 하나 = 한 블록'을 유지해서,
결정·이유·재검토 조건이 같은 청크 안에 들어가도록 한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import logging

import httpx

from config import DIFY_API_BASE, DIFY_DATASET_ID, DIFY_KNOWLEDGE_API_KEY, DIFY_KNOWLEDGE_TIMEOUT, KNOWLEDGE_TARGETS
from schemas import KnowledgeSyncStatus, MetricTrigger
from services.review_trigger import format_review_trigger_v2, normalize_review_trigger

log = logging.getLogger("knowledge_sync")

NONE_TEXT = "없음"
_INLINE_LABELS = {"Decision ID", "Title", "Topic"}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Pydantic 모델 / dict 어느 쪽이든 필드를 안전하게 읽는다."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def format_review_trigger(trigger: Any) -> str:
    """재검토 조건 → 한 줄 (V2 + legacy). 예: 'user_count > 1000', 'search_api_p95_ms > 800 AND 3일 연속', '없음'"""
    return format_review_trigger_v2(trigger)


def _source_label(source_id: str, sources: Mapping[str, Any] | None) -> str:
    source = (sources or {}).get(source_id)
    return _text(_get(source, "title")) or source_id


def decision_to_rag_text(decision: Any, sources: Mapping[str, Any] | None = None) -> str:
    """승인된 Decision을 RAG용 텍스트로 변환한다.

    decision: schemas.Decision(또는 DecisionView) 또는 같은 키를 가진 dict
    sources:  {source_id: Source 또는 {"title": ...}} — 있으면 Source에 문서명을 표시, 없으면 source_id 표시
    없는 필드는 오류 없이 '없음'으로 표시하거나 섹션을 생략한다.
    """
    sections: list[tuple[str, str]] = []

    def add(label: str, body: str):
        sections.append((label, body))

    if _text(_get(decision, "id")):
        add("Decision ID", _text(_get(decision, "id")))
    if _text(_get(decision, "title")):
        add("Title", _text(_get(decision, "title")))

    add("Topic", _text(_get(decision, "topic")) or NONE_TEXT)
    add("Decision", _text(_get(decision, "decision")) or NONE_TEXT)
    add("Rationale", _text(_get(decision, "rationale")) or NONE_TEXT)

    assumptions = [_text(a) for a in (_get(decision, "assumptions") or []) if _text(a)]
    if assumptions:
        add("Assumptions", "\n".join(f"- {a}" for a in assumptions))

    notes = []
    for n in _get(decision, "notes") or []:
        text = _text(_get(n, "text")) if not isinstance(n, str) else _text(n)
        if text:
            label = _text(_get(n, "label")) if not isinstance(n, str) else ""
            notes.append(f"- {label}: {text}" if label else f"- {text}")
    if notes:
        add("Notes", "\n".join(notes))

    trigger = normalize_review_trigger(_get(decision, "review_trigger"))
    trigger_lines = [format_review_trigger(trigger)]
    raw_condition = _text(_get(trigger, "raw_condition"))
    # 한 줄 조건 아래에 원문 조건 문장을 함께 둔다 (한국어 질의로도 검색되도록). 이미 같은 문장이면 생략
    if raw_condition and raw_condition not in trigger_lines[0]:
        trigger_lines.append(f"원문 조건: {raw_condition}")
    if isinstance(trigger, MetricTrigger) and trigger.needs_mapping:
        trigger_lines.append("참고: 등록된 metric에 아직 연결되지 않은 지표라 자동 판정할 수 없다.")
    note = _text(_get(trigger, "note"))
    if note:
        trigger_lines.append(f"참고: {note}")
    add("Review Trigger", "\n".join(trigger_lines))

    evidence = [e for e in (_get(decision, "evidence") or []) if e]
    source_ids = []
    for e in evidence:
        sid = _text(_get(e, "source_id"))
        if sid and sid not in source_ids:
            source_ids.append(sid)
    if source_ids:
        add("Source", "\n".join(_source_label(sid, sources) for sid in source_ids))
    quotes = [_text(_get(e, "quote")) for e in evidence if _text(_get(e, "quote"))]
    if quotes:
        add("Evidence", "\n".join(quotes) if len(quotes) == 1 else "\n".join(f"- {q}" for q in quotes))

    add("Status", _text(_get(decision, "status")) or NONE_TEXT)
    human_verified = _get(decision, "human_verified")
    add("Human Verified", NONE_TEXT if human_verified is None else str(bool(human_verified)).lower())

    # 짧은 식별 항목은 한 줄, 나머지는 '라벨:\n내용' 블록
    return "\n\n".join(
        f"{label}: {body}" if label in _INLINE_LABELS else f"{label}:\n{body}" for label, body in sections
    ) + "\n"


# ---------------------------------------------------------------- Dify Knowledge API


class KnowledgeSyncError(Exception):
    """Dify Knowledge 동기화 실패 (메시지에 원인을 담는다)"""


class KnowledgeSyncConfigError(KnowledgeSyncError):
    """필요한 환경변수가 없음"""


class DecisionNotApproved(KnowledgeSyncError):
    """human_verified가 true가 아닌 Decision — Dify를 호출하지 않는다"""


@dataclass
class KnowledgeSyncResult:
    success: bool
    document_name: str
    document_id: str | None
    batch: str | None  # 이번 요청으로 생성한 경우만. 기존 문서를 찾은 경우 None
    created: bool  # False = 같은 이름의 문서가 이미 있어 생성하지 않음
    indexing_status: str | None = None


# 결정 1건이 한 청크에 들어가도록: 본문에 없는 구분자 + 넉넉한 최대 길이
_PROCESS_RULE = {
    "mode": "custom",
    "rules": {
        "pre_processing_rules": [
            {"id": "remove_extra_spaces", "enabled": False},
            {"id": "remove_urls_emails", "enabled": False},
        ],
        "segmentation": {"separator": "\n\n\n\n", "max_tokens": 1000},
    },
}


def dify_document_name(decision: Any) -> str:
    """Decision ID 기반 문서 이름 → Dify에서 중복 여부를 이름으로 추적할 수 있다."""
    decision_id = _text(_get(decision, "id"))
    if not decision_id:
        raise KnowledgeSyncError("Decision에 id가 없어 Dify 문서 이름을 만들 수 없습니다.")
    return f"decision_{decision_id}"


def _settings(target: Mapping[str, str] | None = None) -> tuple[str, str, str]:
    """target: config.KNOWLEDGE_TARGETS 항목 ({api_base, api_key, dataset_id}). 없으면 .env 기본값."""
    if target is None:
        target = {"api_base": DIFY_API_BASE, "api_key": DIFY_KNOWLEDGE_API_KEY, "dataset_id": DIFY_DATASET_ID}
    missing = [
        env
        for env, key in (
            ("DIFY_API_BASE", "api_base"),
            ("DIFY_KNOWLEDGE_API_KEY", "api_key"),
            ("DIFY_DATASET_ID", "dataset_id"),
        )
        if not target.get(key)
    ]
    if missing:
        raise KnowledgeSyncConfigError(f"backend/.env 에 {', '.join(missing)} 가 설정되지 않았습니다.")
    return target["api_base"].rstrip("/"), target["api_key"], target["dataset_id"]


def _raise_for_status(res: httpx.Response, action: str):
    if res.is_success:
        return
    try:
        body = res.json()
        detail = f"{body.get('code', '')} {body.get('message', '')}".strip() or res.text
    except ValueError:
        detail = res.text
    raise KnowledgeSyncError(f"Dify Knowledge API {action} 실패 (HTTP {res.status_code}): {detail[:300]}")


async def _request(client: httpx.AsyncClient, method: str, url: str, action: str, **kwargs) -> dict:
    try:
        res = await client.request(method, url, **kwargs)
    except httpx.TransportError as e:
        raise KnowledgeSyncError(f"Dify({url.split('/datasets')[0]})에 연결할 수 없습니다 ({action}): {type(e).__name__}") from e
    _raise_for_status(res, action)
    return res.json()


async def sync_decision_to_dify(
    decision: Any, sources: Mapping[str, Any] | None = None, target: Mapping[str, str] | None = None
) -> KnowledgeSyncResult:
    """승인된 Decision 1건을 Dify Knowledge 문서로 생성한다.

    - human_verified가 true가 아니면 DecisionNotApproved (Dify 호출 없음)
    - 같은 이름(decision_<ID>)의 문서가 이미 있으면 생성하지 않고 기존 문서 id를 돌려준다 (중복 방지)
    - 실패 시 KnowledgeSyncError (자동 재시도 없음)
    """
    if _get(decision, "human_verified") is not True:
        raise DecisionNotApproved(
            f"{_text(_get(decision, 'id')) or 'Decision'}은(는) 사람이 승인한 결정(human_verified=true)이 아니라 동기화하지 않습니다."
        )
    base, api_key, dataset_id = _settings(target)
    name = dify_document_name(decision)
    text = decision_to_rag_text(decision, sources)
    headers = {"Authorization": f"Bearer {api_key}"}
    dataset_url = f"{base}/datasets/{dataset_id}"

    async with httpx.AsyncClient(headers=headers, timeout=DIFY_KNOWLEDGE_TIMEOUT) as client:
        # 1) 중복 확인: 같은 이름의 문서가 있으면 만들지 않는다
        found = await _request(client, "GET", f"{dataset_url}/documents", "문서 조회", params={"keyword": name, "limit": 100})
        for doc in found.get("data") or []:
            if doc.get("name") == name:
                return KnowledgeSyncResult(
                    success=True,
                    document_name=name,
                    document_id=doc.get("id"),
                    batch=None,
                    created=False,
                    indexing_status=doc.get("indexing_status"),
                )

        # 2) 데이터셋 색인 설정을 그대로 사용 (다르게 보내면 Dify가 거부)
        dataset = await _request(client, "GET", dataset_url, "Knowledge 정보 조회")
        doc_form = dataset.get("doc_form") or "text_model"
        if doc_form != "text_model":
            raise KnowledgeSyncError(f"지원하지 않는 Knowledge 문서 형식입니다 (doc_form={doc_form}). text_model만 지원합니다.")
        body = {
            "name": name,
            "text": text,
            "indexing_technique": dataset.get("indexing_technique") or "high_quality",
            "doc_form": doc_form,
            "process_rule": _PROCESS_RULE,
        }

        # 3) 문서 생성
        created = await _request(client, "POST", f"{dataset_url}/document/create-by-text", "문서 생성", json=body)

    document = created.get("document") or {}
    if not document.get("id"):
        raise KnowledgeSyncError(f"Dify 응답에 document id가 없습니다: {str(created)[:300]}")
    return KnowledgeSyncResult(
        success=True,
        document_name=document.get("name") or name,
        document_id=document["id"],
        batch=created.get("batch"),
        created=True,
        indexing_status=document.get("indexing_status"),
    )


# ---------------------------------------------------------------- Approve 연동


def _safe_message(message: str, target: Mapping[str, str] | None) -> str:
    """사용자에게 보낼 메시지에서 API Key 등 민감정보를 제거한다."""
    secrets = [DIFY_KNOWLEDGE_API_KEY, *(t.get("api_key", "") for t in KNOWLEDGE_TARGETS.values())]
    if target:
        secrets.append(target.get("api_key", ""))
    for secret in filter(None, secrets):
        message = message.replace(secret, "***")
    return message[:300]


async def sync_after_approve(decision: Any, project: Any, sources: Mapping[str, Any] | None = None) -> KnowledgeSyncStatus:
    """Approve(SQLite 저장) 성공 후 호출한다. 어떤 경우에도 예외를 내지 않는다 → 승인은 그대로 유지된다.

    Knowledge 대상은 프로젝트의 agent_source로 찾는다. 그 프로젝트의 Agent가 읽는 Knowledge에만 들어가므로,
    다른 프로젝트의 Decision이 Decision Debt Knowledge v4로 들어가지 않는다.
    """
    agent_source = _get(project, "agent_source")
    target = KNOWLEDGE_TARGETS.get(agent_source) if agent_source else None
    if target is None:
        return KnowledgeSyncStatus(
            status="not_connected",
            message=f"'{_text(_get(project, 'name'))}' 프로젝트에는 Knowledge가 연결되지 않아 동기화하지 않았습니다.",
        )
    if _text(_get(decision, "project_id")) != _text(_get(project, "id")):
        return KnowledgeSyncStatus(status="failed", message="Decision과 프로젝트가 일치하지 않아 동기화하지 않았습니다.")
    try:
        result = await sync_decision_to_dify(decision, sources, target)
    except KnowledgeSyncError as e:
        log.warning("knowledge sync failed for %s: %s", _get(decision, "id"), _safe_message(str(e), target))
        return KnowledgeSyncStatus(status="failed", message=_safe_message(str(e), target), document_name=f"decision_{_get(decision, 'id')}")
    except Exception as e:  # 예상치 못한 오류도 승인에는 영향을 주지 않는다
        log.exception("unexpected knowledge sync error for %s", _get(decision, "id"))
        return KnowledgeSyncStatus(status="failed", message=f"예상치 못한 오류로 동기화하지 못했습니다 ({type(e).__name__}).")
    return KnowledgeSyncStatus(
        status="synced" if result.created else "already_exists",
        message="Knowledge 문서를 생성했습니다." if result.created else "같은 이름의 Knowledge 문서가 이미 있어 새로 만들지 않았습니다.",
        document_name=result.document_name,
        document_id=result.document_id,
        batch=result.batch,
    )
