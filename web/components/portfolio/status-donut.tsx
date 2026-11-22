import type { DisplayStatus } from "@/lib/types";

const R = 50;
const C = 2 * Math.PI * R;

const SEGMENTS: { status: DisplayStatus; label: string; color: string }[] = [
  { status: "ACTIVE", label: "Active", color: "var(--color-ink)" },
  { status: "REVIEW_DUE", label: "Review due", color: "var(--color-accent)" },
  { status: "PENDING_REVIEW", label: "Pending review", color: "var(--color-faint)" },
  { status: "UNKNOWN", label: "Unknown", color: "var(--color-danger)" },
  { status: "SUPERSEDED", label: "Superseded", color: "var(--color-line-strong)" },
];

/** 결정 상태 분포 donut + legend. 0인 상태는 legend에서 뺀다 (Active / Review due는 항상 표시) */
export function StatusDonut({
  counts,
  total,
  candidates,
}: {
  counts: Record<DisplayStatus, number>;
  total: number;
  candidates: number | null;
}) {
  const lens = SEGMENTS.map((s) => (total ? (counts[s.status] / total) * C : 0));
  const arcs = SEGMENTS.map((s, i) => ({ ...s, len: lens[i], offset: lens.slice(0, i).reduce((a, b) => a + b, 0) }));
  const legend = SEGMENTS.filter((s) => counts[s.status] > 0 || s.status === "ACTIVE" || s.status === "REVIEW_DUE");
  const summary = legend.map((s) => `${counts[s.status]} ${s.label.toLowerCase()}`).join(", ");

  return (
    <div className="flex flex-col gap-6 rounded-2xl bg-soft p-6 sm:flex-row sm:items-center sm:gap-7">
      <div className="relative size-[120px] shrink-0">
        <svg width="120" height="120" viewBox="0 0 120 120" role="img" aria-label={`${total} decisions: ${summary}`}>
          <g transform="rotate(-90 60 60)">
            <circle cx="60" cy="60" r={R} fill="none" stroke="var(--color-line)" strokeWidth="12" />
            {arcs.map(
              (a) =>
                a.len > 0 && (
                  <circle
                    key={a.status}
                    cx="60"
                    cy="60"
                    r={R}
                    fill="none"
                    stroke={a.color}
                    strokeWidth="12"
                    strokeDasharray={`${a.len} ${C}`}
                    strokeDashoffset={-a.offset}
                    className="transition-[stroke-dasharray,stroke-dashoffset] duration-700 ease-out-soft"
                  />
                ),
            )}
          </g>
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <strong className="text-[30px] font-semibold leading-none tracking-[-0.04em] tabular-nums">{total}</strong>
          <span className="mt-1 text-[11px] text-muted">Decisions</span>
        </div>
      </div>
      <dl className="flex flex-1 flex-col gap-2.5 text-sm">
        {legend.map((s) => (
          <div key={s.status} className="flex items-center gap-2.5">
            <i className="size-2.5 shrink-0 rounded-[3px]" style={{ background: s.color }} aria-hidden />
            <dt className="flex-1 text-ink-2">{s.label}</dt>
            <dd className="font-semibold tabular-nums">{counts[s.status]}</dd>
          </div>
        ))}
        <div className="mt-1 flex items-center gap-2.5 border-t border-line-strong/60 pt-3">
          <i className="size-2.5 shrink-0 rounded-[3px] border border-muted" aria-hidden />
          <dt className="flex-1 text-ink-2" title="Candidates awaiting approval">
            Candidates
          </dt>
          <dd className="font-semibold tabular-nums">{candidates ?? "—"}</dd>
        </div>
      </dl>
    </div>
  );
}
