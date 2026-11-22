"""REVIEW_DUE 판정. LLM이 아니라 코드로 deterministic하게 처리한다 (handoff §23).

저장된 lifecycle(status)은 바꾸지 않는다. 조건 충족은 '사람이 재검토할 시점'이라는 표시일 뿐이다.
"""

from schemas import Decision, DecisionView, DisplayStatus, Metric, MetricTrigger, TriggerEvaluation
from services.metrics import MetricError

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


def to_view(decision: Decision, metrics: dict[str, Metric], errors: dict[str, MetricError]) -> DecisionView:
    # metric 트리거만 자동 판정한다. event/duration/date/manual/compound는 판정하지 않는다 (evaluation 없음 → 사람이 확인)
    t = decision.review_trigger
    evaluation = evaluate_trigger(t, metrics, errors) if isinstance(t, MetricTrigger) else None
    return DecisionView(
        **decision.model_dump(),
        display_status=display_status(decision, evaluation),
        evaluation=evaluation,
    )


def trigger_metric_names(decisions: list[Decision]) -> list[str]:
    return sorted(
        {d.review_trigger.metric_key for d in decisions if isinstance(d.review_trigger, MetricTrigger) and d.review_trigger.metric_key}
    )
