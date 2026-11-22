"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Bell, ChevronRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { setProjectCookie } from "@/lib/project-cookie";
import { cn } from "@/lib/utils";
import type { ProjectSummary } from "@/lib/portfolio";
import { StatusDonut } from "./status-donut";

const SWAP_MS = 180;

/**
 * 프로젝트가 바뀌면 이전 내용은 아래로 내려가며 사라지고(opacity 1→0, y 0→8px)
 * 새 내용이 올라오며 나타난다(animate-rise). 같은 프로젝트의 데이터 갱신은 바로 반영.
 */
function useSwap(summary: ProjectSummary) {
  const [shown, setShown] = useState(summary);
  const [leaving, setLeaving] = useState(false);
  const changed = shown.project.id !== summary.project.id;

  useEffect(() => {
    if (!changed) return;
    const out = setTimeout(() => setLeaving(true), 0);
    const swap = setTimeout(() => {
      setShown(summary);
      setLeaving(false);
    }, SWAP_MS);
    return () => {
      clearTimeout(out);
      clearTimeout(swap);
    };
  }, [changed, summary]);

  return { current: changed ? shown : summary, leaving };
}

/** 오른쪽 패널: 선택된 프로젝트의 상태 요약 + Review Due 미리보기 + Open project */
export function ProjectAside({
  summary,
  opening,
  onOpen,
}: {
  summary: ProjectSummary;
  opening: boolean;
  onOpen: () => void;
}) {
  const { current, leaving } = useSwap(summary);
  const { project, counts } = current;
  const dueCount = counts?.REVIEW_DUE ?? 0;
  const more = dueCount - current.reviewDue.length;

  return (
    <aside
      aria-live="polite"
      className="flex w-full flex-col gap-8 border-line bg-surface px-4 py-10 sm:px-8 lg:w-[400px] lg:shrink-0 lg:border-l lg:px-10 lg:py-12 max-lg:border-t"
    >
      {/* 바깥: 이전 내용이 사라지는 transition / 안쪽(key): 새 내용이 올라오는 animation */}
      <div
        className={cn(
          "flex flex-1 flex-col transition-[opacity,transform] duration-[180ms] ease-out",
          leaving ? "translate-y-2 opacity-0" : "translate-y-0 opacity-100",
          opening && "opacity-60",
        )}
      >
      <div key={project.id} className="animate-rise flex flex-1 flex-col gap-8">
        <div>
          <span className="inline-flex rounded-md bg-soft px-2 py-1 text-[11px] font-semibold tracking-[0.08em]">/ {current.code}</span>
          <h2 className="mt-3 text-[30px] font-semibold leading-[1.06] tracking-[-0.035em] sm:text-[32px]">{project.name}</h2>
          <p className="mt-2 text-sm text-muted">
            {[project.description || project.slug, project.metrics_connected ? "Metrics connected" : "Metrics not connected"]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>

        {counts ? (
          <StatusDonut counts={counts} total={current.total} candidates={current.candidates} />
        ) : (
          <p className="rounded-2xl border border-dashed border-line-strong p-6 text-sm text-muted">
            이 프로젝트의 결정을 불러오지 못했습니다. backend(:8001) 상태를 확인하세요.
          </p>
        )}

        <section className="flex flex-col gap-1" aria-labelledby="review-due-heading">
          <div className="mb-2 flex items-center justify-between">
            <h3 id="review-due-heading" className="text-[15px] font-semibold">
              Review due
            </h3>
            <span
              className={cn(
                "min-w-6 rounded-full px-2 py-0.5 text-center text-xs font-semibold tabular-nums",
                dueCount ? "bg-ink text-white" : "bg-soft text-muted",
              )}
            >
              {counts ? dueCount : "—"}
            </span>
          </div>
          {current.reviewDue.length === 0 ? (
            <p className="rounded-xl border border-dashed border-line-strong px-4 py-5 text-center text-[13px] text-muted">
              {counts ? "Nothing to review right now." : "—"}
            </p>
          ) : (
            <ul className="-mx-3 flex flex-col">
              {current.reviewDue.map((d) => (
                <li key={d.id}>
                  <Link
                    href={`/decisions/${encodeURIComponent(d.id)}`}
                    onClick={() => setProjectCookie(project.id)}
                    className="group flex items-center gap-3.5 rounded-xl px-3 py-2.5 transition-colors hover:bg-soft"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-[10px] bg-accent-soft text-accent-ink">
                      <Bell className="size-4" />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <strong className="truncate text-sm font-semibold">{d.title}</strong>
                      <span className="font-mono text-[11.5px] leading-snug text-muted [overflow-wrap:anywhere]">
                        {d.trigger}
                        {d.observed && ` · 현재 ${d.observed}`}
                      </span>
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-faint transition-transform duration-200 group-hover:translate-x-0.5" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {more > 0 && (
            <Link
              href="/decisions?status=REVIEW_DUE"
              onClick={() => setProjectCookie(project.id)}
              className="mt-1 self-start text-[13px] text-muted underline-offset-4 hover:text-ink hover:underline"
            >
              +{more} more
            </Link>
          )}
        </section>
      </div>
      </div>

      <Button size="lg" className="w-full rounded-2xl" onClick={onOpen} disabled={opening}>
        {opening ? <Loader2 className="size-4 animate-spin" /> : null}
        Open project
        {!opening && <ArrowRight className="size-[18px]" />}
      </Button>
    </aside>
  );
}
