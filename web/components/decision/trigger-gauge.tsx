import { MappingBadge } from "@/components/decision/status-badge";
import { formatMetricValue, formatNumber, formatReviewTrigger, formatTriggerRaw, metricKey, metricLabel, needsMapping } from "@/lib/metrics";
import { cn } from "@/lib/utils";
import type { ManualTrigger, MetricTrigger, ReviewTrigger, TriggerEvaluation, TriggerStatus } from "@/lib/types";

/**
 * 재검토 조건 vs 현재 metric 비교.
 * 바의 75% 지점이 임계값이 되도록 스케일링해서, 초과분도 시각적으로 보이게 한다.
 */
export function TriggerGauge({
  trigger,
  evaluation,
  size = "md",
  bare = false,
}: {
  trigger: MetricTrigger;
  evaluation: TriggerEvaluation;
  size?: "sm" | "md";
  /** true면 막대만 (조건/현재값을 이미 옆에 표시하는 곳) */
  bare?: boolean;
}) {
  const { metric, met } = evaluation;
  const ratio = evaluation.ratio ?? 0;
  const fill = Math.min(ratio * 75, 100);
  // 충족 = accent, 미충족 = ink. 임계값 근접은 색 대신 바 길이로 읽힌다
  const barColor = met ? "bg-accent" : "bg-ink";

  return (
    <div>
      <div className={cn("flex items-baseline justify-between gap-3", bare && "sr-only")}>
        <span className={cn("font-mono text-muted", size === "sm" ? "text-[11px]" : "text-xs")}>
          {formatTriggerRaw(trigger)}
        </span>
        <span
          className={cn(
            "font-semibold tabular-nums",
            metric === null
              ? evaluation.reason === "not_connected"
                ? "text-faint"
                : "text-danger"
              : met
                ? "text-accent-ink"
                : "text-ink",
            size === "sm" ? "text-xs" : "text-sm",
          )}
          title={evaluation.error ?? undefined}
        >
          {metric
            ? formatMetricValue(metric.value, metric.unit)
            : evaluation.reason === "not_connected"
              ? "Metric not connected"
              : "조회 실패"}
        </span>
      </div>
      <div className={cn("relative mt-2 rounded-full bg-line", size === "sm" ? "h-1" : "h-1.5")}>
        {metric && <div className={cn("h-full rounded-full transition-[width] duration-700 ease-out-soft", barColor)} style={{ width: `${fill}%` }} />}
        <div className="absolute inset-y-[-4px] left-3/4 w-px bg-ink" title={`threshold ${trigger.threshold}`} />
      </div>
      {size === "md" && (
        <div className="mt-1.5 flex justify-between text-[11px] text-faint">
          <span>{metricLabel(trigger)}</span>
          <span className="tabular-nums">threshold {formatNumber(trigger.threshold)}</span>
        </div>
      )}
    </div>
  );
}

/** 사람이 확인하는 조건 (MANUAL): 원문 그대로 */
export function ManualTriggerView({ trigger, size = "md" }: { trigger: ManualTrigger; size?: "sm" | "md" }) {
  return (
    <div>
      <MappingBadge status="MANUAL" />
      <p className={cn("mt-1.5 text-ink-2", size === "sm" ? "line-clamp-2 text-xs" : "text-sm")}>{trigger.description}</p>
    </div>
  );
}

/** 자동 평가하지 않는 조건 (NEEDS_MAPPING: 데이터 소스 미연결 / MANUAL: event · duration · date · compound): 요약 + 원문 */
export function V2TriggerView({
  trigger,
  status,
  size = "md",
}: {
  trigger: ReviewTrigger;
  status?: TriggerStatus | null;
  size?: "sm" | "md";
}) {
  const raw = trigger.raw_condition ?? ("description" in trigger ? trigger.description : null);
  const summary = formatReviewTrigger(trigger);
  const mapping = status?.mapping_status ?? (needsMapping(trigger) ? "NEEDS_MAPPING" : "MANUAL");
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        <MappingBadge status={mapping} />
        {trigger.type === "compound" && (
          <span className="rounded border border-line-strong px-1 text-[10px] font-semibold text-ink-2">{trigger.logic}</span>
        )}
      </div>
      <p className={cn("mt-1.5 font-mono text-ink-2", size === "sm" ? "line-clamp-2 text-[11px]" : "text-xs")}>{summary}</p>
      {size === "md" && raw && raw !== summary && <p className="mt-1 text-xs text-muted">원문: {raw}</p>}
    </div>
  );
}

export function ReviewTriggerView({
  trigger,
  evaluation,
  status,
  size = "md",
}: {
  trigger: ReviewTrigger;
  evaluation: TriggerEvaluation | null;
  status?: TriggerStatus | null;
  size?: "sm" | "md";
}) {
  // 자동 평가 게이지는 CONNECTED 단일 metric 조건만. 나머지는 자동화 상태 + 요약
  if (status && status.mapping_status !== "CONNECTED") {
    return trigger.type === "manual" ? <ManualTriggerView trigger={trigger} size={size} /> : <V2TriggerView trigger={trigger} status={status} size={size} />;
  }
  if (trigger.type === "manual") return <ManualTriggerView trigger={trigger} size={size} />;
  if (trigger.type === "metric" && metricKey(trigger)) {
    return evaluation ? <TriggerGauge trigger={trigger} evaluation={evaluation} size={size} /> : null;
  }
  return <V2TriggerView trigger={trigger} status={status} size={size} />;
}
