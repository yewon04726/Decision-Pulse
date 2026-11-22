import { cn } from "@/lib/utils";
import type { DisplayStatus, SourceType } from "@/lib/types";

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
