"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BellRing, Check, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { acknowledgeReviewEvent, getOpenReviewAlerts } from "@/lib/client-api";
import { formatNumber, formatReviewTrigger } from "@/lib/metrics";
import type { ReviewEvent } from "@/lib/types";

const POLL_MS = 10_000;

function time(iso: string) {
  return iso.replace("T", " ").slice(0, 19);
}

/**
 * backend review monitor가 감지한 REVIEW_DUE 알림 (새로고침 없이 10초마다 polling).
 * 새 알림이 오면 router.refresh()로 서버 컴포넌트(Dashboard 상태 등)도 다시 그린다.
 */
export function ReviewAlerts({ projectId }: { projectId: number }) {
  const router = useRouter();
  const [events, setEvents] = useState<ReviewEvent[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const seen = useRef<Set<number> | null>(null);

  const poll = useCallback(async () => {
    if (document.hidden) return; // 탭이 안 보일 때는 건너뛴다
    try {
      const next = await getOpenReviewAlerts(projectId);
      const ids = new Set(next.map((e) => e.id));
      const fresh = seen.current !== null && next.some((e) => !seen.current!.has(e.id));
      seen.current = ids;
      setEvents(next);
      if (fresh) router.refresh(); // 새 REVIEW_DUE → 화면의 결정 상태도 갱신 (브라우저 새로고침 아님)
    } catch {
      // backend가 잠시 꺼져 있어도 다음 주기에 다시 시도한다 (서버 상태 안내는 각 페이지에서 표시)
    }
  }, [projectId, router]);

  useEffect(() => {
    const first = setTimeout(poll, 0);
    const timer = setInterval(poll, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [poll]);

  async function acknowledge(id: number) {
    setBusy(id);
    try {
      await acknowledgeReviewEvent(id);
      setEvents((prev) => prev.filter((e) => e.id !== id));
    } finally {
      setBusy(null);
    }
  }

  if (events.length === 0) return null;

  return (
    <div className="animate-rise mb-12 border-l-2 border-accent bg-surface px-5 py-4 sm:px-6" role="alert">
      <div className="flex items-center gap-2.5 text-sm font-semibold text-ink">
        <BellRing className="size-4 text-accent-ink" />
        재검토가 필요한 결정이 있습니다
        <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold tabular-nums text-white">{events.length}</span>
      </div>
      <ul className="mt-2 divide-y divide-line">
        {events.map((e) => (
          <li key={e.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 space-y-1">
              <Link href={`/decisions/${e.decision_id}`} className="text-[15px] font-medium text-ink underline-offset-4 hover:underline">
                {e.decision_title ?? e.decision_id}
              </Link>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                {Object.entries(e.observed ?? {}).map(([name, m]) => (
                  <span key={name}>
                    현재 <span className="font-mono">{name}</span>:{" "}
                    <span className="font-semibold text-accent-ink">
                      {formatNumber(m.value)} {m.unit}
                    </span>
                  </span>
                ))}
                <span>
                  재검토 조건: <span className="font-mono">{formatReviewTrigger(e.trigger)}</span>
                </span>
                <span className="tabular-nums">감지 시각: {time(e.detected_at)}</span>
              </div>
              {e.resolved_at && (
                <p className="text-xs text-muted">이후 조건이 다시 풀렸습니다 ({time(e.resolved_at)})</p>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <Link
                href={`/decisions/${e.decision_id}#review-memo`}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-ink px-3 text-xs font-medium text-white transition-colors hover:bg-ink-2"
              >
                <Sparkles className="size-3.5" />
                Review Memo
              </Link>
              <Button variant="secondary" size="sm" disabled={busy !== null} onClick={() => acknowledge(e.id)} className="shrink-0">
                {busy === e.id ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                확인
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
