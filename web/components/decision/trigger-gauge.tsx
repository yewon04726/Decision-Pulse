import { Link2Off, UserCheck } from "lucide-react";
import { formatMetricValue, formatNumber, formatReviewTrigger, formatTriggerRaw, metricKey, metricLabel, needsMapping } from "@/lib/metrics";
import { cn } from "@/lib/utils";
import type { ManualTrigger, MetricTrigger, ReviewTrigger, TriggerEvaluation } from "@/lib/types";

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

/** check_metric으로 판정할 수 없는 조건: 원문 그대로 보여주고 사람이 확인 */
export function ManualTriggerView({ trigger, size = "md" }: { trigger: ManualTrigger; size?: "sm" | "md" }) {
  return (
    <div>
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
        <UserCheck className="size-3" />
        사람이 확인
      </span>
      <p className={cn("mt-1.5 text-ink-2", size === "sm" ? "line-clamp-2 text-xs" : "text-sm")}>{trigger.description}</p>
    </div>
  );
}

/** V2 조건(event, duration, date, compound, 매핑 안 된 metric): 한 줄 요약 + 원문. 자동 판정하지 않는 조건이다 */
export function V2TriggerView({ trigger, size = "md" }: { trigger: ReviewTrigger; size?: "sm" | "md" }) {
  const raw = trigger.raw_condition ?? ("description" in trigger ? trigger.description : null);
  const summary = formatReviewTrigger(trigger);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1">
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
          <UserCheck className="size-3" />
          사람이 확인
        </span>
        {trigger.type === "compound" && (
          <span className="rounded border border-line-strong px-1 text-[10px] font-semibold text-ink-2">{trigger.logic}</span>
        )}
        {needsMapping(trigger) && (
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-accent-ink">
            <Link2Off className="size-3" />
            지표 매핑 필요
          </span>
        )}
      </div>
      <p className={cn("mt-1.5 font-mono text-ink-2", size === "sm" ? "line-clamp-2 text-[11px]" : "text-xs")}>{summary}</p>
      {raw && raw !== summary && (
        <p className={cn("mt-1 text-muted", size === "sm" ? "line-clamp-2 text-[11px]" : "text-xs")}>원문: {raw}</p>
      )}
    </div>
  );
}

export function ReviewTriggerView({
  trigger,
  evaluation,
  size = "md",
}: {
  trigger: ReviewTrigger;
  evaluation: TriggerEvaluation | null;
  size?: "sm" | "md";
}) {
  if (trigger.type === "manual") return <ManualTriggerView trigger={trigger} size={size} />;
  // 자동 판정 게이지는 등록된 지표의 단일 metric 조건만. 나머지 V2 조건은 요약으로 표시한다
  if (trigger.type === "metric" && metricKey(trigger)) {
    return evaluation ? <TriggerGauge trigger={trigger} evaluation={evaluation} size={size} /> : null;
  }
  return <V2TriggerView trigger={trigger} size={size} />;
}
