"""Decision Extraction: 원문 → Decision Candidate 초안 (handoff §16). 2-stage pipeline.

llama-swap의 OpenAI 호환 API로 Qwen2.5-7B를 호출한다 (llama-swap 설정은 변경하지 않음).

Stage 1 (원문 전체, 1회): 무엇이 실제로 말해졌는지 찾는다. 해석하지 않고 원문을 보존한다.
  - 확정된 CURRENT DECISION, 명시된 RATIONALE, REVIEW CONDITION 원문, evidence(원문 exact span 배열)
  - 확정되지 않은 결정(not_confirmed)은 버린다
Backend (결정적 코드):
  - 현재 결정 없이 재검토 조건만 근거로 만든 항목은 후보로 만들지 않고 warning만 남긴다 (결정 추론 금지)
  - review condition을 원문 문장에 맞추고, 결정과 연결되지 않은 재검토 조건 문장을 바로 앞 결정에 연결한다
Stage 2 (재검토 조건이 있는 항목마다 1회): review condition 문장 하나만 V2 구조로 바꾼다.
  - 새 Decision/Rationale을 만들지 않는다. 후보 개수를 바꾸지 않는다
Backend 검증 (결정적 코드):
  - metric_key는 config.METRIC_ALIASES 완전 일치로만 매핑. 그 외는 null + needs_mapping
  - operator는 원문의 비교 표현(초과/이상/미만/이하...)으로 확인·교정, 모호하면 warning만
  - date는 원문에 달력 날짜가 있을 때만, duration은 "3일 연속"처럼 기간 표현이 있을 때만 허용
  - 검증 실패 → Stage 2 재시도 1회 → 그래도 실패하면 원문을 manual 조건으로 보존 (후보는 유지)

공통:
- evidence가 원문에 그대로 있는지 확인해 evidence_in_source로 표시 (DB에는 줄바꿈으로 구분해 한 문자열로 저장)
- title은 보조 필드: 비었거나 decision의 부정 의미("도입하지 않는다")를 뒤집으면 재호출 없이 decision 문장을 title로 쓴다
- 중국어 혼입 제거: 문장부호(。，：)는 일반 기호로 바꾸고, 원문에 없는 한자가 섞이면 재생성,
  재시도 후에도 남으면 저장하지 않고 오류로 처리한다
LLM 결과는 바로 Decision이 되지 않는다. Candidate로 저장되고 사람이 Approve해야 한다.
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

import metric_registry

from config import (
    EXTRACTION_MODEL,
    EXTRACTION_TIMEOUT,
    LLM_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_PROVIDER_SORT,
    OPENROUTER_REASONING_EFFORT,
    OPENROUTER_REQUIRE_PARAMETERS,
    OPENROUTER_STAGE1_MAX_TOKENS,
    OPENROUTER_STAGE2_MAX_TOKENS,
)
from schemas import ManualTrigger
from services.review_trigger import normalize_review_trigger
from services.text import foreign_han, normalize_cjk_punct

log = logging.getLogger("extraction")

MAX_ATTEMPTS = 3  # Stage 1: 핵심 필드 중국어 혼입 시 재생성 포함 최대 호출 수 (title 문제는 재호출하지 않음)
STAGE2_ATTEMPTS = 2  # Stage 2: 검증 실패 시 재시도 포함 최대 호출 수 (실패하면 manual로 보존)


class ExtractionUnavailable(Exception):
    pass


class ExtractionError(Exception):
    pass


STAGE1_PROMPT = """너는 소프트웨어 프로젝트의 회의록, Issue, ADR에서 '확정된 기술적 의사결정'이 적힌 원문을 찾는다.
이 단계에서는 해석하지 않는다. 원문에 실제로 적힌 내용을 찾아서 그대로 보존한다.

[찾을 것] decision, rationale, review_condition, evidence는 원문을 그대로 복사한다. 다시 쓰거나 다듬지 않는다. (topic, title만 요약 가능)
- decision: 지금 팀이 실제로 무엇을 하기로 확정했는가 (CURRENT DECISION). 원문 결정 문장을 그대로 복사한다.
  결정 문장은 "~하기로 한다", "~하는 걸로 합시다", "~로 정하죠", "~한다", "확정" 같은 합의 표현이 있는 문장이다.
  현황 설명("지금은 ~하고 있어요"), 제안이나 질문("~하는 게 어떨까요?")은 결정 문장이 아니다.
  제안이 뒤에서 확정되었으면 합의 표현이 있는 문장(또는 "오늘 결정은 ~입니다" 같은 정리 문장)을 decision으로 쓴다.
- rationale: 그 결정을 한 이유가 적힌 원문 문장을 그대로 복사한다. 결정 바로 다음 문장에 이유("이유는", "~때문이다",
  "~때문에", "~이 더 중요하다", "~우선")가 있으면 그 문장이 rationale이다. 이유가 원문에 없으면 "". 이유를 추측해서 만들지 않는다.
  예) 원문: "MVP에서는 단일 서버로 배포한다. 아직 트래픽이 작고 인프라 비용을 줄이는 것이 더 중요하다."
    → rationale "아직 트래픽이 작고 인프라 비용을 줄이는 것이 더 중요하다."
- review_condition: 미래에 어떤 조건이 되면 그 결정을 다시 검토/논의/확대/변경/전환한다는 원문 문장을 그대로 복사한다. 없으면 "".
  "~하면 다시 검토한다", "~일 때 다시 논의한다", "~를 넘으면", "~이상 발생하면 확대한다", "~이면 전환한다", "~할 경우 변경한다",
  "~거나 ~하면" 같은 문장이다. 문장이 여러 줄에 걸쳐 있으면 전체를 복사한다.
  "다음 주에 다시 이야기한다" 같은 단순 일정은 재검토 조건이 아니다.
  이 단계에서는 조건을 숫자나 구조로 바꾸지 않는다. 문장 그대로 복사만 한다.
- evidence: decision, rationale, review_condition이 적힌 원문 문장들을 배열로 쓴다.
  각 항목은 원문의 한 문장(또는 한 줄)을 한 글자도 바꾸지 않고 그대로 복사한다. 번역, 요약, 재작성 금지. 앞뒤에 따옴표를 붙이지 않는다.
  화자 이름, 시간 표시([00:01:07]), 문장부호, "네.", "그럼" 같은 말도 지우지 않고 원문 그대로 둔다.
  서로 떨어진 문장을 한 항목으로 이어 붙이지 않는다. 문장마다 별도 항목으로 쓴다.

[재검토 조건은 결정이 아니다]
- "~하면 다시 검토한다" 같은 문장은 decision이 아니라 그 결정의 review_condition이다. 별도 항목으로 만들지 않는다.
예) 원문: "로그는 서버 로컬 파일로 남긴다. 디스크 사용률이 80%를 넘으면 ELK 도입을 다시 검토한다."
  → 항목 1개. decision "로그는 서버 로컬 파일로 남긴다.", review_condition "디스크 사용률이 80%를 넘으면 ELK 도입을 다시 검토한다."

[확정된 결정만]
- "오늘은 결정하지 않는다", "다음에 다시 논의한다", "아직 확정하지 않았다", "A를 쓸 수도 있다", "B도 고려해볼 수 있다",
  질문, 아이디어, 후보안, 취소된 제안, 회의 중간 의견은 결정이 아니다. 이런 항목은 decision_status를 "not_confirmed"로 쓴다.
- 회의 중 앞에서 정했다가 뒤에서 취소/변경했다면 마지막 최종 결정 하나만 쓴다. 취소된 앞의 제안은 항목으로 만들지 않는다.
- 현재 결정 문장 없이 재검토 조건만 있으면 결정을 추론해서 만들지 않는다 ("현재 방식을 유지한다" 같은 문장을 만들지 않는다).
  이런 항목은 decision_status를 "no_current_decision"으로 쓴다.
- 원문에 현재 결정 문장이 있으면 재검토 조건이 붙어 있어도 decision_status는 "confirmed"다.
- 서로 독립적인 확정 결정은 각각 별도 항목으로 만든다. 확정된 결정이 하나도 없으면 items는 빈 배열이다.

[기타]
- topic은 결정 주제를 영어 소문자로 짧게 쓴다. 예: database, cache, authentication, deployment, logging, storage, api
- title은 결정의 핵심을 한국어 20자 이내로 짧게 쓴다 (영어로 쓰지 않는다).
  title은 decision의 긍정/부정 의미를 그대로 유지한다. 하지 않기로 한 결정이면 title에도 "미도입", "미사용", "하지 않음"을 넣는다.
  예) decision "MVP에서는 Kafka를 도입하지 않는다." → title "MVP Kafka 미도입" (잘못된 예: "MVP Kafka 도입")
- assumptions는 원문에 명시된 전제만 쓴다. 없으면 빈 배열.
- 모든 자연어 값은 한국어로 쓴다. SQLite, Redis, Worker 같은 기술 용어는 원문 표기를 유지한다.
- 중국어(한자 단어, 중국어 문장, 。，： 같은 중국어 문장부호)를 절대 사용하지 않는다."""


STAGE2_PROMPT = """너는 재검토 조건 문장 하나를 구조화한다. 주어진 review_condition 문장만 보고 판단한다.
새로운 결정이나 이유를 만들지 않는다. 문장에 없는 조건을 추가하지 않는다.

