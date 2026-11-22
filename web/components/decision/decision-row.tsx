import Link from "next/link";
import { cn } from "@/lib/utils";
import type { DecisionView } from "@/lib/types";
import { StatusBadge, TopicTag } from "./status-badge";
import { ReviewTriggerView } from "./trigger-gauge";

/** Decisions 목록의 한 줄: 제목/결정 · 이유 · 재검토 조건 */
export function DecisionRow({ decision }: { decision: DecisionView }) {
  const { display_status: status, evaluation } = decision;
  return (
    <Link
      href={`/decisions/${decision.id}`}
      className={cn(
        "group grid gap-4 border-b border-line py-6 transition-colors hover:bg-surface/70 md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_1rem] md:gap-8",
        status === "SUPERSEDED" && "opacity-60",
      )}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusBadge status={status} />
          <TopicTag topic={decision.topic} />
        </div>
        {/* 두 줄까지 그대로, 넘칠 때만 … (전체 제목은 hover) */}
        <h3
          className="mt-2 line-clamp-2 text-lg font-semibold leading-snug tracking-[-0.02em] text-ink group-hover:underline group-hover:underline-offset-4"
          title={decision.title}
        >
          {decision.title}
        </h3>
        <p className="mt-1 line-clamp-2 text-sm text-muted">{decision.decision}</p>
      </div>
      <div className="min-w-0">
        <div className="eyebrow text-[10px]">Reason</div>
        <p className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-ink-2">{decision.rationale}</p>
      </div>
      <div className="min-w-0">
        <div className="eyebrow text-[10px]">Review trigger</div>
        <div className="mt-1.5">
          {decision.review_trigger ? (
            <ReviewTriggerView trigger={decision.review_trigger} evaluation={evaluation} size="sm" />
          ) : (
            <span className="text-sm text-faint">없음 (영구 결정)</span>
          )}
        </div>
        <div className="mt-2 font-mono text-[10.5px] text-faint">{decision.id}</div>
      </div>
      <span aria-hidden className="hidden pt-6 text-faint transition-transform duration-200 group-hover:translate-x-0.5 md:block">
        →
      </span>
    </Link>
  );
}
