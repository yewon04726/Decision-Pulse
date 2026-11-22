import Link from "next/link";
import { StatusBadge } from "@/components/decision/status-badge";
import type { DemoCondition } from "@/lib/types";

/** 시뮬레이터로 조작할 수 없는 재검토 조건 (event / manual / date / duration / compound ...) — 조회만 */
export function ReviewConditions({ conditions }: { conditions: DemoCondition[] }) {
  if (conditions.length === 0) return <p className="text-sm text-muted">조작 불가 조건이 없습니다.</p>;
  return (
    <ul className="divide-y divide-line border-y border-line">
      {conditions.map((c) => (
        <li key={c.decision_id} className="grid gap-4 py-6 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1.4fr)_9rem] md:gap-8">
          <div className="min-w-0">
            <Link href={`/decisions/${c.decision_id}`} className="text-[15px] font-medium text-ink underline-offset-4 hover:underline">
              {c.title}
            </Link>
            <div className="mt-1 font-mono text-[11px] text-faint">
              {c.decision_id} · {c.project_name}
            </div>
          </div>
          <dl className="grid min-w-0 grid-cols-[5.5rem_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="eyebrow pt-0.5 text-[10px]">Trigger type</dt>
            <dd className="font-mono text-ink-2">{c.trigger_type}</dd>
            <dt className="eyebrow pt-0.5 text-[10px]">Condition</dt>
            <dd className="min-w-0 text-ink-2">
              {c.parts.length > 0 ? (
                <ul className="space-y-1.5">
                  {c.parts.map((p, i) => (
                    <li key={i}>
                      {i > 0 && <div className="mb-1.5 text-[10px] font-semibold tracking-[0.12em] text-muted">{c.logic}</div>}
                      <span className="font-mono text-[13px]">{p.expression}</span>{" "}
                      <span className="text-[11px] text-muted">
                        {p.simulator_backed ? `· ${p.metric} 값은 Demo에서 변경 가능` : `· ${p.type}: simulator 없음`}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="leading-relaxed">{c.expression}</span>
              )}
            </dd>
            <dt className="eyebrow pt-0.5 text-[10px]">Demo control</dt>
            <dd className="text-muted">{c.note}</dd>
          </dl>
          <div className="flex flex-col items-start gap-2 md:items-end">
            <StatusBadge status={c.status} />
            <span className="rounded border border-line-strong px-1.5 py-px text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
              Read-only
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