[조건 type]
- metric: 숫자로 비교하는 지표 조건. raw_metric(원문의 지표 표현을 주어까지 포함해 그대로), operator, threshold(숫자만), unit(원문 단위, 없으면 "").
  operator: "초과", "넘으면", "넘어서면" → ">" / "이상" → ">=" / "미만" → "<" / "이하" → "<=" / "같으면" → "==" / "아니면", "다르면" → "!="
  threshold는 단위를 뺀 숫자다. 예) "1,000명" → 1000, "500ms" → 500, "20GB" → 20
  횟수나 개수를 비교하면 raw_metric 끝에 "횟수" 또는 "수"를 붙여 지표 이름으로 쓴다.
  예) "API 서버가 4대 이상이면" → raw_metric "API 서버 수", operator ">=", threshold 4, unit "대"
  예) "하루 장애가 3번을 넘으면" → raw_metric "하루 장애 횟수", operator ">", threshold 3, unit "회"
  "한 달 안에", "하루" 같은 집계 기간은 duration으로 나누지 말고 raw_metric에 포함한다.
  예) "일주일 안에 롤백이 두 번 이상 발생하면" → raw_metric "일주일 안 롤백 횟수", operator ">=", threshold 2, unit "회"
- duration: 상태가 얼마나 지속되는지. value, unit(minute/hour/day/week/month), mode(consecutive=연속, within=기간 동안).
  "N일 연속", "N주 동안", "N시간 이상 지속" 같은 표현만 duration이다. duration은 compound AND 안에서 metric과 함께 쓴다.
- date: 원문에 "2027-01-01", "2027년 1월 1일" 같은 달력 날짜가 있을 때만 쓴다. date는 YYYY-MM-DD.
  "30일 보관", "3일 연속", "7일 동안", "2주 이상"은 date가 아니다.
- event: 숫자 비교가 아닌 사건. description에 사건을 원문 표현으로 짧게 쓴다 ("~함" 형태).
  "90일 보관을 요구하면"처럼 기간이 사건 설명의 일부이면 duration이나 date로 나누지 말고 event description에 그대로 포함한다.
- manual: 위 어디에도 맞지 않아 사람이 판단해야 하는 조건. description.
- compound: 조건 여러 개를 logic(AND/OR)으로 묶는다. conditions에 위 조건들을 넣는다 (compound 안에 compound는 넣지 않는다).
  "~한 상태가 N일 연속 지속되면" → AND [metric, duration]
  "~하거나 ~하면" → OR

[예시]
review_condition: "에러율이 5%를 초과한 상태가 2시간 이상 지속되면 롤백 정책을 다시 검토한다."
→ compound AND [metric(raw_metric "에러율", ">", 5, "%"), duration(2, hour, consecutive)]
review_condition: "월 비용이 300만원을 넘거나 고객사가 온프레미스 설치를 요구하면 클라우드 구성을 다시 논의한다."
→ compound OR [metric(raw_metric "월 비용", ">", 300, "만원"), event("고객사가 온프레미스 설치를 요구함")]
review_condition: "분기 안에 배포 롤백이 두 번 이상 발생하거나, 배포에 1시간을 넘긴 경우가 4회 이상 생기면 배포 방식을 바꾼다."
→ compound OR [metric(raw_metric "분기 안 배포 롤백 횟수", ">=", 2, "회"), metric(raw_metric "배포에 1시간을 넘긴 경우 횟수", ">=", 4, "회")]
  ("N건/N회/N번 이상 발생"은 사건 수를 세는 metric이다. event가 아니다)

