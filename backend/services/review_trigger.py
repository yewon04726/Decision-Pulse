"""Review Trigger V2 helper.

- normalize_review_trigger(): legacy / V2 / 잘못된 구조 무엇이 들어와도 V2 모델(또는 None)을 돌려준다. 예외를 내지 않는다.
- format_review_trigger_v2(): 사람이 읽는 한 줄 표현. 예) "search_api_p95_ms > 800 AND 3일 연속"

DB의 기존 데이터는 바꾸지 않는다. 읽을 때만 이 함수로 V2 형태로 해석한다.
자동 평가(trigger evaluation)는 이 모듈의 범위가 아니다.
"""

import json
import logging
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from schemas import (
    CompoundTrigger,
    DateTrigger,
    DurationTrigger,
    EventTrigger,
    ManualTrigger,
    MetricTrigger,
    ReviewTrigger,
)

log = logging.getLogger("review_trigger")

_ADAPTER: TypeAdapter = TypeAdapter(ReviewTrigger)
UNPARSEABLE = "해석할 수 없는 재검토 조건 (원문 확인 필요)"


def _first_text(data: dict, *keys: str) -> str | None:
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def normalize_review_trigger(trigger: Any) -> ReviewTrigger | None:
    """review_trigger 값을 V2 모델로 정규화한다. 어떤 입력에도 예외를 내지 않는다.

    - None / 빈 값 / {}                       → None (재검토 조건 없음)
    - 이미 V2 모델                            → 그대로
    - legacy dict {"type":"metric","metric":…} → MetricTrigger (metric → metric_key)
    - type 없는 dict에 metric/operator/threshold가 있으면 metric으로 간주
    - 문자열                                  → ManualTrigger (원문 그대로)
    - 해석 불가                               → ManualTrigger(원문/JSON 보존) — 데이터를 버리지 않는다
    """
    if trigger is None or trigger == "" or trigger == {}:
        return None
    if isinstance(trigger, (MetricTrigger, EventTrigger, DurationTrigger, DateTrigger, ManualTrigger, CompoundTrigger)):
        return trigger
    if isinstance(trigger, BaseModel):
        trigger = trigger.model_dump()
    if isinstance(trigger, str):
        text = trigger.strip()
        if not text:
            return None
        try:  # JSON 문자열로 저장된 경우
            parsed = json.loads(text)
        except ValueError:
            return ManualTrigger(description=text, raw_condition=text)
        return normalize_review_trigger(parsed) if isinstance(parsed, dict) else ManualTrigger(description=text, raw_condition=text)
    if not isinstance(trigger, dict):
        text = str(trigger)
        return ManualTrigger(description=UNPARSEABLE, raw_condition=text[:1000])

    data = dict(trigger)
    if "type" not in data and {"operator", "threshold"} <= data.keys() and ("metric" in data or "metric_key" in data):
        data["type"] = "metric"
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError as e:
        raw = _first_text(data, "raw_condition", "description")
        log.warning("review_trigger를 해석하지 못해 manual로 처리: %s", e.errors()[:2])
        if data.get("type") == "manual" and raw:
            return ManualTrigger(description=raw, raw_condition=raw)
        return ManualTrigger(
            description=raw or UNPARSEABLE,
            raw_condition=raw or json.dumps(data, ensure_ascii=False, default=str)[:1000],
        )


# ---------------------------------------------------------------- formatter

_DURATION_UNITS = {"minute": "분", "hour": "시간", "day": "일", "week": "주", "month": "개월"}
_DATE_OPS = {">=": "이후", ">": "초과(이후)", "<=": "이전", "<": "미만(이전)", "==": "당일", "!=": "제외"}


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format(t: ReviewTrigger, nested: bool) -> str:
    if isinstance(t, MetricTrigger):
        if t.metric_key:  # 등록된 지표: 키로 표시 (예: user_count > 1000)
            return f"{t.metric_key} {t.operator} {_number(t.threshold)}"
        name = t.raw_metric or "미확인 지표"
        unit = f" {t.unit}" if t.unit else ""
        return f"{name} {t.operator} {_number(t.threshold)}{unit}"
    if isinstance(t, DurationTrigger):
        unit = _DURATION_UNITS.get(t.unit, t.unit)
        return f"{_number(t.value)}{unit} {'연속' if t.mode == 'consecutive' else '동안'}"
    if isinstance(t, DateTrigger):
        return f"{t.date} {_DATE_OPS.get(t.operator, t.operator)}"
    if isinstance(t, EventTrigger):
        return t.description
    if isinstance(t, ManualTrigger):
        return t.description if nested else f"사람 확인 필요: {t.description}"
    if isinstance(t, CompoundTrigger):
        parts = [_format(c, nested=True) for c in t.conditions]
        joined = f" {t.logic} ".join(f"({p})" if isinstance(c, CompoundTrigger) else p for p, c in zip(parts, t.conditions))
        return joined
    return UNPARSEABLE


def format_review_trigger_v2(trigger: Any) -> str:
    """재검토 조건 → 한 줄. 조건이 없으면 '없음'. legacy 데이터도 정규화해서 표시한다."""
    t = normalize_review_trigger(trigger)
    return _format(t, nested=False) if t is not None else "없음"
