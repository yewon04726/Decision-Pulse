"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Bot, ChevronDown, ChevronUp, FileText, Loader2, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { MemoMarkdown } from "@/components/review/memo-markdown";
import { generateReviewMemo, getReviewMemo, getReviewMemos, ReviewMemoApiError } from "@/lib/client-api";
import { formatNumber } from "@/lib/metrics";
import type { ReviewMemo } from "@/lib/types";

const POLL_MS = 3000;

const DETECTED_BY: Record<ReviewMemo["detected_by"], string> = {
  review_event: "상태 전환 (ACTIVE → REVIEW_DUE)",
  review_state_baseline: "baseline (첫 모니터링부터 조건 충족)",
};

function time(iso: string | null) {
  return iso ? iso.replace("T", " ").slice(0, 19) : "-";
}

/**
 * REVIEW_DUE 결정의 Review Memo (Hermes가 Decision Debt read-only MCP + gbrain read-only MCP로 작성).
 * advisory 산출물 — 이 패널은 Decision / review 상태를 바꾸지 않는다. 최종 판단(Keep/Revise/Supersede)은 사람이 한다.
 */
export function ReviewMemoPanel({ decisionId, reviewDue }: { decisionId: string; reviewDue: boolean }) {
  const [memos, setMemos] = useState<ReviewMemo[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [open, setOpen] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await getReviewMemos(decisionId);
      setMemos(list);
      return list;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, [decisionId]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load]);

  const generating = memos?.find((m) => m.status === "generating") ?? null;
  const generatingId = generating?.id ?? null;

  // 생성 중이면 끝날 때까지 polling → 끝나면 목록을 다시 읽고 새 memo를 연다
  useEffect(() => {
    if (generatingId === null) return;
    const timer = setInterval(async () => {
      try {
        const m = await getReviewMemo(generatingId);
        if (m.status !== "generating") {
          await load();
          setSelectedId(m.id);
          setOpen(true);
        }
      } catch {
        // 다음 주기에 다시 확인
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [generatingId, load]);

  async function request(regenerate: boolean) {
    setRequesting(true);
    setError(null);
    try {
      const memo = await generateReviewMemo(decisionId, regenerate);
      await load();
      setSelectedId(memo.id);
      setOpen(true);
    } catch (e) {
      if (e instanceof ReviewMemoApiError && e.code === "memo_generating") {
        await load(); // 이미 생성 중 → 그 memo를 기다린다 (중복 실행 없음)
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setRequesting(false);
    }
  }

  if (memos === null && !error) {
    return <div className="mb-14 h-20 animate-pulse rounded-xl bg-soft" />;
  }
  if (!reviewDue && (memos?.length ?? 0) === 0) return null;

  const latestCompleted = memos?.find((m) => m.status === "completed") ?? null;
  const selected = memos?.find((m) => m.id === selectedId) ?? memos?.find((m) => m.status !== "generating") ?? null;
  const busy = requesting || generating !== null;

  return (
    <Card id="review-memo" className="mb-14 scroll-mt-32 border-ink">
      <CardHeader
        className="flex-col sm:flex-row"
        title={
          <span className="inline-flex items-center gap-1.5">
            <Bot className="size-4" />
            Review Memo
          </span>
        }
        description="Hermes가 Decision Debt(read-only)와 gbrain(read-only)만 조회해 작성한 검토 메모입니다. 참고용이며 결정을 바꾸지 않습니다."
        action={
          <div className="flex flex-wrap gap-2 sm:shrink-0 sm:justify-end">
            {latestCompleted && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setSelectedId(latestCompleted.id);
                  setOpen((v) => (selected?.id === latestCompleted.id ? !v : true));
                }}
              >
                <FileText className="size-3.5" />
                View Review Memo
                {open && selected?.id === latestCompleted.id ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
              </Button>
            )}
            {reviewDue && (
              <Button size="sm" variant={latestCompleted ? "secondary" : "primary"} disabled={busy} onClick={() => request(!!latestCompleted)}>
                {busy ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : latestCompleted ? (
                  <RefreshCw className="size-3.5" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                {latestCompleted ? "Regenerate" : "Generate Review Memo"}
              </Button>
            )}
          </div>
        }
      />

      <div className="space-y-5 p-5 sm:p-6">
        {error && <p className="rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}</p>}

        {generating && (
          <div className="flex items-start gap-2 rounded-lg bg-soft p-3 text-sm text-ink">
            <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin" />
            <div>
              <div className="font-semibold">Generating… (memo #{generating.id})</div>
              <p className="text-xs">
                Hermes가 list_review_due → get_decision_detail → gbrain 검색 순서로 조회하고 있습니다. 보통 1~2분 걸립니다.
              </p>
            </div>
          </div>
        )}

        {!generating && (memos?.length ?? 0) === 0 && (
          <p className="text-sm text-muted">아직 Review Memo가 없습니다. Generate Review Memo를 누르면 생성합니다.</p>
        )}

        {selected && open && <MemoView memo={selected} />}

        {memos && memos.length > 1 && (
          <div className="border-t border-line pt-3">
            <div className="eyebrow text-[10px]">History</div>
            <ul className="mt-2 flex flex-wrap gap-2">
              {memos.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedId(m.id);
                      setOpen(true);
                    }}
                    className={`rounded-md border px-2 py-1 text-xs ${
                      m.id === selected?.id ? "border-ink bg-ink text-white" : "border-line text-ink-2 hover:border-ink"
                    }`}
                  >
                    #{m.id} · {m.status} · {time(m.generated_at ?? m.created_at)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}

function MemoView({ memo }: { memo: ReviewMemo }) {
  if (memo.status === "generating") return null;
  if (memo.status === "failed") {
    return (
      <div className="rounded-lg border border-danger/25 bg-danger-soft p-3 text-sm text-danger">
        <div className="flex items-center gap-1.5 font-semibold">
          <AlertTriangle className="size-4" />
          Review Memo generation failed.
        </div>
        <p className="mt-1">Decision and review state were not changed.</p>
        {memo.error && <p className="mt-2 font-mono text-xs text-danger">{memo.error}</p>}
        <p className="mt-1 text-xs text-danger">
          memo #{memo.id} · {time(memo.generated_at)}
        </p>
      </div>
    );
  }
  const observed = memo.observed_snapshot?.observed;
  const audit = memo.tool_audit;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-lg bg-soft px-3 py-2 text-xs text-ink-2">
        <span>memo #{memo.id}</span>
        <span>생성 {time(memo.generated_at)}</span>
        <span>
          {memo.provider ?? "-"} · {memo.model ?? "-"} · prompt {memo.prompt_version}
        </span>
        <span>감지: {DETECTED_BY[memo.detected_by]}</span>
        {memo.observed_snapshot?.trigger_text && (
          <span>
            조건 <span className="font-mono">{memo.observed_snapshot.trigger_text}</span>
          </span>
        )}
        {observed && (
          <span>
            생성 시점 관측값 <span className="font-mono">{observed.metric}</span> = {formatNumber(observed.value)} {observed.unit ?? ""}
          </span>
        )}
      </div>
      {audit && (
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
          <ShieldCheck className="size-3.5 text-ink" />
          사용한 tool:
          {audit.tools_used.map((t) => (
            <span key={t} className="rounded bg-soft px-1.5 py-0.5 font-mono text-ink-2">
              {t.replace(/^mcp__/, "")}
            </span>
          ))}
        </div>
      )}
      <div className="rounded-xl border border-line px-5 py-5 sm:px-6">
        <MemoMarkdown content={memo.content ?? ""} />
      </div>
      {/* 이후 단계: Keep / Revise / Supersede workflow가 여기에 붙는다 (이번에는 표시만) */}
      <p className="border-l-2 border-ink bg-soft px-3 py-2 text-xs font-medium text-ink">
        Final decision is human-controlled. 이 메모는 Decision, Review Event, Candidate를 변경하지 않습니다.
      </p>
    </div>
  );
}
