import { cn } from "@/lib/utils";
import type { DecisionView, DisplayStatus, MappingStatus, SourceType } from "@/lib/types";

// 상태는 색 면이 아니라 작은 점 + 라벨로 표시한다. accent는 REVIEW_DUE에만.
const STATUS: Record<DisplayStatus, { label: string; text: string; dot: string }> = {
  ACTIVE: { label: "Active", text: "text-muted", dot: "bg-ok" },
  REVIEW_DUE: { label: "Review due", text: "text-accent-ink", dot: "bg-accent" },
  PENDING_REVIEW: { label: "Pending review", text: "text-muted", dot: "border border-muted bg-transparent" },
  SUPERSEDED: { label: "Superseded", text: "text-faint", dot: "bg-line-strong" },
  UNKNOWN: { label: "Unknown", text: "text-danger", dot: "bg-danger" },
};

export function StatusBadge({ status, className }: { status: DisplayStatus; className?: string }) {
  const { label, text, dot } = STATUS[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em]", text, className)}>
      <span className={cn("size-1.5 shrink-0 rounded-full", dot)} aria-hidden />
      {label}
    </span>
  );
}

const SOURCE_LABEL: Record<SourceType, string> = {
  meeting: "Meeting",
  issue: "Issue",
  adr: "ADR",
  pr: "PR",
  audio_transcript: "Audio",
};

export function SourceTypeBadge({ type }: { type: SourceType }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded border border-line-strong px-1.5 py-px text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-2">
      {SOURCE_LABEL[type]}
    </span>
  );
}

export function TopicTag({ topic }: { topic: string }) {
  return <span className="font-mono text-[11px] text-muted">#{topic}</span>;
}

/** 사람이 작성한 원문 표시 (Review Queue 승인 여부와는 별개) */
export function HumanAuthoredTag() {
  return (
    <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-faint" title="사람이 작성한 원문">
      human-authored
    </span>
  );
}

// ---- 재검토 조건 자동화 상태 (review state와 섞지 않는다)

const MAPPING: Record<MappingStatus, { label: string; short: string; filled: boolean }> = {
  CONNECTED: { label: "자동 모니터링", short: "Auto", filled: true },
  NEEDS_MAPPING: { label: "데이터 연결 필요", short: "Needs mapping", filled: false },
  MANUAL: { label: "수동 확인 필요", short: "Manual review", filled: false },
};

/** ● 자동 모니터링 / ○ 데이터 연결 필요 / ○ 수동 확인 필요 */
export function MappingBadge({ status, className }: { status: MappingStatus; className?: string }) {
  const m = MAPPING[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[11px] font-semibold text-ink-2", className)}>
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", m.filled ? "bg-ink" : "border border-ink-2")} />
      {m.label}
    </span>
  );
}

/**
 * 목록용 상태 라벨: 자동 평가 대상(CONNECTED)은 review state(ACTIVE / REVIEW_DUE / UNKNOWN),
 * 데이터 연결이 필요하거나 사람이 확인하는 조건은 Needs mapping / Manual review (UNKNOWN으로 뭉치지 않는다).
 * lifecycle 상태(PENDING_REVIEW, SUPERSEDED)는 항상 우선한다.
 */
export function ReviewStateBadge({ decision, className }: { decision: DecisionView; className?: string }) {
  const { display_status: status, trigger_status: ts } = decision;
  if (status === "SUPERSEDED" || status === "PENDING_REVIEW" || !ts || ts.mapping_status === "CONNECTED") {
    return <StatusBadge status={status} className={className} />;
  }
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted", className)}>
      <span aria-hidden className="size-1.5 shrink-0 rounded-full border border-muted" />
      {MAPPING[ts.mapping_status].short}
    </span>
  );
}
