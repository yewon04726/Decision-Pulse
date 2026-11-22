import { cn } from "@/lib/utils";
import type { ProjectSummary } from "@/lib/portfolio";

/** 결정 상태 분포 막대 (active / review due / 기타) */
function StatusBar({ summary, active }: { summary: ProjectSummary; active: boolean }) {
  const c = summary.counts;
  if (!c || summary.total === 0) return <div className={cn("h-1.5 rounded-full", active ? "bg-white/20" : "bg-line")} />;
  const other = c.PENDING_REVIEW + c.UNKNOWN + c.SUPERSEDED;
  const parts = [
    { n: c.ACTIVE, cls: active ? "bg-white" : "bg-ink" },
    { n: c.REVIEW_DUE, cls: "bg-accent" },
    { n: other, cls: active ? "bg-white/35" : "bg-line-strong" },
  ].filter((p) => p.n > 0);
  return (
    <div className="flex h-1.5 gap-[3px]">
      {parts.map((p, i) => (
        <span key={i} className={cn("h-full rounded-full", p.cls)} style={{ width: `${(p.n / summary.total) * 100}%` }} />
      ))}
    </div>
  );
}

/**
 * 프로젝트 오브젝트: 종이 한 장이 살짝 비어져 나온 폴더.
 * 선택된 카드는 검정 / 흰 글자로 반전한다. 위치(transform)는 carousel이 정한다.
 */
export function ProjectCard({ summary, active }: { summary: ProjectSummary; active: boolean }) {
  const { project, counts } = summary;
  const value = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n);
  return (
    <span className="relative block h-full w-full">
      {/* 폴더 안의 종이 */}
      <span
        aria-hidden
        className={cn(
          "absolute inset-x-5 -top-[7px] h-8 rounded-t-lg border transition-colors duration-500",
          active ? "border-line bg-paper" : "border-line bg-soft",
        )}
      />
      {/* 폴더 탭 */}
      <span
        aria-hidden
        className={cn(
          "absolute -top-[11px] left-0 h-[14px] w-24 rounded-t-[10px] border border-b-0 transition-colors duration-500",
          active ? "border-ink bg-ink" : "border-line bg-surface",
        )}
      />
      <span
        className={cn(
          "relative flex h-full flex-col gap-5 rounded-2xl rounded-tl-none border p-6 text-left transition-[background-color,color,border-color] duration-500",
          active ? "border-ink bg-ink text-white" : "border-line bg-surface text-ink",
        )}
      >
        <span className="flex items-center justify-between">
          <span
            className={cn(
              "rounded-md px-2 py-1 text-[11px] font-semibold tracking-[0.08em]",
              active ? "bg-white/12 text-white" : "bg-soft text-ink",
            )}
          >
            {summary.code}
          </span>
          <span className={cn("flex items-center gap-1.5 text-xs", active ? "text-white/70" : "text-muted")}>
            <span className={cn("size-1.5 rounded-full", project.metrics_connected ? "bg-ok" : active ? "bg-white/40" : "bg-line-strong")} />
            {project.metrics_connected ? "Monitoring" : "Manual"}
          </span>
        </span>

        <span className="mt-4 flex flex-col gap-2">
          <strong className="line-clamp-3 text-[26px] font-semibold leading-[1.08] tracking-[-0.03em]">{project.name}</strong>
          {(project.description || project.slug) && (
            <span className={cn("line-clamp-2 text-[13px]", active ? "text-white/65" : "text-muted")}>
              {project.description || project.slug}
            </span>
          )}
        </span>

        <span className="flex-1" />

        <span className="block">
          <span className="flex items-baseline justify-between">
            <span className={cn("text-xs", active ? "text-white/65" : "text-muted")}>Decisions</span>
            <strong className="text-[40px] font-semibold leading-none tracking-[-0.04em] tabular-nums">
              {counts ? summary.total : "—"}
            </strong>
          </span>
          <span className="mt-3 block">
            <StatusBar summary={summary} active={active} />
          </span>
        </span>

        <span className={cn("grid grid-cols-2 gap-3 border-t pt-4", active ? "border-white/15" : "border-line")}>
          <span className="flex flex-col gap-1">
            <span className={cn("text-[11px]", active ? "text-white/65" : "text-muted")}>Review due</span>
            <strong className={cn("text-lg font-semibold tabular-nums", counts?.REVIEW_DUE && !active && "text-accent-ink")}>
              {value(counts?.REVIEW_DUE)}
            </strong>
          </span>
          <span className="flex flex-col gap-1">
            <span className={cn("text-[11px]", active ? "text-white/65" : "text-muted")}>Candidates</span>
            <strong className="text-lg font-semibold tabular-nums">{value(summary.candidates)}</strong>
          </span>
        </span>
      </span>
    </span>
  );
}
