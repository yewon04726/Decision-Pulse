import Link from "next/link";
import { ArrowLink } from "@/components/ui/button";
import { formatMetricValue, formatReviewTrigger, metricKey } from "@/lib/metrics";
import type { DecisionView } from "@/lib/types";
import { StatusBadge, TopicTag } from "./status-badge";
import { TriggerGauge } from "./trigger-gauge";

/** "Decisions requiring attention"의 한 줄: 큰 제목 + 결정 문장 + Trigger / Observed + 다음 동작 */
export function AttentionRow({ decision }: { decision: DecisionView }) {
  const t = decision.review_trigger;
  const metric = decision.evaluation?.metric;
  const gauge = t?.type === "metric" && metricKey(t) && decision.evaluation ? { trigger: t, evaluation: decision.evaluation } : null;

  return (
    <article className="grid gap-6 border-b border-line py-8 first:pt-2 md:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)_auto] md:gap-10">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusBadge status={decision.display_status} />
          <TopicTag topic={decision.topic} />
          <span className="font-mono text-[11px] text-faint">{decision.id}</span>
        </div>
        <h3 className="mt-3 text-2xl font-semibold leading-tight tracking-[-0.03em] sm:text-[28px]">
          <Link href={`/decisions/${decision.id}`} className="underline-offset-[6px] hover:underline">
            {decision.title}
          </Link>
        </h3>
        <p className="mt-2 text-[15px] leading-relaxed text-ink-2">{decision.decision}</p>
      </div>

      <dl className="grid grid-cols-2 content-start gap-x-6 gap-y-5">
        <div className="col-span-2 sm:col-span-1">
          <dt className="eyebrow text-[10px]">Trigger</dt>
          <dd className="mt-1.5 font-mono text-[13px] text-ink">{formatReviewTrigger(t)}</dd>
        </div>
        <div className="col-span-2 sm:col-span-1">
          <dt className="eyebrow text-[10px]">Observed</dt>
          <dd className="mt-1.5 text-[15px] font-semibold tabular-nums text-accent-ink">
            {metric ? formatMetricValue(metric.value, metric.unit) : <span className="font-normal text-muted">사람이 확인</span>}
          </dd>
        </div>
        {gauge && (
          <div className="col-span-2">
            <TriggerGauge trigger={gauge.trigger} evaluation={gauge.evaluation} size="sm" bare />
          </div>
        )}
      </dl>

      <div className="flex flex-row flex-wrap gap-x-6 gap-y-2 md:flex-col md:items-end md:pt-7">
        <ArrowLink href={`/decisions/${decision.id}#review-memo`}>Review Memo</ArrowLink>
        <ArrowLink href={`/decisions/${decision.id}`} className="text-muted hover:text-ink">
          Open Decision
        </ArrowLink>
      </div>
    </article>
  );
}
