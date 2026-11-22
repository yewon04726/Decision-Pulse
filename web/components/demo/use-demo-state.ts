"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getDemoState } from "@/lib/client-api";
import type { DemoDecisionImpact, DemoState } from "@/lib/types";

const FAST_MS = 3_000; // Review Monitor 판정을 기다리는 동안
const SLOW_MS = 10_000;

/** 현재값 판정(status)과 Review Monitor 기록(monitor_status)이 다른 결정 = 아직 monitor가 확인하지 않음 */
export function pendingDecisions(state: DemoState): DemoDecisionImpact[] {
  return state.metrics
    .flatMap((m) => m.decisions)
    .filter((d) => (d.status === "ACTIVE" || d.status === "REVIEW_DUE") && d.monitor_status !== null && d.status !== d.monitor_status);
}

/**
 * Demo 상태 polling. 새 websocket 없이 backend GET /api/demo/state를 주기적으로 읽는다.
 * monitor 판정을 기다리는 결정이 있으면 3초, 아니면 10초. 탭이 안 보이면 건너뛴다.
 */
export function useDemoState(initial: DemoState) {
  const [state, setState] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    setLoading(true);
    try {
      setState(await getDemoState());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, []);

  const waiting = pendingDecisions(state).length > 0;
  useEffect(() => {
    const timer = setInterval(() => {
      if (!document.hidden) refresh();
    }, waiting ? FAST_MS : SLOW_MS);
    return () => clearInterval(timer);
  }, [refresh, waiting]);

  return { state, error, loading, refresh, waiting };
}
