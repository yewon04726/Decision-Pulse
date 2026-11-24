"""Review Trigger 자동화 상태 판정 (deterministic, LLM 없음). DB에 저장하지 않고 읽을 때 계산한다.

    CONNECTED     : canonical 지표에 resolver(data source adapter)가 있다 → Review Monitor가 자동 평가
    NEEDS_MAPPING : 측정 가능한 수치 조건인데 연결된 data source가 없다 (registry에 resolver 없음 / registry에 없는 지표 /
                    프로젝트에 metric source 없음) → 저장·표시는 정상, 자동 평가만 보류
    MANUAL        : 객관적인 자동 측정값으로 표현하기 어렵다 (manual / event / date / duration, 주관적 표현의 metric,
                    자동 평가하지 않는 compound) → 사람이 확인

UNKNOWN은 여기서 쓰지 않는다. UNKNOWN은 CONNECTED인데 '지금 값을 읽지 못한' 평가 결과다 (services/review.py).
"""

from __future__ import annotations

import metric_registry
from schemas import (
    CompoundTrigger,
    DateTrigger,
    DurationTrigger,
    EventTrigger,
    ManualTrigger,
    MetricTrigger,
    TriggerStatus,
)

_MANUAL_REASON = {
    "manual": "자동 측정이 어려운 재검토 조건입니다. 사람이 직접 확인해야 합니다.",
    "event": "사건 발생 여부를 자동으로 확인할 data source가 없습니다. 사람이 직접 확인해야 합니다.",
    "date": "날짜 조건은 자동 평가하지 않습니다. 사람이 직접 확인해야 합니다.",
    "duration": "지속 기간 조건은 자동 평가하지 않습니다. 사람이 직접 확인해야 합니다.",
}


def _metric_status(t: MetricTrigger) -> TriggerStatus:
    definition = metric_registry.get(t.canonical_metric)
    label = definition.label if definition else t.raw_metric
    if t.metric_key:
        d = metric_registry.get(t.metric_key)
        return TriggerStatus(mapping_status="CONNECTED", canonical_metric=t.metric_key, metric_label=d.label if d else t.metric_key,
                             resolver=d.resolver if d else None, data_source=d.source if d else None, window=t.window,
                             reason="자동 모니터링: Review Monitor가 현재값을 읽어 판정합니다.")
    if definition is None and metric_registry.is_subjective(t.raw_metric, t.raw_condition):
        return TriggerStatus(mapping_status="MANUAL", metric_label=label, window=t.window,
                             reason="측정 가능한 지표로 표현하기 어려운 조건입니다. 사람이 직접 확인해야 합니다.")
    reason = ("현재 Decision Pulse에 연결된 데이터 소스가 없습니다." if definition
              else "등록되지 않은 지표입니다. 측정 가능한 조건이지만 연결된 데이터 소스가 없습니다.")
    return TriggerStatus(mapping_status="NEEDS_MAPPING", canonical_metric=t.canonical_metric, metric_label=label,
                         window=t.window, reason=reason)


def classify(trigger) -> TriggerStatus | None:
    """registry 기준 상태 (프로젝트 연결 여부는 to_view가 평가 결과로 반영한다)"""
    if trigger is None:
        return None
    if isinstance(trigger, MetricTrigger):
        return _metric_status(trigger)
    if isinstance(trigger, CompoundTrigger):
        leaves = [classify(c) for c in trigger.conditions]
        unmapped = [s for s in leaves if s and s.mapping_status == "NEEDS_MAPPING"]
        if unmapped:
            return TriggerStatus(mapping_status="NEEDS_MAPPING", canonical_metric=unmapped[0].canonical_metric,
                                 metric_label=unmapped[0].metric_label,
                                 reason="복합 조건 안에 데이터 소스가 연결되지 않은 지표가 있습니다.")
        return TriggerStatus(mapping_status="MANUAL",
                             reason="복합 조건(AND/OR, 지속 기간 포함)은 자동 평가하지 않습니다. 사람이 직접 확인해야 합니다.")
    kind = getattr(trigger, "type", "manual")
    if isinstance(trigger, (ManualTrigger, EventTrigger, DateTrigger, DurationTrigger)):
        return TriggerStatus(mapping_status="MANUAL", reason=_MANUAL_REASON.get(kind, _MANUAL_REASON["manual"]))
    return TriggerStatus(mapping_status="MANUAL", reason=_MANUAL_REASON["manual"])
