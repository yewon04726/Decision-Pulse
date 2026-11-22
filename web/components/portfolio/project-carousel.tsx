"use client";

import { cn } from "@/lib/utils";
import type { ProjectSummary } from "@/lib/portfolio";
import { ProjectCard } from "./project-card";
import { offsetOf, useMediaQuery } from "./use-carousel";

type Carousel = ReturnType<typeof import("./use-carousel").useCarousel>;

// 데스크톱: 3D runway (회전 + 깊이) / 모바일: 선택 카드 + 양옆 카드가 살짝 보이는 평면 swipe
export const LAYOUT = {
  wide: { spread: 240, tilt: 24, depth: 120, scales: [1, 0.86, 0.74], width: 300, height: 400 },
  compact: { spread: 276, tilt: 0, depth: 0, scales: [1, 0.9, 0.9], width: 260, height: 360 },
};

function cardStyle(o: number, dragPx: number, compact: boolean, opening: boolean): React.CSSProperties {
  const L = compact ? LAYOUT.compact : LAYOUT.wide;
  const abs = Math.abs(o);
  // 드래그 중에는 카드가 손가락을 따라오도록 위치를 연속값으로 계산
  const x = o * L.spread + dragPx;
  const scale = L.scales[Math.min(abs, 2)];
  const hidden = abs > (compact ? 1 : 2);
  if (opening) {
    // Open project: 가운데 카드가 앞으로 확장되고 나머지는 바깥으로 물러난다
    return o === 0
      ? { transform: "translate(-50%, -50%) translateZ(80px) scale(1.12)", opacity: 1, zIndex: 20, pointerEvents: "auto" }
      : { transform: `translate(-50%, -50%) translateX(${x * 1.6}px) translateZ(-200px) scale(${scale * 0.9})`, opacity: 0, zIndex: 10 - abs, pointerEvents: "none" };
  }
  return {
    transform: `translate(-50%, -50%) translateX(${x}px) translateZ(${-abs * L.depth}px) rotateY(${-o * L.tilt}deg) scale(${scale})`,
    opacity: hidden ? 0 : abs >= 2 ? 0.5 : abs === 1 && compact ? 0.55 : 1,
    filter: abs >= 2 ? "blur(2px)" : abs === 1 && !compact ? "blur(0.3px)" : "none",
    zIndex: 10 - abs,
    pointerEvents: hidden ? "none" : "auto",
  };
}

export function ProjectCarousel({
  summaries,
  carousel,
  opening,
  onOpen,
}: {
  summaries: ProjectSummary[];
  carousel: Carousel;
  opening: boolean;
  onOpen: () => void;
}) {
  const compact = useMediaQuery("(max-width: 639px)");
  const L = compact ? LAYOUT.compact : LAYOUT.wide;
  const { index, dragPx, dragging, attachStage, handlers } = carousel;

  return (
    <div
      ref={attachStage}
      className="relative min-h-[420px] flex-1 cursor-grab touch-pan-y select-none overscroll-x-none active:cursor-grabbing [perspective:1600px] [perspective-origin:50%_45%] sm:min-h-[500px]"
      {...handlers}
    >
      {/* 바닥 그림자와 궤도: 깊이감만 주는 장식 */}
      <div aria-hidden className="absolute left-1/2 top-1/2 hidden size-[560px] -translate-x-1/2 -translate-y-[46%] rounded-full border border-dashed border-line-strong/70 sm:block" />
      <div aria-hidden className="absolute bottom-3 left-1/2 h-7 w-[340px] -translate-x-1/2 rounded-full bg-ink/10 blur-[14px]" />

      {/* 트랙은 hit-test에서 빼고(뒤로 밀린 카드를 가리지 않게) 카드만 클릭을 받는다 */}
      <div className="pointer-events-none absolute inset-0 [transform-style:preserve-3d]" role="group" aria-roledescription="carousel" aria-label="Projects">
        {summaries.map((s, i) => {
          const o = offsetOf(i, index, summaries.length);
          const active = o === 0;
          return (
            <button
              key={s.project.id}
              type="button"
              aria-label={active ? `Open ${s.project.name}` : `Select ${s.project.name}`}
              aria-current={active ? "true" : undefined}
              tabIndex={active ? 0 : -1}
              onClick={() => {
                if (carousel.consumeClick() || opening) return;
                if (active) onOpen();
                else carousel.go(i);
              }}
              className={cn(
                "absolute left-1/2 top-1/2 cursor-pointer rounded-2xl outline-offset-4",
                !dragging && "transition-[transform,opacity,filter] duration-[600ms] ease-out-soft",
                active
                  ? "shadow-[0_40px_80px_-28px_rgba(17,17,17,0.45),0_12px_24px_-14px_rgba(17,17,17,0.3)]"
                  : "shadow-[0_20px_40px_-26px_rgba(17,17,17,0.22)]",
              )}
              style={{ width: L.width, height: L.height, ...cardStyle(o, dragPx, compact, opening) }}
            >
              <ProjectCard summary={s} active={active} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
