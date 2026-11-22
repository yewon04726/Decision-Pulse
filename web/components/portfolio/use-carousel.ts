"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

/** CSS media query 구독 (서버 렌더에서는 false) */
export function useMediaQuery(query: string) {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

export function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const DRAG_START_PX = 6;
/** 넘길 곳이 없을 때(프로젝트 1개) 드래그 저항: 손가락의 1/4만 따라오고 놓으면 제자리로 */
const RUBBER_BAND = 0.25;
/** trackpad 두 손가락 가로 스와이프: 누적 deltaX가 이만큼 넘으면 한 장 */
const WHEEL_STEP_PX = 50;
/** 한 번 넘긴 뒤 관성 스크롤 이벤트를 무시하는 시간 */
const WHEEL_LOCK_MS = 500;

/**
 * Carousel 상태: 선택 index, 좌우 이동(순환), 키보드 ←/→, pointer drag/swipe.
 * 드래그 중에는 dragPx만큼 카드가 손가락을 따라오고, 놓으면 이동 거리(spread 단위)만큼 넘어간다.
 * Mac trackpad 두 손가락 가로 스와이프(wheel deltaX)로도 넘긴다 → attachStage를 무대 요소의 ref에 붙인다.
 */
export function useCarousel(count: number, initial: number, spread: number) {
  const [index, setIndex] = useState(initial);
  const [dragPx, setDragPx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ x: number; y: number; pointer: number } | null>(null);
  /** 방금 드래그가 끝났으면 뒤따르는 click을 무시 */
  const suppressClick = useRef(false);
  /** 무대 요소 (callback ref로 받아 wheel listener를 붙인다) */
  const [stage, attachStage] = useState<HTMLDivElement | null>(null);

  const go = useCallback((i: number) => setIndex(((i % count) + count) % count), [count]);
  const next = useCallback(() => setIndex((i) => (i + 1) % count), [count]);
  const prev = useCallback(() => setIndex((i) => (i - 1 + count) % count), [count]);

  useEffect(() => {
    if (count < 2) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.altKey || e.metaKey || e.ctrlKey) return;
      if (t?.closest("input, textarea, select, [contenteditable], [role=listbox], [role=dialog]")) return;
      if (e.key === "ArrowLeft") prev();
      else if (e.key === "ArrowRight") next();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [count, next, prev]);

  // trackpad 가로 스와이프. React onWheel은 passive라 preventDefault(브라우저 뒤로가기 제스처 방지)를 위해 직접 등록
  useEffect(() => {
    const el = stage;
    if (!el || count < 2) return;
    let acc = 0;
    let lockedUntil = 0;
    let idle: ReturnType<typeof setTimeout> | undefined;
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return; // 세로 스크롤은 그대로
      e.preventDefault();
      const now = performance.now();
      if (now < lockedUntil) return;
      acc += e.deltaX;
      clearTimeout(idle);
      idle = setTimeout(() => (acc = 0), 200);
      if (Math.abs(acc) >= WHEEL_STEP_PX) {
        setIndex((i) => (((i + Math.sign(acc)) % count) + count) % count);
        acc = 0;
        lockedUntil = now + WHEEL_LOCK_MS;
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("wheel", onWheel);
      clearTimeout(idle);
    };
  }, [stage, count]);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    start.current = { x: e.clientX, y: e.clientY, pointer: e.pointerId };
    suppressClick.current = false;
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      const s = start.current;
      if (!s || s.pointer !== e.pointerId) return;
      const dx = e.clientX - s.x;
      if (!dragging) {
        // 세로 스크롤 의도면 드래그로 보지 않는다
        if (Math.abs(e.clientY - s.y) > Math.abs(dx)) {
          if (Math.abs(e.clientY - s.y) > DRAG_START_PX) start.current = null;
          return;
        }
        if (Math.abs(dx) < DRAG_START_PX) return;
        setDragging(true);
        e.currentTarget.setPointerCapture(e.pointerId);
      }
      setDragPx(count < 2 ? dx * RUBBER_BAND : dx);
    },
    [count, dragging],
  );

  const endDrag = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      const s = start.current;
      start.current = null;
      if (!s || !dragging) return;
      const dx = e.clientX - s.x;
      setDragging(false);
      setDragPx(0);
      // 드래그 직후 같은 tick의 click만 무시한다 (다음 클릭에는 영향 없음)
      suppressClick.current = true;
      setTimeout(() => {
        suppressClick.current = false;
      }, 0);
      // 1/4 카드 이상 끌면 넘긴다. 크게 끌면 여러 장
      const steps = count < 2 || Math.abs(dx) < spread * 0.25 ? 0 : Math.max(1, Math.round(Math.abs(dx) / spread));
      if (steps) setIndex((i) => (((i - Math.sign(dx) * steps) % count) + count) % count);
    },
    [count, dragging, spread],
  );

  const consumeClick = useCallback(() => {
    const suppressed = suppressClick.current;
    suppressClick.current = false;
    return suppressed;
  }, []);

  return {
    index,
    go,
    next,
    prev,
    dragPx,
    dragging,
    consumeClick,
    attachStage,
    handlers: { onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag },
  };
}

/** index i 카드의 active 기준 상대 위치. 4장 이상이면 순환 배치 */
export function offsetOf(i: number, active: number, count: number) {
  let o = i - active;
  if (count >= 4) {
    if (o > count / 2) o -= count;
    if (o < -count / 2) o += count;
  }
  return o;
}
