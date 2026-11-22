"""Dify Chatflow(advanced-chat) 앱 proxy. Dify 설정은 건드리지 않고 API만 호출한다.

streaming 모드로 호출해 이벤트에서 다음을 꺼낸다.
- knowledge-retrieval 노드 출력 → Evidence (message_end의 retriever_resources는 이 앱 구조에서 비어 있음)
- agent_log "CALL <tool>" → Tool Activity
- 마지막 ROUND의 Thought 출력 → 최종 답변 (ROUND 1 Thought가 앞에 붙는 문제 회피, handoff §12)
"""

import json
import re
import time
from pathlib import PurePath

import httpx

from config import DIFY_TIMEOUT
from schemas import AgentChatResponse, RetrievedChunk, ToolCall
from services.text import foreign_han, normalize_cjk_punct


# 검색된 근거가 없을 때의 표준 응답 (handoff §6: threshold 0.50 아래 → 확인 불가)
NO_EVIDENCE_ANSWER = "근거 문서에서 확인할 수 없습니다. 확인 필요"
# Qwen이 중국어로 답했고 재질문 후에도 중국어일 때 (Dify 설정은 바꾸지 않고 backend에서 처리)
NON_KOREAN_ANSWER = "Agent 답변이 한국어로 생성되지 않아 표시하지 않습니다. 다시 질문해 주세요."
MAX_ATTEMPTS = 2  # 중국어 답변이면 새 대화로 한 번 더 묻는다


class DifyNotConfigured(Exception):
    pass


class DifyUnavailable(Exception):
    pass


class DifyError(Exception):
    pass


def _parse_tool_response(raw: str) -> dict | str:
    """'tool response: {...}.{...}' 형태에서 마지막 JSON 객체를 꺼낸다."""
    for candidate in reversed(re.findall(r"\{[^{}]*\}", raw)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return raw


def _parse_events(events: list[dict]) -> dict:
    retrieval: list[RetrievedChunk] = []
    tool_calls: list[ToolCall] = []
    final_thought: str | None = None
    raw_answer = ""
    current_round = 0
    conversation_id = message_id = None

    for e in events:
        event = e.get("event")
        data = e.get("data") or {}
        conversation_id = e.get("conversation_id") or conversation_id
        message_id = e.get("message_id") or message_id

        if event == "error":
            raise DifyError(e.get("message") or json.dumps(e, ensure_ascii=False))

        if event == "node_finished" and data.get("node_type") == "knowledge-retrieval":
            for r in (data.get("outputs") or {}).get("result") or []:
                meta = r.get("metadata") or {}
                title = meta.get("document_name") or r.get("title") or "unknown"
                retrieval.append(
                    RetrievedChunk(
                        source_id=PurePath(title).stem,
                        title=title,
                        score=float(meta.get("score") or 0),
                        snippet=r.get("content") or "",
                    )
                )

        elif event == "agent_log" and (data.get("label") or "").startswith("ROUND "):
            # ROUND n 'start' 이벤트가 해당 라운드의 CALL 로그보다 먼저 온다
            n = data["label"].split()[1]
            current_round = int(n) if n.isdigit() else current_round

        elif event == "agent_log" and data.get("status") == "success":
            label = data.get("label") or ""
            output = (data.get("data") or {}).get("output")
            if label.startswith("CALL ") and isinstance(output, dict):
                result = _parse_tool_response(str(output.get("tool_response", "")))
                tool_calls.append(
                    ToolCall(
                        name=output.get("tool_call_name") or label[5:],
                        arguments=output.get("tool_call_input") or {},
                        result=result,
                        status="error" if isinstance(result, str) and "error" in result.lower() else "success",
                        round=current_round,
                    )
                )
            elif label.endswith("Thought") and isinstance(data.get("data"), dict):
                thought = data["data"]
                if not thought.get("tool_input") and isinstance(thought.get("output"), str):
                    final_thought = thought["output"]

        elif event == "message":
            raw_answer += e.get("answer") or ""

        elif event == "workflow_finished":
            if data.get("status") != "succeeded":
                raise DifyError(data.get("error") or f"workflow {data.get('status')}")
            raw_answer = (data.get("outputs") or {}).get("answer") or raw_answer

    answer = (final_thought or raw_answer).strip()
    # 근거 없이 생성된 답변은 신뢰할 수 없으므로(과거 결정은 검색 문서에서만, handoff §14 규칙 1)
    # 표준 응답으로 대체한다. 모델 원문은 raw_answer에 남긴다.
    guard = None
    if not retrieval:
        answer, guard = NO_EVIDENCE_ANSWER, "no_evidence"
    return {
        "answer": answer,
        "guard": guard,
        "raw_answer": raw_answer.strip(),
        "retrieval": retrieval,
        "tool_calls": tool_calls,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }


async def _run_once(base_url: str, api_key: str, query: str, conversation_id: str | None, user: str) -> dict:
    body = {
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "user": user,
        "conversation_id": conversation_id or "",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    events: list[dict] = []
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=DIFY_TIMEOUT) as client:
            async with client.stream("POST", "/chat-messages", headers=headers, json=body) as res:
                if res.status_code != 200:
                    detail = (await res.aread()).decode(errors="replace")[:500]
                    raise DifyError(f"Dify HTTP {res.status_code}: {detail}")
                async for line in res.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            events.append(json.loads(line[5:].strip()))
                        except json.JSONDecodeError:
                            continue
    except httpx.TransportError as e:
        raise DifyUnavailable(f"Dify({base_url})에 연결할 수 없습니다: {type(e).__name__}") from e
    return _parse_events(events)


def _chinese_in_answer(parsed: dict, query: str) -> set[str]:
    """답변의 한자 중 질문/근거 문서에 없는 것 (= 중국어 혼입)"""
    allowed = query + " ".join(c.snippet for c in parsed["retrieval"])
    return foreign_han(parsed["answer"], allowed)


async def chat(
    agent: dict, query: str, conversation_id: str | None = None, user: str = "decision-debt-web"
) -> AgentChatResponse:
    """agent: config.AGENT_SOURCES의 항목 ({base_url, api_key}). 프로젝트마다 다른 Dify 앱을 붙일 수 있다.

    중국어 처리: 답변의 중국어 문장부호는 일반 기호로 바꾸고, 근거에 없는 한자가 섞이면
    새 대화로 한 번 더 묻는다. 그래도 중국어면 답변 대신 NON_KOREAN_ANSWER를 보여준다 (원문은 raw_answer).
    """
    base_url, api_key = agent["base_url"], agent["api_key"]
    if not api_key:
        raise DifyNotConfigured("backend/.env 에 DIFY_API_KEY가 설정되지 않았습니다.")

    started = time.perf_counter()
    for attempt in range(MAX_ATTEMPTS):
        # 재질문은 새 대화로 (중국어 답변이 대화 이력에 남아 다시 영향을 주지 않도록)
        parsed = await _run_once(base_url, api_key, query, conversation_id if attempt == 0 else None, user)
        if parsed["guard"] == "no_evidence":
            break
        parsed["answer"] = normalize_cjk_punct(parsed["answer"])
        if not _chinese_in_answer(parsed, query):
            break
    else:
        parsed["answer"], parsed["guard"] = NON_KOREAN_ANSWER, "non_korean"
    return AgentChatResponse(**parsed, latency_ms=round((time.perf_counter() - started) * 1000))
