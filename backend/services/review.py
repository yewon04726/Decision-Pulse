"""REVIEW_DUE 판정. LLM이 아니라 코드로 deterministic하게 처리한다 (handoff §23).

저장된 lifecycle(status)은 바꾸지 않는다. 조건 충족은 '사람이 재검토할 시점'이라는 표시일 뿐이다.
"""

from schemas import Decision, DecisionView, DisplayStatus, Metric, MetricTrigger, TriggerEvaluation, TriggerStatus
from services.metrics import MetricError
from services.trigger_status import classify

_OPS = {
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
}


def evaluate_trigger(
    trigger: MetricTrigger, metrics: dict[str, Metric], errors: dict[str, MetricError]
) -> TriggerEvaluation:
    if trigger.metric_key is None:  # 등록되지 않은 지표 → 판정하지 않는다 (다른 지표로 대체하지 않음)
        return TriggerEvaluation(
            metric=None,
            met=None,
            ratio=None,
            error=f"지표 매핑 필요: '{trigger.raw_metric or '미확인 지표'}'는 등록된 metric이 아닙니다.",
            reason="not_found",
        )
    metric = metrics.get(trigger.metric_key)
    if metric is None:
        err = errors.get(trigger.metric_key)
        return TriggerEvaluation(
            metric=None,
            met=None,
            ratio=None,
            error=err.message if err else "metric 조회 결과 없음",
            reason=err.reason if err else None,
        )
    return TriggerEvaluation(
        metric=metric,
        met=_OPS[trigger.operator](metric.value, trigger.threshold),
        ratio=metric.value / trigger.threshold if trigger.threshold else None,
    )


def display_status(decision: Decision, evaluation: TriggerEvaluation | None) -> DisplayStatus:
    if decision.status == "superseded":
        return "SUPERSEDED"
    if decision.status == "pending_review":
        return "PENDING_REVIEW"
    if evaluation is None:
        return "ACTIVE"
    if evaluation.met is None:
        return "UNKNOWN"
    return "REVIEW_DUE" if evaluation.met else "ACTIVE"


def trigger_view(decision: Decision, metrics: dict[str, Metric], errors: dict[str, MetricError]
                 ) -> tuple[TriggerEvaluation | None, TriggerStatus | None]:
    """(evaluation, trigger_status). CONNECTED 지표 조건만 평가한다.
    NEEDS_MAPPING / MANUAL은 평가하지 않는다 → display_status는 UNKNOWN이 아니라 lifecycle 그대로 (ACTIVE 등)."""
    t = decision.review_trigger
    status = classify(t)
    if status is None or status.mapping_status != "CONNECTED" or not isinstance(t, MetricTrigger):
        return None, status
    evaluation = evaluate_trigger(t, metrics, errors)
    if evaluation.reason == "not_connected":
        # 지표에는 resolver가 있지만 이 프로젝트에는 data source가 없다 → 연결 필요 (값 조회 실패 UNKNOWN이 아님)
        return None, status.model_copy(update={
            "mapping_status": "NEEDS_MAPPING", "resolver": None, "data_source": None,
            "reason": "이 프로젝트에는 데이터 소스(metric source)가 연결되지 않았습니다."})
    if evaluation.met is None:
        update = {"evaluation_status": "UNKNOWN", "reason": f"자동 모니터링 대상이지만 현재값을 읽지 못했습니다: {evaluation.error}"}
    else:
        update = {"evaluation_status": "REVIEW_DUE" if evaluation.met else "ACTIVE"}
    if evaluation.metric and evaluation.metric.source:
        update["data_source"] = evaluation.metric.source
    return evaluation, status.model_copy(update=update)


def to_view(decision: Decision, metrics: dict[str, Metric], errors: dict[str, MetricError]) -> DecisionView:
    # CONNECTED metric 조건만 자동 판정한다. NEEDS_MAPPING / MANUAL(event/duration/date/manual/compound)은 판정하지 않는다
    evaluation, status = trigger_view(decision, metrics, errors)
    return DecisionView(
        **decision.model_dump(),
        display_status=display_status(decision, evaluation),
        evaluation=evaluation,
        trigger_status=status,
    )


def trigger_metric_names(decisions: list[Decision]) -> list[str]:
    return sorted(
        {d.review_trigger.metric_key for d in decisions if isinstance(d.review_trigger, MetricTrigger) and d.review_trigger.metric_key}
    )
