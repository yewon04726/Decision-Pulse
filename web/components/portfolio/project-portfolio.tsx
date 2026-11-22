"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { setProjectCookie } from "@/lib/project-cookie";
import { cn } from "@/lib/utils";
import type { ProjectSummary } from "@/lib/portfolio";
import { ProjectAside } from "./project-aside";
import { LAYOUT, ProjectCarousel } from "./project-carousel";
import { prefersReducedMotion, useCarousel, useMediaQuery } from "./use-carousel";

const OPEN_MS = 460;
const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 초기 화면: 프로젝트 carousel + 선택된 프로젝트 패널.
 * Open project → 현재 프로젝트 쿠키를 바꾸고 기존 Workspace(/overview)로 이동한다.
 */
export function ProjectPortfolio({ summaries, initialId }: { summaries: ProjectSummary[]; initialId: number | null }) {
  const router = useRouter();
  const compact = useMediaQuery("(max-width: 639px)");
  const initial = Math.max(0, summaries.findIndex((s) => s.project.id === initialId));
  const carousel = useCarousel(summaries.length, initial, (compact ? LAYOUT.compact : LAYOUT.wide).spread);
  const [opening, setOpening] = useState(false);
  const selected = summaries[carousel.index];
  const many = summaries.length > 1;

  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    router.prefetch("/overview");
  }, [router]);

  // Next는 떠난 페이지를 숨겨 둔 채 state를 보존한다(<Activity>). 숨겨질 때 effect cleanup이 돌므로
  // 여기서 Open 전환 상태를 되돌려, 로고/뒤로가기로 돌아오면 항상 처음 화면(전체 carousel)이 보이게 한다.
  useEffect(
    () => () => {
      if (openTimer.current) clearTimeout(openTimer.current);
      openTimer.current = null;
      setOpening(false);
    },
    [],
  );

  function open() {
    if (opening) return;
    setProjectCookie(selected.project.id);
    if (prefersReducedMotion()) return router.push("/overview");
    setOpening(true);
    openTimer.current = setTimeout(() => router.push("/overview"), OPEN_MS);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden px-4 pb-10 pt-10 sm:px-12 sm:pt-14 lg:px-14">
        <div className={cn("flex items-end justify-between gap-6 transition-opacity duration-300", opening && "opacity-0")}>
          <div className="min-w-0">
            <div className="eyebrow">Projects</div>
            <h1 className="mt-3 text-[38px] font-semibold leading-[1.02] tracking-[-0.04em] sm:text-[52px]">Choose a project</h1>
            <p className="mt-3 text-[15px] text-muted sm:text-base">
              Monitor decisions, detect changes, and get notified when it’s time to review.
            </p>
          </div>
          <div className="hidden shrink-0 text-[15px] tabular-nums text-muted sm:block" aria-live="polite">
            <b className="font-semibold text-ink">{pad(carousel.index + 1)}</b> / {pad(summaries.length)}
          </div>
        </div>

        <ProjectCarousel summaries={summaries} carousel={carousel} opening={opening} onOpen={open} />

        {many && (
          <div className={cn("flex items-center justify-center gap-5 transition-opacity duration-300 sm:gap-7", opening && "opacity-0")}>
            <button
              onClick={carousel.prev}
              aria-label="Previous project"
              className="flex size-12 items-center justify-center rounded-full border border-line-strong bg-surface transition-colors duration-200 hover:border-ink hover:bg-ink hover:text-white"
            >
              <ArrowLeft className="size-[18px]" />
            </button>
            <div className="flex max-w-[60vw] overflow-x-auto" role="tablist" aria-label="Projects">
              {summaries.map((s, i) => (
                <button
                  key={s.project.id}
                  role="tab"
                  aria-selected={i === carousel.index}
                  aria-label={`Go to ${s.project.name}`}
                  onClick={() => carousel.go(i)}
                  className="flex size-11 shrink-0 items-center justify-center"
                >
                  <span
                    className={cn(
                      "block h-1.5 rounded-full transition-[width,background-color] duration-400",
                      i === carousel.index ? "w-7 bg-ink" : "w-1.5 bg-line-strong",
                    )}
                  />
                </button>
              ))}
            </div>
            <button
              onClick={carousel.next}
              aria-label="Next project"
              className="flex size-12 items-center justify-center rounded-full border border-line-strong bg-surface transition-colors duration-200 hover:border-ink hover:bg-ink hover:text-white"
            >
              <ArrowRight className="size-[18px]" />
            </button>
          </div>
        )}
      </section>

      <ProjectAside summary={selected} opening={opening} onOpen={open} />
    </div>
  );
}