모든 자연어 값은 한국어로 쓴다. 중국어를 절대 사용하지 않는다."""


_OPERATORS = [">", ">=", "<", "<=", "==", "!="]


def _stage1_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "decision": {"type": "string"},
                        "rationale": {"type": "string"},
                        "review_condition": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "decision_status": {"type": "string", "enum": ["confirmed", "no_current_decision", "not_confirmed"]},
                        "title": {"type": "string"},
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["topic", "decision", "rationale", "review_condition", "evidence", "decision_status", "title", "assumptions"],
                },
            }
        },
        "required": ["items"],
    }


def _stage2_schema() -> dict:
    """metric_key는 받지 않는다 (지표 매핑은 backend 코드가 한다). compound는 한 단계까지만 (7B 출력 안정성)."""

    def obj(type_: str, props: dict) -> dict:
        props = {"type": {"type": "string", "enum": [type_]}, **props}
        return {"type": "object", "properties": props, "required": list(props)}

    leaves = [
        obj("metric", {
            "raw_metric": {"type": "string"},
            "operator": {"type": "string", "enum": _OPERATORS},
            "threshold": {"type": "number"},
            "unit": {"type": "string"},
        }),
        obj("duration", {
            "value": {"type": "number"},
            "unit": {"type": "string", "enum": ["minute", "hour", "day", "week", "month"]},
            "mode": {"type": "string", "enum": ["consecutive", "within"]},
        }),
        obj("date", {"operator": {"type": "string", "enum": _OPERATORS}, "date": {"type": "string"}}),
        obj("event", {"description": {"type": "string"}}),
        obj("manual", {"description": {"type": "string"}}),
    ]
    compound = obj("compound", {
        "logic": {"type": "string", "enum": ["AND", "OR"]},
        "conditions": {"type": "array", "items": {"anyOf": leaves}, "minItems": 1},
    })
    return {"type": "object", "properties": {"trigger": {"anyOf": [*leaves, compound]}}, "required": ["trigger"]}


# ---------------------------------------------------------------- 원문 대조

_QUOTES = "\"'“”‘’"


def _normalize(text: str) -> str:
    """evidence 원문 대조용 정규화. 표기 차이만 없애고 문장 내용은 바꾸지 않는다 (fuzzy match 아님).

    - 중국어/전각 문장부호 → 일반 기호 (。→. ，→, ：→:)
    - 목록 기호(-, *, 1.) 제거: 모델이 bullet 줄을 인용할 때 기호를 빼는 경우
    - 공백/줄바꿈 → 공백 하나
    - 앞뒤 공백과 앞뒤 따옴표(" ' “ ” ‘ ’) 제거: 모델이 인용문을 따옴표로 감싸는 경우
    """
    text = normalize_cjk_punct(text)
    text = re.sub(r"(^|\n)\s*(?:[-*]|\d+\.)\s+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(_QUOTES).strip()


def evidence_in_source(evidence: str, content: str) -> bool:
    """evidence가 원문에 (정규화 후) 그대로 포함되는지. 줄바꿈으로 구분된 여러 문장이면 각 줄이 모두 있어야 한다.
    정규화 결과가 비면 False."""
    source = _normalize(content)
    lines = [_normalize(line) for line in evidence.split("\n")]
    lines = [line for line in lines if line]
    return bool(lines) and all(line in source for line in lines)


def _clean(text: str) -> str:
    return normalize_cjk_punct(text).strip()


def _clean_values(value):
    """trigger 안의 모든 문자열에 _clean 적용"""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        return [_clean_values(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean_values(v) for k, v in value.items()}
    return value


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _sentences(text: str) -> list[str]:
    return [s for s in (x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text)) if s]


def _units(content: str) -> list[str]:
    """원문을 줄 단위로 나눈다. 쉼표로 끝난 줄은 다음 줄과 한 문장으로 잇는다 (여러 줄에 걸친 조건 문장)."""
    units, buf = [], ""
    for line in (x.strip() for x in content.splitlines()):
        if not line:
            if buf:
                units.append(buf)
                buf = ""
            continue
        buf = f"{buf} {line}" if buf else line
        if not buf.endswith(","):
            units.append(buf)
            buf = ""
    if buf:
        units.append(buf)
    return units


def _match_key(text: str) -> str:
    """원문 포함 여부 비교용: 정규화 + 끝 문장부호 제거 ("검토한다." vs 원문 "검토한다, 이 정도요?")"""
    return _normalize(text).rstrip(".,?!~ ")


def _overlaps(a: str, b: str) -> bool:
    a, b = _match_key(a), _match_key(b)
    return bool(a and b) and (a in b or b in a)


# 화자/시간 표시 접두어: "[00:01:07] 지훈: ", "09:08 - ", "민수: ", "- "
_PREFIX = re.compile(r"^\s*(?:[-*]\s+)?(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*(?:-\s*)?)?(?:[^\s:\[\]][^:\[\]\n]{0,11}:\s+)?")


def _strip_prefix(text: str) -> str:
    rest = _PREFIX.sub("", text, count=1).strip()
    return rest or text.strip()


_MIN_SPAN = 6  # 너무 짧은 조각("네", "좋습니다")은 원문 위치를 특정하지 않는다
_MAX_UNIT = 250  # 이보다 긴 줄(문단)은 줄 전체 대신 해당 문장만 근거로 쓴다


def _locate(text: str, units: list[str]) -> str | None:
    """text가 원문에 그대로 있으면 그 원문 span을 돌려준다 (줄 전체, 긴 문단이면 해당 문장). 없으면 None.
    부분 일치/유사도 비교가 아니다: 정규화 후 완전 포함일 때만."""
    key = _match_key(text)
    if len(key) < _MIN_SPAN:
        return None
    hits = [u for u in units if key in _match_key(u)]
    if not hits:
        return None
    if len(hits) > 1:  # 같은 문장이 여러 번 나오면 위치를 특정하지 않고 text 자체(원문에 있음)를 쓴다
        return text.strip()
    unit = hits[0]
    if len(unit) <= _MAX_UNIT:
        return unit
    parts = [x for x in _sentences(unit) if key in _match_key(x)]
    return parts[0] if len(parts) == 1 else text.strip()


def _exact_spans(text: str, units: list[str]) -> list[str]:
    """모델이 쓴 문자열 → 원문 exact span 목록.
    그대로 있으면 1개. 없으면 화자/시간 접두어를 뗀 형태, 문장별로 나눈 형태로 다시 찾는다
    (모델이 떨어진 문장을 이어 붙였거나 접두어를 바꾼 경우). 어느 것도 원문에 없으면 빈 목록."""
    found = _locate(text, units) or _locate(_strip_prefix(text), units)
    if found:
        return [found]
    out = []
    for piece in _sentences(text):
        span = _locate(piece, units) or _locate(_strip_prefix(piece), units)
        if span and span not in out:
            out.append(span)
    return out


# 재검토 조건 문장: 조건("~면/경우/때") + 재검토 표현(재검토/다시 검토·논의/확대/전환/변경/교체...).
# "다음 주 다시 얘기하죠" 같은 단순 일정은 조건절이 없으므로 해당하지 않는다.
_TRIGGER_SENTENCE = re.compile(
    r"(면|경우|때)[\s,]*.*(재검토|재논의|다시\s*(?:검토|논의|보|정하|얘기|이야기)|확대|전환|변경|교체|(?:도입|변경|전환|교체)(?:을|를)?\s*(?:다시\s*)?(?:검토|고려))"
)
CONDITION_ONLY_WARNING = "review condition found without explicit current decision"
UNLINKED_WARNING = "review condition not linked to an extracted decision"
AMBIGUOUS_WARNING = "review condition has multiple candidate decisions (not linked)"


_LEADING_CONNECTIVE = re.compile(r"^(?:그러면|그럼|그렇다면|그러니까)\s*")


def _is_trigger_sentence(text: str) -> bool:
    """조건절과 재검토 표현이 '같은 문장' 안에 있을 때만 재검토 조건 문장이다.
    문장 앞 접속사("그러면")의 '면'은 조건절이 아니다."""
    return any(
        _TRIGGER_SENTENCE.search(_LEADING_CONNECTIVE.sub("", _strip_prefix(x)))
        for x in _sentences(_normalize(text))
    )


def _has_decision_sentence(content: str) -> bool:
    """원문에 재검토 조건이 아닌 문장이 하나라도 있는지 (없으면 현재 결정이 적혀 있을 수 없다)"""
    return any(not _is_trigger_sentence(s) for s in _sentences(content))


# 명시적 미결정 표현 ("오늘 결정하지 맙시다", "아직 확정하지 않았다"). '보류한다' 같은 결정은 포함하지 않는다.
_NOT_DECIDED = re.compile(
    r"(결정|확정)(을|은|는)?\s*(하지\s*(않|말|맙)|안\s*(하|했)|미루|보류)|결론\s*(이\s*)?(없|내지\s*않|안\s*났)|아직\s*(결정|확정)(되지|하지|\s*안|\s*못)"
)

# 이유 표현. 결정 바로 다음 문장에 있을 때만 rationale로 쓴다 (단순 상태 설명을 이유로 추론하지 않음)
_REASON = re.compile(r"(이유|때문|왜냐하면|우선|중요하|위해|므로|니까)")
# 섹션 경계: markdown 제목, "09:25 - 로그 보관" 같은 시간 소제목
_SECTION = re.compile(r"^(#+\s|\d{1,2}:\d{2}\s*(?:[-–]|$))")

# 명시적 최종 결정/정리 표현. 재검토 조건보다 '아래'에 있는 결정 문장은 이 표현이 있을 때만 인정한다.
_FINAL_DECISION = re.compile(
    r"(?:오늘|최종|이번|회의)\s*(?:의\s*)?(?:결정|결론)(?:은|는|사항은)|정리하면|(?:으로|로)\s*확정|기로\s*(?:했|하였|합의|결정)"
)
# 내용 없이 앞 발언을 받는 확정 ("네 그걸로 확정.") → 결정 문장으로 쓰지 않는다 (무엇을 결정했는지 없음)
_BARE_CONFIRM = re.compile(r"^(?:네|예|좋습니다|좋아요|그럼)?[\s,.]*(?:그걸로|이걸로|그렇게|그대로|그거로)\s*확정[.!]*$")
# 정리 표현이 문장 중간에 있으면 그 표현부터를 결정 문장으로 쓴다 ("네 제품은 나중에 정하고, 오늘 결정은 …")
_SUMMARY_START = re.compile(r"(?:오늘|최종|이번|회의)\s*(?:의\s*)?(?:결정|결론)(?:은|는|사항은)|정리하면")


def _summary_decisions_below(units: list[str], i: int) -> list[int]:
    """재검토 조건 줄 i 아래, 같은 섹션 안에서 명시적 최종 결정/정리 표현이 있는 줄 위치.
    다음 섹션(제목, 시간 소제목)을 만나면 멈춘다. 질문/미결정/재검토 조건/내용 없는 확정은 제외."""
    found = []
    for j in range(i + 1, len(units)):
        if _SECTION.match(units[j]):
            break
        text = _strip_prefix(units[j])
        norm = _normalize(text)
        if (
            _FINAL_DECISION.search(norm)
            and not _NOT_DECIDED.search(norm)
            and not _is_trigger_sentence(text)
            and not norm.rstrip().endswith("?")
            and not _BARE_CONFIRM.match(norm)
        ):
            found.append(j)
    return found


def _summary_text(unit: str) -> str:
    """정리 줄 → 결정 문장 (원문 exact span). 화자 접두어를 떼고, 정리 표현이 중간에 있으면 거기서부터."""
    text = _strip_prefix(unit)
    m = _SUMMARY_START.search(text)
    return text[m.start():].strip() if m and m.start() > 0 else text


# 하지 않기로 한 결정의 표현 / title에서 부정 의미를 나타내는 표현
_NEGATIVE_DECISION = re.compile(r"(않|안\s|미도입|미사용|금지|보류|제외|중단|폐기)")
_NEGATIVE_TITLE = re.compile(r"(미도입|미사용|미적용|않|안\s|안함|금지|보류|제외|중단|폐기|없음|불가|X\b)")


def _title_flips_meaning(title: str, decision: str) -> bool:
    return bool(_NEGATIVE_DECISION.search(decision)) and not _NEGATIVE_TITLE.search(title)


TITLE_FALLBACK_WARNING = "title validation failed; fell back to decision text"


def _apply_title_fallback(items: list[dict], content: str, warnings: list[str]) -> int:
    """title은 보조 필드다. 검증에 실패해도 LLM을 다시 부르지 않고 decision 문장을 title로 쓴다 (요약을 지어내지 않음).
    실패 사유: 비어 있음 / decision의 부정 의미가 빠짐 / 원문에 없는 한자(중국어) 혼입. 반환: fallback한 항목 수."""
    count = 0
    for it in items:
        title = it["title"]
        if not title:
            reason = "empty"
        elif _title_flips_meaning(title, it["decision"]):
            reason = "polarity"
        elif foreign_han(title, content):
            reason = "foreign_han"
        else:
            continue
        it["title"] = it["decision"].rstrip(".")
        warnings.append(f"{TITLE_FALLBACK_WARNING} ({reason}): {title[:60]!r} → {it['title'][:60]!r}")
        count += 1
    return count


# ---------------------------------------------------------------- LLM 호출


# ---------------------------------------------------------------- provider
# 모델 호출 경로만 provider별로 다르다. prompt, schema, 검증, fallback은 모든 provider가 같은 코드를 쓴다.
#   local      : llama-swap(llama.cpp) Qwen2.5-7B. 웹 API 기본값 (항상 이것)
#   openrouter : 비교 실험용 (유료). scripts/run_extraction_comparison.py에서 명시적으로 고를 때만

PROVIDERS = ("local", "openrouter")
OPENROUTER_PROFILES = ("baseline", "optimized")


@dataclass(frozen=True)
class OpenRouterOptions:
    """OpenRouter 요청 옵션 (생성/라우팅). prompt, schema, 검증은 profile과 무관하게 같다."""

    profile: str
    reasoning_effort: str | None = None  # body.reasoning.effort
    provider_sort: str | None = None  # body.provider.sort
    require_parameters: bool = True  # body.provider.require_parameters (JSON schema 지원 provider로만)
    stage1_max_tokens: int | None = None  # body.max_tokens (Stage 1)
    stage2_max_tokens: int | None = None  # body.max_tokens (Stage 2, 결정 줄 찾기)


def openrouter_options(profile: str) -> OpenRouterOptions:
    if profile == "baseline":
        # 처음 10개 비교 테스트 때와 같은 요청 (require_parameters만)
        return OpenRouterOptions("baseline")
    if profile == "optimized":
        return OpenRouterOptions(
            "optimized",
            reasoning_effort=OPENROUTER_REASONING_EFFORT or None,
            provider_sort=OPENROUTER_PROVIDER_SORT or None,
            require_parameters=OPENROUTER_REQUIRE_PARAMETERS,
            stage1_max_tokens=OPENROUTER_STAGE1_MAX_TOKENS,
            stage2_max_tokens=OPENROUTER_STAGE2_MAX_TOKENS,
        )
    raise ValueError(f"unknown OpenRouter profile: {profile}")


# 호출 이름 → max_tokens 단계 (결정 줄 찾기는 짧은 답이라 Stage 2 상한을 쓴다)
_TOKEN_STAGE = {"decision_sources": "stage1", "review_trigger": "stage2", "decision_line": "stage2"}


@dataclass
class LLMRoute:
    """한 번의 extract() 동안 쓰는 모델 경로 + 호출 통계. Stage 1/2/보조 호출 모두 같은 route를 쓴다."""

    provider: str
    base_url: str
    model: str
    client: httpx.AsyncClient
    stats: dict = field(default_factory=lambda: {"calls": {}, "retries": {"stage1": 0, "stage2": 0}, "usage": {}})
    options: OpenRouterOptions | None = None  # openrouter일 때만. local 요청에는 어떤 옵션도 넣지 않는다

    @property
    def label(self) -> str:
        return "llama-swap" if self.provider == "local" else "OpenRouter"


def provider_settings(provider: str) -> tuple[str, str, dict]:
    """(base_url, model, headers). API Key는 헤더에만 들어가고 메시지/로그/결과에는 쓰지 않는다."""
    if provider == "local":
        return LLM_BASE_URL, EXTRACTION_MODEL, {}
    if provider == "openrouter":
        if not OPENROUTER_API_KEY or not OPENROUTER_MODEL:
            raise ExtractionUnavailable("OpenRouter 설정이 없습니다 (backend/.env의 OPENROUTER_API_KEY, OPENROUTER_MODEL)")
        return OPENROUTER_BASE_URL, OPENROUTER_MODEL, {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    raise ValueError(f"unknown extraction provider: {provider}")


def _mask(text: str) -> str:
    return text.replace(OPENROUTER_API_KEY, "***") if OPENROUTER_API_KEY else text


def _json_content(content: str) -> dict:
    """응답 본문 → JSON. 일부 provider가 ```json 코드 블록으로 감싸는 경우만 벗긴다 (내용은 바꾸지 않음)."""
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return json.loads(fenced.group(1) if fenced else text)


async def _call_llm(llm: LLMRoute, messages: list[dict], schema: dict, name: str, temperature: float) -> dict:
    body = {
        "model": llm.model,
        "temperature": temperature,
        "messages": messages,
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
    }
    cap = None
    if llm.provider == "openrouter":
        opts = llm.options or openrouter_options("baseline")
        provider = {}
        if opts.require_parameters:
            # 같은 JSON schema 강제 조건으로 비교하기 위해 response_format을 지원하는 provider로만 라우팅한다
            provider["require_parameters"] = True
        if opts.provider_sort:
            provider["sort"] = opts.provider_sort
        if provider:
            body["provider"] = provider
        if opts.reasoning_effort:
            body["reasoning"] = {"effort": opts.reasoning_effort}
        cap = opts.stage1_max_tokens if _TOKEN_STAGE.get(name) == "stage1" else opts.stage2_max_tokens
        if cap:
            body["max_tokens"] = cap
    llm.stats["calls"][name] = llm.stats["calls"].get(name, 0) + 1
    try:
        res = await llm.client.post(f"{llm.base_url}/chat/completions", json=body)
    except httpx.TransportError as e:
        raise ExtractionUnavailable(f"{llm.label}({llm.base_url})에 연결할 수 없습니다: {type(e).__name__}") from e
    if res.status_code != 200:
        raise ExtractionError(_mask(f"LLM HTTP {res.status_code}: {res.text[:300]}"))
    try:
        data = res.json()
        _record_usage(llm.stats, data)
        choice = data["choices"][0]
    except (KeyError, ValueError, TypeError, AttributeError, IndexError) as e:
        raise ExtractionError(f"LLM 응답을 해석할 수 없습니다: {e}") from e
    finish = choice.get("finish_reason") or "unknown"
    reasons = llm.stats.setdefault("finish_reasons", {})
    reasons[finish] = reasons.get(finish, 0) + 1
    if finish == "length":
        # 출력 상한에 걸려 잘림 (reasoning token 포함). 정상적인 추출 실패와 구분해서 기록한다
        llm.stats.setdefault("truncated", []).append(name)
        raise ExtractionError(f"출력이 max_tokens({cap or '기본값'})에서 잘림 ({name})")
    try:
        return _json_content(choice["message"]["content"])
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        llm.stats.setdefault("invalid_json", []).append(name)
        raise ExtractionError(f"LLM 응답을 해석할 수 없습니다: {e}") from e


def _record_usage(stats: dict, data: dict) -> None:
    """응답에 있는 usage 값만 합산한다 (가격을 계산하지 않음). 중첩 값은 'completion_tokens_details.reasoning_tokens'처럼 기록.
    응답에 provider(실제로 처리한 곳)가 있으면 함께 센다."""
    usage = stats["usage"]

    def add(key: str, value) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            usage[key] = usage.get(key, 0) + value

    for k, v in (data.get("usage") or {}).items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                add(f"{k}.{k2}", v2)
        else:
            add(k, v)
    served = data.get("provider")
    if isinstance(served, str) and served:
        by = stats.setdefault("served_by", {})
        by[served] = by.get(served, 0) + 1


# ---------------------------------------------------------------- Stage 1: 원문 탐지


def _stage1_item(raw: dict, content: str, units: list[str], warnings: list[str]) -> dict:
    """Stage 1 결과 정리. decision/rationale/review_condition/evidence를 원문 exact span 기준으로 맞춘다."""
    label = _clean(raw.get("title") or raw.get("decision") or "")

    evidence: list[str] = []
    for e in raw.get("evidence") or []:
        if not (isinstance(e, str) and e.strip()):
            continue
        spans = _exact_spans(_clean(e), units)
        if not spans:  # 원문에 없는 근거는 저장하지 않는다
            warnings.append(f"evidence not found in source (dropped) [{label}]: {_clean(e)[:120]}")
        evidence += [x for x in spans if x not in evidence]

    condition = _clean(raw.get("review_condition") or "")
    if condition:
        spans = _exact_spans(condition, units)
        if len(spans) == 1:  # 원문 문장 전체로 맞춘다 (여러 줄 조건, 모델이 일부만 복사한 경우)
            condition = _strip_prefix(spans[0])
        elif spans:
            triggers = [x for x in spans if _is_trigger_sentence(x)]
            condition = _strip_prefix(triggers[0]) if len(triggers) == 1 else condition

    rationale = _clean(raw.get("rationale") or "")
    if rationale and not evidence_in_source(rationale, content):
        # 원문 exact span이 아니면 (여러 문장을 이어 붙였거나 다시 쓴 경우) 원문에 있는 이유 문장만 남긴다
        pieces = [
            _strip_prefix(x) for x in _exact_spans(rationale, units)
            if not _is_trigger_sentence(x) and not _overlaps(x, condition)
        ]
        reasons = [x for x in pieces if _REASON.search(x)] or pieces
        if not reasons:
            warnings.append(f"rationale not found in source (dropped) [{label}]: {rationale[:120]}")
        rationale = reasons[0] if reasons else ""

    return {
        "topic": re.sub(r"[^a-z0-9_-]", "", (raw.get("topic") or "").lower()) or "general",
        # 길이로 자르지 않는다 (말이 중간에 끊긴 채 저장되므로). 화면에서 넘칠 때만 말줄임 처리한다.
        "title": _clean(raw.get("title") or ""),
        "decision": _clean(raw.get("decision") or ""),
        "rationale": rationale,
        "assumptions": [_clean(a) for a in raw.get("assumptions") or [] if a.strip()],
        "evidence": evidence,
        "review_condition": condition,
        "status": raw.get("decision_status"),
    }


def _is_condition_only(item: dict, content: str) -> bool:
    """현재 결정 없이 재검토 조건만 근거로 만든 항목인지.

    원문 확인을 모델 표시보다 우선한다 (7B 모델이 조건이 붙은 결정에도 no_current_decision을 붙이는 경우가 있음).
    - 원문에 재검토 조건이 아닌 문장이 하나도 없으면 → True
    - decision 또는 evidence의 '재검토 조건이 아닌 문장'이 원문에 그대로 있으면 → 명시된 결정 (False)
    - evidence가 모두 원문 그대로인데 재검토 조건 문장뿐이면 → True
    - 그 외는 모델 표시를 따른다
    """
    if not _has_decision_sentence(content):
        return True
    cond = item["review_condition"]
    grounded = [
        s for s in [item["decision"], *item["evidence"]]
        if not _is_trigger_sentence(s) and not _overlaps(s, cond) and evidence_in_source(s, content)
    ]
    if grounded:
        return False
    if item["evidence"]:  # evidence는 이미 원문 exact span만 남아 있다
        return True
    return item["status"] == "no_current_decision"


def _anchor(item: dict, units: list[str]) -> int | None:
    """결정 문장이 있는 원문 줄 위치 (decision 원문 → 재검토 조건이 아닌 첫 evidence 순)"""
    for text in [item["decision"], *[e for e in item["evidence"] if not _is_trigger_sentence(e)]]:
        key = _match_key(_strip_prefix(text))
        hits = [i for i, u in enumerate(units) if len(key) >= _MIN_SPAN and key in _match_key(u)]
        if len(hits) == 1:
            return hits[0]
    return None


def _fill_rationale(item: dict, units: list[str]) -> None:
    """rationale이 비었으면: 결정 바로 다음 문장에 이유 표현이 있을 때만 그 원문 문장을 쓴다 (추론하지 않음)."""
    if item["rationale"]:
        return
    i = _anchor(item, units)
    if i is None:
        return
    candidates = []
    sents = _sentences(units[i])
    key = _match_key(_strip_prefix(item["decision"]))
    after = [k for k, x in enumerate(sents) if key and key in _match_key(x)]
    if after and after[0] + 1 < len(sents):  # 같은 줄 안의 다음 문장
        candidates.append(sents[after[0] + 1])
    if i + 1 < len(units) and not _SECTION.match(units[i + 1]):
        candidates.append(units[i + 1])
    for c in candidates:
        if _REASON.search(c) and not _is_trigger_sentence(c):
            item["rationale"] = _strip_prefix(c)
            return


def _owned(item: dict, unit: str) -> bool:
    return any(_overlaps(unit, x) for x in [item["decision"], item["rationale"], *item["evidence"]] if x and not _is_trigger_sentence(x))


def _link_conditions(items: list[dict], dropped: list[dict], content: str, warnings: list[str]) -> None:
    """결정 ↔ 재검토 조건 연결, rationale 보완 (결정적 코드). 연결하지 못한 재검토 조건은 warning으로 남긴다.

    1) review_condition이 비었는데 evidence에 재검토 조건 문장이 하나 있으면 그 문장을 쓴다
    2) 어느 항목에도 연결되지 않은 원문 재검토 조건 문장은, 같은 섹션 안에서 위로 가장 가까운
       결정 줄(결정/이유/근거 문장)의 항목에 연결한다. 그 줄의 항목이 하나이고 아직 조건이 없을 때만 연결하고,
       아니면 억지로 연결하지 않고 warning을 남긴다
    3) rationale이 비었으면 결정 바로 다음 문장의 명시적 이유를 쓴다
    """
    units = _units(content)
    for item in items:
        if not item["review_condition"]:
            found = [e for e in item["evidence"] if _is_trigger_sentence(e)]
            if len(found) == 1:
                item["review_condition"] = _strip_prefix(found[0])
        _fill_rationale(item, units)

    for i, unit in enumerate(units):
        if not _is_trigger_sentence(unit) or any(_overlaps(unit, it["review_condition"]) for it in items):
            continue
        linked, reason = False, UNLINKED_WARNING if _has_decision_sentence(content) else CONDITION_ONLY_WARNING
        found_above = False
        for j in range(i - 1, -1, -1):
            if _SECTION.match(units[j]):
                break
            owners = [it for it in items if _owned(it, units[j])]
            if not owners:
                continue
            found_above = True
            if len(owners) == 1 and not owners[0]["review_condition"]:
                owners[0]["review_condition"] = _strip_prefix(unit)
                linked = True
            elif len(owners) > 1:
                reason = AMBIGUOUS_WARNING
            break
        if not found_above:
            # 위쪽에 결정이 없을 때만: 같은 섹션 아래쪽의 명시적 정리 문장이 정확히 하나이고, 그 문장의 항목이 하나일 때 연결
            below = _summary_decisions_below(units, i)
            owners = [it for j in below for it in items if _owned(it, units[j])]
            if len(below) == 1 and len(owners) == 1 and not owners[0]["review_condition"]:
                owners[0]["review_condition"] = _strip_prefix(unit)
                linked = True
            elif len(below) > 1:
                reason = AMBIGUOUS_WARNING
        if not linked and not any(_overlaps(unit, w) for w in warnings):
            warnings.append(f"{reason}: {unit}")

    # 버린 항목(현재 결정 없음)의 조건 중 재검토 조건 문장이면서 다른 결정에 연결되지 않은 것만 안내한다
    # ("다음 주 다시 얘기하죠" 같은 단순 일정은 안내하지 않는다)
    for d in dropped:
        text = d["review_condition"] or " ".join(d["evidence"])
        if not _is_trigger_sentence(text):
            continue
        if not any(_overlaps(text, it["review_condition"]) for it in items) and not any(_overlaps(text, w) for w in warnings):
            warnings.append(f"{CONDITION_ONLY_WARNING}: {text}")

    for item in items:  # 결정/이유/재검토 조건 문장도 근거에 포함 (원문 exact span일 때만)
        for text in (item["decision"], item["rationale"], item["review_condition"]):
            if not text or any(_overlaps(text, e) for e in item["evidence"]):
                continue
            span = _locate(text, units)
            if span:
                item["evidence"].append(span)


def _leaked_han(items: list[dict], content: str) -> set[str]:
    """핵심 필드에서 원문에 없는 한자 (중국어 혼입). title은 보조 필드라 여기서 보지 않는다 (_apply_title_fallback)"""
    texts = []
    for d in items:
        texts += [d["decision"], d["rationale"], *d["evidence"], *d["assumptions"], d["review_condition"]]
    return foreign_han(" ".join(texts), content)


async def _stage1(llm: LLMRoute, content: str) -> tuple[list[dict], list[dict], list[str]]:
    """(확정 항목, 현재 결정이 없다고 본 항목, warnings)"""
    messages = [{"role": "system", "content": STAGE1_PROMPT}, {"role": "user", "content": content}]
    leaked: set[str] = set()
    units = _units(content)
    for attempt in range(MAX_ATTEMPTS):
        # 첫 시도는 결정적(temperature 0). 같은 입력이면 같은 결과가 나오므로 재시도 때만 약간 올린다.
        if attempt:
            llm.stats["retries"]["stage1"] += 1
        parsed = await _call_llm(llm, messages, _stage1_schema(), "decision_sources", 0 if attempt == 0 else 0.3)
        try:
            raw_items = [r for r in parsed["items"] if isinstance(r, dict)]
        except (KeyError, TypeError) as e:
            raise ExtractionError(f"LLM 응답을 해석할 수 없습니다: {e}") from e
        # 확정되지 않은 항목(not_confirmed)은 후보로 만들지 않는다
        warnings: list[str] = []
        items = [_stage1_item(r, content, units, warnings) for r in raw_items if r.get("decision_status") != "not_confirmed"]
        items = [it for it in items if it["decision"]]
        # "오늘 결정하지 않는다" 같은 명시적 미결정 문장은 모델 표시와 관계없이 결정이 아니다
        for it in [it for it in items if _NOT_DECIDED.search(_normalize(it["decision"]))]:
            warnings.append(f"not a confirmed decision (dropped): {it['decision'][:120]}")
            items.remove(it)
        # 현재 결정 없이 재검토 조건만 있는 항목은 후보로 만들지 않는다 (결정을 추론하지 않음)
        dropped = [it for it in items if _is_condition_only(it, content)]
        items = [it for it in items if it not in dropped]
        # 재시도는 핵심 필드 문제(중국어 혼입)일 때만. title 문제는 재시도하지 않고 아래에서 decision 문장으로 대체한다
        leaked = _leaked_han(items, content)
        if not leaked:
            fallbacks = _apply_title_fallback(items, content, warnings)
            if fallbacks:  # retry와 구분되는 통계 (escalation 판단에 쓰지 않는 보조 신호)
                llm.stats["title_fallbacks"] = llm.stats.get("title_fallbacks", 0) + fallbacks
            return items, dropped, warnings
        messages = [
            *messages[:2],
            {
                "role": "user",
                "content": f"이전 응답에 중국어 한자({''.join(sorted(leaked))})가 섞였다. 중국어 없이 한국어와 원문 표기만 사용하라. 다시 찾아라.",
            },
        ]
    raise ExtractionError(
        f"모델이 {MAX_ATTEMPTS}회 모두 중국어({''.join(sorted(leaked))})가 섞인 결과를 반환해 후보를 저장하지 않았습니다. 다시 시도하세요."
    )


def _rescue_by_summary(item: dict, items: list[dict], units: list[str], warnings: list[str]) -> bool | None:
    """모델이 결정 문장을 다시 써서 근거가 재검토 조건뿐인 항목을, 같은 섹션의 원문 결정 문장으로 되살린다 (결정적 코드).

    1) 조건 줄 위쪽(같은 섹션)에 이미 다른 항목의 결정이 있으면 되살리지 않는다 (그 조건은 그 결정에 연결된다)
    2) 위쪽에 없을 때만 아래쪽에서 명시적 최종 결정/정리 문장을 찾는다. 정확히 하나일 때만 채택하고,
       decision은 그 원문 exact span으로 바꾼다 (모델이 다시 쓴 문장은 쓰지 않는다)
    반환: True = 되살림, False = 후보 없음, None = 후보가 둘 이상이라 연결하지 않음 (warning)
    """
    hits = [i for i, u in enumerate(units) if item["review_condition"] and _overlaps(u, item["review_condition"])]
    if len(hits) != 1:
        return False
    i = hits[0]
    for j in range(i - 1, -1, -1):
        if _SECTION.match(units[j]):
            break
        if any(_owned(it, units[j]) for it in items):
            return False
    below = _summary_decisions_below(units, i)
    if not below:
        return False
    if len(below) > 1:
        warnings.append(f"{AMBIGUOUS_WARNING}: {units[i]}")
        return None
    unit = units[below[0]]
    item["decision"] = _summary_text(unit)
    item["evidence"].insert(0, unit)
    item["status"] = "confirmed"
    return True


ANCHOR_PROMPT = """원문은 줄마다 L번호가 붙어 있다. 주어진 결정이 적힌 줄의 번호를 고른다.
합의나 결정 표현("~하기로 한다", "~하는 걸로 합시다", "~로 정하죠", "~을 결정으로 적자", "오늘 결정은 ~")이 있는 줄을 고른다.
제안이나 질문("~어떨까요?"), 현황 설명("지금은 ~하고 있어요"), 재검토 조건 줄은 고르지 않는다.
그런 줄이 없으면 line은 0이다."""


def _has_decision_evidence(item: dict) -> bool:
    """evidence에 결정 문장(재검토 조건/이유가 아닌 원문 문장)이 있는지"""
    return any(
        not _is_trigger_sentence(e) and not (item["rationale"] and _overlaps(e, item["rationale"]))
        for e in item["evidence"]
    ) or evidence_in_source(item["decision"], "\n".join(item["evidence"]))


async def _anchor_decision(llm: LLMRoute, item: dict, content: str, units: list[str]) -> bool:
    """decision이 원문 그대로가 아니고 근거에도 결정 문장이 없을 때, 원문의 결정 줄만 다시 찾는다 (작은 호출 1회).
    모델은 줄 번호만 고르고 근거 문자열은 원문 줄을 그대로 쓴다. 질문/재검토 조건/미결정 줄이면 채택하지 않는다.
    decision 문구는 바꾸지 않는다."""
    # 7B 모델은 "그대로 복사"를 요청해도 문장을 고쳐 쓰므로, 줄 번호만 고르게 하고 원문 줄은 코드가 가져온다
    numbered = "\n".join(f"L{i + 1}: {u}" for i, u in enumerate(units))
    messages = [
        {"role": "system", "content": ANCHOR_PROMPT},
        {"role": "user", "content": f"원문:\n{numbered}\n\n결정: {item['decision']}\n재검토 조건: {item['review_condition'] or '(없음)'}"},
    ]
    schema = {
        "type": "object",
        "properties": {"line": {"type": "integer", "minimum": 0, "maximum": len(units)}},
        "required": ["line"],
    }
    try:
        parsed = await _call_llm(llm, messages, schema, "decision_line", 0)
    except (ExtractionError, ExtractionUnavailable):
        return False
    line = parsed.get("line")
    if not isinstance(line, int) or not 1 <= line <= len(units):
        return False
    span = units[line - 1]
    if _SECTION.match(span) or _is_trigger_sentence(span) or _NOT_DECIDED.search(_normalize(span)) or _normalize(span).endswith("?"):
        return False
    item["evidence"].insert(0, span)
    return True


# ---------------------------------------------------------------- Backend 검증: metric / operator / date / duration


def _alias_key(text: str) -> str:
    return re.sub(r"[\s_()\-]", "", text.lower())


def map_metric_key(raw_metric: str | None, unit: str | None, allowed_metrics: list[str]) -> str | None:
    """원문 지표 표현 → 지표 키 (allowed_metrics 안에서). alias 완전 일치 + 단위 호환일 때만. 아니면 None (추측하지 않음).
    alias 목록은 Canonical Metric Registry(metric_registry.py) 하나에서 관리한다."""
    return metric_registry.canonicalize(raw_metric, unit, allowed_metrics)


# 비교 표현 → operator. "넘지 않" 같은 부정 표현을 먼저 확인한다.
_OPERATOR_WORDS = [
    ("넘지 않", "<="), ("넘지않", "<="), ("이상", ">="), ("이하", "<="), ("초과", ">"), ("넘", ">"), ("미만", "<"),
    ("같으면", "=="), ("와 같", "=="), ("과 같", "=="), ("아니면", "!="), ("다르면", "!="),
]


def _operators_in(text: str) -> set[str]:
    found, rest = set(), text
    for word, op in _OPERATOR_WORDS:
        if word in rest:
            found.add(op)
            rest = rest.replace(word, " ")
    return found


# 고유어 수사 + 단위 ("다섯 번", "두 건", "세 대"). 단위가 붙은 경우만 숫자로 본다 ("네 맞아요"의 "네"는 아님)
_KOREAN_NUMERALS = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
                    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
_KOREAN_NUMBER = re.compile(r"(?<![가-힣])(하나|다섯|여섯|일곱|여덟|아홉|한|두|둘|세|셋|네|넷|열)\s*(?=번|회|건|명|대|개|차례|달|주)")


# 한자어 단위 수 ("천 명", "2천 명", "삼천", "5만"). 말로 한 회의 전사(STT)에서 숫자가 글자로 나오는 경우.
# 단어 일부("백업", "만약", "천천히", "에러율만")는 숫자가 아니다: 앞이 한글/숫자가 아니고 뒤가 단위/조사/공백일 때만
_SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_MAGNITUDES = {"백": 100, "천": 1000, "만": 10000}
_MAGNITUDE_NUMBER = re.compile(
    r"(?<![가-힣\d.,])(?:(\d+(?:\.\d+)?)|([일이삼사오육칠팔구]))?\s?([백천만])"
    r"(?=\s|$|명|대|건|회|번|개|원|[을를이가은는도])"
)


def _number_spans(text: str) -> list[tuple[float, int]]:
    """원문의 숫자와 끝 위치. "1,000" → 1000, "다섯 번" → 5, "천 명" → 1000, "2만" → 20000"""
    spans = [(float(m.group().replace(",", "")), m.end()) for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)]
    spans += [(float(_KOREAN_NUMERALS[m.group(1)]), m.end()) for m in _KOREAN_NUMBER.finditer(text)]
    for m in _MAGNITUDE_NUMBER.finditer(text):
        lead = float(m.group(1)) if m.group(1) else _SINO_DIGITS.get(m.group(2) or "", 1)
        spans.append((lead * _MAGNITUDES[m.group(3)], m.end()))
    return spans


def _operator_near(condition: str, threshold: float) -> str | None:
    """임계값 숫자 바로 뒤(단위 포함 12자 이내)의 비교 표현. 예) "800ms를 초과한" → ">" """
    ops = set()
    for value, end in _number_spans(condition):
        if value == float(threshold):
            ops |= _operators_in(condition[end:end + 12])
    return ops.pop() if len(ops) == 1 else None


_DURATION_WORDS = {"minute": "분", "hour": "시간", "day": "일", "week": "주", "month": "(?:개월|달)"}
# "3일 연속", "7일 동안", "2시간 이상 지속", "3일간" — 기간 뒤에 지속을 뜻하는 표현이 있어야 duration이다 ("30일 보관"은 아님)
_DURATION_CONTEXT = r"\s*(?:이상\s*)?(?:연속|동안|간|째|넘게|지속|이내|내내)"
_CALENDAR_DATE = re.compile(r"(\d{4})\s*(?:-|\.|/|년)\s*(\d{1,2})\s*(?:-|\.|/|월)\s*(\d{1,2})")


def _calendar_dates(text: str) -> set[str]:
    return {f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in _CALENDAR_DATE.findall(text)}


def _check_leaf(c: dict, condition: str, allowed_metrics: list[str], single_metric: bool) -> tuple[dict, list[str], list[str]]:
    """조건 하나 검증. (교정된 조건, problems=재시도 사유, notes=warning)"""
    problems, notes = [], []
    c = dict(c)
    kind = c.get("type")
    if kind == "metric" and (map_metric_key(c.get("raw_metric"), c.get("unit"), metric_registry.all_names()) is None
                             and metric_registry.is_subjective(c.get("raw_metric"), c.get("raw_condition"))):
        # 수치처럼 보여도 객관적으로 측정할 지표가 아니다 (예: "고객 불만", "운영 부담") → metric으로 억지 변환하지 않는다
        text = c.get("raw_condition") or condition
        notes.append(f"측정 가능한 지표가 아니어서 사람 확인 조건(manual)으로 분류: {c.get('raw_metric')}")
        return {"type": "manual", "description": text, "raw_condition": text, "_demoted": True}, problems, notes
    if kind == "metric":
        threshold = c.get("threshold")
        if not c.get("raw_metric"):
            problems.append("metric의 raw_metric이 비어 있다")
        if not isinstance(threshold, (int, float)) or not any(v == float(threshold) for v, _ in _number_spans(condition)):
            problems.append(f"metric threshold {threshold}가 원문 조건에 없다. 원문의 숫자를 그대로 써라")
        else:
            if float(threshold).is_integer():
                c["threshold"] = int(threshold)
            op = _operator_near(condition, threshold)
            if op is None and single_metric:
                ops = _operators_in(condition)
                op = ops.pop() if len(ops) == 1 else None
            if op is None:
                notes.append(f"operator를 원문에서 확인하지 못함 ({c.get('raw_metric')} {c.get('operator')} {c['threshold']})")
            elif op != c.get("operator"):
                notes.append(f"operator 교정: {c.get('raw_metric')} {c.get('operator')} → {op} (원문 표현 기준)")
                c["operator"] = op
        c["unit"] = c.get("unit") or None
        # 지표 매핑은 backend가 한다 (LLM 값은 쓰지 않음)
        c["metric_key"] = map_metric_key(c.get("raw_metric"), c.get("unit"), allowed_metrics)
        # 연결 여부와 무관한 canonical 지표 (registry에 있으면 NEEDS_MAPPING이라도 이름을 통일한다)
        c["canonical_metric"] = c["metric_key"] or metric_registry.canonicalize(c.get("raw_metric"), c.get("unit"))
    elif kind == "duration":
        value, unit = c.get("value"), c.get("unit")
        if isinstance(value, float) and value.is_integer():
            c["value"] = value = int(value)
        word = _DURATION_WORDS.get(unit)
        if not word or not re.search(rf"(?<![\d.]){value}\s*{word}{_DURATION_CONTEXT}", condition):
            problems.append(
                f"duration {value} {unit}은 원문에 '{value}일 연속', '{value}일 동안' 같은 지속 기간 표현이 없다. "
                "기간이 사건 설명의 일부(예: 30일 보관 요구)이면 event description에 포함하라"
            )
        else:
            if "연속" in condition:
                c["mode"] = "consecutive"
            elif re.search(r"동안|이내", condition):
                c["mode"] = "within"
    elif kind == "date":
        dates = _calendar_dates(condition)
        if not dates:
            problems.append("원문 조건에 달력 날짜(예: 2027-01-01, 2027년 1월 1일)가 없으므로 date type을 쓰면 안 된다")
        elif c.get("date") not in dates:
            problems.append(f"date {c.get('date')}가 원문 날짜({', '.join(sorted(dates))})와 다르다")
    elif kind in ("event", "manual"):
        if not (c.get("description") or "").strip():
            problems.append(f"{kind}의 description이 비어 있다")
    else:
        problems.append(f"알 수 없는 type {kind}")
    return c, problems, notes


_OR_WORDS = re.compile(r"거나|또는|혹은")
# 조건절 끝 ("~하면", "~경우", "~때", "~시"). 그 뒤는 재검토 action ("SAML 또는 OIDC 기반 로그인을 재검토한다")
# "다시 검토"의 '시'는 조건절 끝이 아니다.
_CONDITION_END = re.compile(r"(?:면|경우(?:에는|에)?|때(?:에는|에)?|(?<!다)시)(?=[\s,])")


def _condition_clause(condition: str) -> str:
    """재검토 조건 문장에서 조건절만 (마지막 조건 어미까지). 조건 어미가 없으면 문장 전체."""
    ends = list(_CONDITION_END.finditer(condition))
    return condition[: ends[-1].end()] if ends else condition


# 조건절 안의 AND 신호 ("~이면서", "동시에", "둘 다"). validator가 강제하지 않는 복합 조건이라 escalation 판단에만 쓴다
_AND_WORDS = re.compile(r"동시에|둘\s*다|모두|이면서|하면서|\bAND\b")


def _compound_signal(condition: str) -> bool:
    """조건절(재검토 action 제외) 안에 복합 조건 표현이 있는지. action 안의 '또는'은 보지 않는다."""
    clause = _condition_clause(condition)
    return bool(_OR_WORDS.search(clause) or _AND_WORDS.search(clause))


def _leaf_types(t: dict) -> list[str]:
    return [c.get("type") for c in t["conditions"]] if t.get("type") == "compound" else [t.get("type")]


def _missing_parts(t: dict, condition: str) -> list[str]:
    """원문 조건에 있는 요소가 구조화 결과에서 빠졌는지 (결정적 확인). 빠졌으면 재시도 사유."""
    problems = []
    duration = re.search(rf"\d+\s*(?:분|시간|일|주|개월|달){_DURATION_CONTEXT}", condition)
    if duration and "duration" not in _leaf_types(t):
        problems.append(f"원문의 지속 기간 조건 '{duration.group()}'이 빠졌다. compound AND로 metric과 duration을 함께 넣어라")
    # 원문의 숫자 비교("2건 이상", "800ms를 초과")는 metric threshold이거나 지표 이름(raw_metric) 안에 있어야 한다.
    # 사건 설명(event/manual)에 숫자 비교를 넣거나 빠뜨리면 자동 판정할 수 없으므로 다시 구조화한다.
    leaves = t["conditions"] if t.get("type") == "compound" else [t]
    metric_values = {float(c["threshold"]) for c in leaves if c.get("type") == "metric" and isinstance(c.get("threshold"), (int, float))}
    duration_values = {float(c["value"]) for c in leaves if c.get("type") == "duration" and isinstance(c.get("value"), (int, float))}
    names = " ".join(c.get("raw_metric") or "" for c in leaves if c.get("type") == "metric")
    texts = " ".join(c.get("description") or "" for c in leaves if c.get("type") in ("event", "manual") and not c.get("_demoted"))
    # validator가 주관적 조건으로 판단해 manual로 내린 조건의 숫자는 의도적으로 metric이 아니다
    demoted = " ".join(c.get("description") or "" for c in leaves if c.get("_demoted"))
    for value, end in _number_spans(condition):
        if not _operators_in(condition[end:end + 12]):
            continue
        shown = f"{value:g}"
        tail = condition[end:end + 12]
        cut = min((tail.find(w) + len(w) for w, _ in _OPERATOR_WORDS if w in tail), default=len(tail))
        phrase = condition[:end].split()[-1] + tail[:cut]  # 예) "2건 이상"
        if value in metric_values or value in duration_values or shown in names or shown in demoted:
            continue
        if shown in texts:
            problems.append(f"숫자 비교 '{phrase.strip()}'를 event/manual 설명에 넣었다. metric(operator, threshold)으로 나눠라")
        else:
            problems.append(f"원문의 숫자 비교 '{phrase.strip()}'가 빠졌다. metric으로 넣어라")
    # '~거나/또는'이 조건절 안에 있을 때만 OR 조건이다. 재검토 action 안의 선택지("SAML 또는 OIDC")는 조건이 아니다.
    if _OR_WORDS.search(_condition_clause(condition)) and not (t.get("type") == "compound" and t.get("logic") == "OR"):
        problems.append("원문에 '~거나/또는' 조건이 있다. compound OR로 각 조건을 나눠 넣어라")
    return problems


def validate_trigger(t: dict, condition: str, allowed_metrics: list[str], content: str = "") -> tuple[dict | None, list[str], list[str]]:
    """Stage 2 결과 검증 (결정적 코드). (V2 trigger dict 또는 None, problems, notes)
    problems가 있으면 저장하지 않는다 (재시도 → manual fallback)."""
    if not isinstance(t, dict):
        return None, ["trigger가 비어 있다"], []
    t = _clean_values(t)
    if t.get("type") == "compound":
        conds = [c for c in t.get("conditions") or [] if isinstance(c, dict)]
        if not conds:
            return None, ["compound의 conditions가 비어 있다"], []
        if len(conds) == 1:  # 조건이 하나뿐이면 compound로 감쌀 필요가 없다
            t = conds[0]
    problems, notes = [], []
    if t.get("type") == "compound":
        conds = t["conditions"]
        if any(c.get("type") == "compound" for c in conds):
            return None, ["compound 안에 compound를 넣지 마라"], []
        single = sum(c.get("type") == "metric" for c in conds) == 1
        checked = []
        for c in conds:
            c, p, n = _check_leaf(c, condition, allowed_metrics, single)
            checked.append(c)
            problems += p
            notes += n
        t = {"type": "compound", "logic": t.get("logic"), "conditions": checked}
        if t["logic"] == "OR" and "duration" in _leaf_types(t):
            problems.append("duration은 OR 조건이 될 수 없다. 지속 기간이면 AND로 metric과 묶고, 집계 기간이면 raw_metric에 포함하라")
    else:
        t, problems, notes = _check_leaf(t, condition, allowed_metrics, True)
        if t.get("type") == "duration":
            problems.append("duration만으로는 재검토 조건이 될 수 없다. 무엇이 지속되는지 metric과 함께 compound AND로 써라")
    problems += _missing_parts(t, condition)
    if content:
        leaked = foreign_han(" ".join(_strings(t)), content)
        if leaked:
            problems.append(f"중국어 한자({''.join(sorted(leaked))})가 섞였다. 한국어만 써라")
    if problems:
        return None, problems, notes
    t["raw_condition"] = condition
    trigger = normalize_review_trigger(t)
    return (trigger.model_dump(mode="json") if trigger is not None else None), [], notes


def _manual(condition: str) -> dict:
    return ManualTrigger(description=condition, raw_condition=condition).model_dump(mode="json")


# ---------------------------------------------------------------- Stage 2: review condition 구조화


async def _stage2(llm: LLMRoute, item: dict, allowed_metrics: list[str], content: str) -> tuple[dict, list[str]]:
    """review condition 하나 → V2 trigger. 실패해도 예외를 내지 않고 원문을 manual로 보존한다 (후보는 유지)."""
    condition = item["review_condition"]
    messages = [
        {"role": "system", "content": STAGE2_PROMPT},
        {"role": "user", "content": f"decision: {item['decision']}\nreview_condition: {condition}"},
    ]
    problems: list[str] = []
    # 조건 하나의 구조화 결과 (escalation 판단용). warning 문자열을 해석하지 않도록 구조로 남긴다
    outcome = {"condition": condition[:200], "compound_signal": _compound_signal(condition), "attempts": 0}
    llm.stats.setdefault("stage2_outcomes", []).append(outcome)
    cause = "validation_failed"
    for attempt in range(STAGE2_ATTEMPTS):
        outcome["attempts"] = attempt + 1
        try:
            if attempt:
                llm.stats["retries"]["stage2"] += 1
            parsed = await _call_llm(llm, messages, _stage2_schema(), "review_trigger", 0 if attempt == 0 else 0.2)
        except (ExtractionError, ExtractionUnavailable) as e:
            problems, cause = [str(e)], "llm_error"
            break
        trigger, problems, notes = validate_trigger(parsed.get("trigger"), condition, allowed_metrics, content)
        if trigger is not None:
            # 모델이 직접 manual을 고른 경우(사람이 판단할 의미적 조건)도 여기서 status=ok, type=manual이다
            outcome.update(status="ok", type=trigger["type"])
            return trigger, [f"{item['title'] or item['decision']}: {n}" for n in notes]
        messages = [
            *messages[:2],
            {"role": "user", "content": "이전 결과의 문제: " + " / ".join(problems) + ". review_condition 문장만 보고 다시 구조화하라."},
        ]
    # 구조화 실패 → 원문을 manual로 보존 (모델 실패가 원인. 의미적 manual과 구분)
    outcome.update(status="manual_fallback", type="manual", cause=cause, problems=[x[:200] for x in problems[:3]])
    return _manual(condition), [f"{item['title'] or item['decision']}: 재검토 조건 구조화 실패 → manual로 보존 ({'; '.join(problems)[:300]})"]


# ---------------------------------------------------------------- pipeline


async def extract(
    content: str,
    allowed_metrics: list[str],
    provider: str = "local",
    stats: dict | None = None,
    profile: str | None = None,
) -> tuple[list[dict], list[str]]:
    """(후보 초안 목록, warnings). warnings는 저장하지 않은 항목/자동 교정/구조화 실패 안내 (DB에는 저장하지 않음)

    provider: "local"(기본, 웹 API는 항상 이것) | "openrouter"(비교 실험 script 전용). Stage 1/2/보조 호출 모두 같은 provider.
    stats: 넘기면 호출 수/재시도/usage를 채워 준다 (비교 script용)
    profile: openrouter 요청 옵션 묶음 "baseline"(기본) | "optimized". local에는 적용되지 않는다
    """
    base_url, model, headers = provider_settings(provider)
    async with httpx.AsyncClient(timeout=EXTRACTION_TIMEOUT, headers=headers) as client:
        options = openrouter_options(profile or "baseline") if provider == "openrouter" else None
        llm = LLMRoute(provider, base_url, model, client, options=options)
        if stats is not None:
            stats.update(provider=provider, model=model, options=asdict(options) if options else None)
            llm.stats = stats.setdefault("llm", llm.stats)
        items, dropped, warnings = await _stage1(llm, content)
        units = _units(content)
        # 결정 문장 근거 보완: 모델이 결정을 다시 써서 원문 결정 문장이 근거에 없는 경우만 (원문 exact span일 때만 채택)
        if _has_decision_sentence(content):
            for item in [it for it in dropped if it["status"] == "confirmed"]:
                # 먼저 코드로: 재검토 조건 아래 같은 섹션의 명시적 정리 문장 (모델 호출 없음)
                rescued = _rescue_by_summary(item, items, units, warnings)
                if rescued or rescued is None:  # None = 모호해서 연결하지 않음 (모델로도 찾지 않는다)
                    if rescued:
                        dropped.remove(item)
                        items.append(item)
                    continue
                if await _anchor_decision(llm, item, content, units):
                    dropped.remove(item)  # 원문에 결정 문장이 있으므로 '현재 결정 없음'이 아니다
                    items.append(item)
            for item in items:
                if not evidence_in_source(item["decision"], content) and not _has_decision_evidence(item):
                    await _anchor_decision(llm, item, content, units)
        _link_conditions(items, dropped, content, warnings)
        drafts = []
        for item in items:
            trigger = None
            if item["review_condition"]:
                trigger, notes = await _stage2(llm, item, allowed_metrics, content)
                warnings += notes
            evidence = "\n".join(item["evidence"])  # DB 스키마 유지: 원문 문장들을 줄바꿈으로 구분해 저장
            drafts.append({
                "topic": item["topic"],
                "title": item["title"],
                "decision": item["decision"],
                "rationale": item["rationale"],
                "assumptions": item["assumptions"],
                "evidence": evidence,
                "evidence_in_source": evidence_in_source(evidence, content),
                "review_trigger": trigger,
            })
    for w in warnings:
        log.warning("extraction: %s", w)
    return drafts, warnings


# ---------------------------------------------------------------- Local-first selective escalation
# 보통 문서는 local만 쓴다. local 결과에 '모델의 구조화 실패'가 있을 때만 같은 원문을 OpenRouter(optimized)로 다시 추출한다.
# 판단 규칙은 should_escalate() 한 곳에만 둔다. 결과는 항상 Candidate(사람 승인 대기)로만 저장된다.

EXTRACTION_MODES = ("local", "auto")  # 웹 API에서 고를 수 있는 mode (openrouter 단독은 비교 script 전용)


def extractor_label(provider: str, model: str) -> str:
    """candidates.extractor에 저장하는 값. 예: local:qwen2.5-7b-instruct, openrouter:z-ai/glm-5.3"""
    return f"{provider}:{model}"


def should_escalate(stats: dict | None, error: Exception | None = None) -> dict:
    """local 추출 결과를 OpenRouter로 다시 추출할지. {"escalate": bool, "reasons": [...]}

    escalation 하는 경우 (모델의 구조화 실패만)
    - stage1_failed: Stage 1 응답을 해석할 수 없음 (JSON/schema, 핵심 필드 중국어 혼입 반복 등)
    - stage2_llm_error: Stage 2 응답 자체가 깨짐 (JSON 파싱 실패, 출력 잘림)
    - stage2_manual_fallback_after_validation_failure: 검증 실패 → 재시도까지 실패 → manual로 보존
    - compound_trigger_structuring_failed: 조건절에 복합 조건 표현이 있는데 단일 조건으로 축소됨
    escalation 하지 않는 경우
    - title fallback, 등록되지 않은 지표(needs_mapping), 모델이 고른 manual(사람 판단 조건), 정상 event,
      재시도가 있었지만 최종 구조화 성공, 후보 0개, 근거/연결 warning
    """
    reasons: list[str] = []
    if error is not None and not isinstance(error, ExtractionUnavailable):
        reasons.append("stage1_failed")
    llm = (stats or {}).get("llm") or {}
    for o in llm.get("stage2_outcomes", []):
        if o.get("status") == "manual_fallback":
            reasons.append(
                "stage2_llm_error" if o.get("cause") == "llm_error" else "stage2_manual_fallback_after_validation_failure"
            )
        elif o.get("status") == "ok" and o.get("compound_signal") and o.get("type") not in ("compound", "manual"):
            reasons.append("compound_trigger_structuring_failed")
    reasons = list(dict.fromkeys(reasons))
    return {"escalate": bool(reasons), "reasons": reasons}


def _run_summary(stats: dict, elapsed: float) -> dict:
    llm = stats.get("llm") or {}
    return {
        "provider": stats.get("provider"),
        "model": stats.get("model"),
        "calls": llm.get("calls", {}),
        "retries": llm.get("retries", {}),
        "title_fallbacks": llm.get("title_fallbacks", 0),
        "stage2_outcomes": llm.get("stage2_outcomes", []),
        "usage": llm.get("usage", {}),
        "elapsed_seconds": round(elapsed, 1),
    }


async def _timed_extract(content: str, allowed_metrics: list[str], provider: str, profile: str | None):
    """(drafts, warnings, stats, elapsed, error). 예외를 밖으로 내지 않는다."""
    stats: dict = {}
    started = time.monotonic()
    try:
        drafts, warnings = await extract(content, allowed_metrics, provider=provider, stats=stats, profile=profile)
        return drafts, warnings, stats, time.monotonic() - started, None
    except (ExtractionError, ExtractionUnavailable) as e:
        return None, [], stats, time.monotonic() - started, e


async def extract_auto(
    content: str, allowed_metrics: list[str], stats: dict | None = None, allow_external: bool = True
) -> tuple[list[dict], list[str], str]:
    """Local-first. (drafts, warnings, extractor). stats에 escalation 경과를 채운다.

    allow_external: 외부 모델로 원문을 보내도 되는지 (나중에 프로젝트/요청 단위 정책으로 연결할 자리). False면 local만.
    결과 선택: OpenRouter가 검증을 통과하면 OpenRouter → 아니면 local 결과 유지 → local도 없으면 local 오류를 그대로 낸다.
    """
    info = stats if stats is not None else {}
    info.update(mode="auto", initial_provider="local", escalated=False, escalation_reasons=[], runs={})

    drafts, warnings, local_stats, elapsed, local_error = await _timed_extract(content, allowed_metrics, "local", None)
    info["runs"]["local"] = _run_summary(local_stats, elapsed)
    local_label = extractor_label("local", local_stats.get("model") or EXTRACTION_MODEL)
    if isinstance(local_error, ExtractionUnavailable):
        raise local_error  # llama-swap이 꺼져 있음 → 구조화 실패가 아니므로 외부로 보내지 않고 기존처럼 알린다

    decision = should_escalate(local_stats, local_error)
    info["escalation_reasons"] = decision["reasons"]
    if not decision["escalate"] or not allow_external:
        if decision["escalate"]:
            warnings = [*warnings, f"escalation skipped (external model not allowed): {', '.join(decision['reasons'])}"]
        if local_error is not None:
            raise local_error
        info["final_provider"] = "local"
        return drafts, warnings, local_label

    info["escalated"] = True
    log.warning("extraction: local 결과 구조화 실패 → OpenRouter로 다시 추출 (%s)", ", ".join(decision["reasons"]))
    or_drafts, or_warnings, or_stats, or_elapsed, or_error = await _timed_extract(
        content, allowed_metrics, "openrouter", "optimized"
    )
    info["runs"]["openrouter"] = _run_summary(or_stats, or_elapsed)
    if or_error is None and not should_escalate(or_stats)["escalate"]:
        info["final_provider"] = "openrouter"
        note = f"escalated to OpenRouter ({', '.join(decision['reasons'])})"
        return or_drafts, [note, *or_warnings], extractor_label("openrouter", or_stats.get("model") or OPENROUTER_MODEL)

    # OpenRouter 실패 또는 OpenRouter도 구조화 실패 → local 결과 유지 (manual 조건은 사람이 검토)
    why = _mask(str(or_error)) if or_error else "OpenRouter result also failed validation"
    info["escalation_error"] = why
    if local_error is not None:
        raise local_error
    info["final_provider"] = "local"
    return drafts, [*warnings, f"escalation failed, kept local result: {why}"], local_label
