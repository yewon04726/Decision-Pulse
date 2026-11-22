"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { StatusBadge } from "@/components/decision/status-badge";
import { Button } from "@/components/ui/button";
import { previewDemoMetric } from "@/lib/client-api";
import { cn } from "@/lib/utils";
import type { DemoMetricState, DemoPreview } from "@/lib/types";
import { clock, formatDemoValue, metricTitle } from "./format";

const SIMULATOR_LABEL = {
  users_table: "users table row 수 (set_user_count.py)",
  metrics_store: "metrics table 값 (set_metric.py)",
} as const;

/**
 * 외부 상태값 1개: 현재값 + preset + 직접 입력 → 미리보기(backend 판정 함수) → Apply.
 * Apply는 값만 바꾼다. 결정 상태는 Review Monitor가 다음 확인 때 바꾼다.
 */
export function MetricControl({
  metric,
  projectFilter,
  busy,
  onApply,
}: {
  metric: DemoMetricState;
  projectFilter: number | "all";
  busy: boolean;
  onApply: (metric: string, value: number) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState<DemoPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);

  const decisions = metric.decisions.filter((d) => projectFilter === "all" || d.project_id === projectFilter);
  const value = draft.trim() === "" ? null : Number(draft);
  const valid = value !== null && Number.isFinite(value) && value >= 0 && (!metric.integer || Number.isInteger(value));
  const changed = valid && value !== metric.value;

  // 입력이 바뀌면 300ms 뒤 미리보기 (backend evaluate_trigger로 계산 — frontend에서 판정하지 않는다)
  useEffect(() => {
    if (!changed || value === null || !metric.simulator) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const p = await previewDemoMetric(metric.name, value);
        if (!cancelled) {
          setPreview(p);
          setPreviewError(null);
        }
      } catch (e) {
        if (!cancelled) setPreviewError(e instanceof Error ? e.message : String(e));
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [changed, value, metric.name, metric.simulator]);

  async function apply() {
    if (value === null || !valid) {
      setInputError(metric.integer ? "0 이상의 정수를 입력하세요." : "0 이상의 숫자를 입력하세요.");
      return;
    }
    setInputError(null);
    if (await onApply(metric.name, value)) {
      setDraft("");
      setPreview(null);
    }
  }

  const shownPreview = changed && preview && preview.value === value ? preview : null;
  const chips = [
    ...(metric.baseline !== null ? [{ value: metric.baseline, label: "baseline", review_due: null as number | null }] : []),
    ...metric.presets,
  ]
    .filter((p, i, all) => all.findIndex((q) => q.value === p.value) === i)
    .sort((a, b) => a.value - b.value);

  return (
    <article className="grid gap-10 border-b border-line py-10 first:pt-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-14">
      {/* 현재값 + 조작 */}
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h3 className="text-lg font-semibold tracking-[-0.02em]">{metricTitle(metric.name)}</h3>
          <span className="font-mono text-xs text-muted">{metric.name}</span>
        </div>
        <div className="mt-4 text-[44px] font-semibold leading-none tracking-[-0.045em] tabular-nums sm:text-[52px]">
          {metric.error ? <span className="text-2xl text-danger">읽을 수 없음</span> : formatDemoValue(metric.value, metric.unit)}
        </div>
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-muted">
          <dt>source</dt>
          <dd className="font-mono text-ink-2">{metric.source ?? "—"}</dd>
          <dt>observed</dt>
          <dd className="tabular-nums text-ink-2">{clock(metric.observed_at)}</dd>
          <dt>writes</dt>
          <dd className="text-ink-2">{metric.simulator ? SIMULATOR_LABEL[metric.simulator] : "—"}</dd>
        </dl>
        {metric.error && <p className="mt-3 text-sm text-danger">{metric.error}</p>}

        {metric.simulator ? (
          <div className="mt-7">
            <div className="eyebrow text-[10px]">Presets · trigger 경계</div>
            <div className="mt-2.5 flex flex-wrap gap-2">
              {chips.map((p) => {
                const current = p.value === metric.value;
                const selected = value === p.value;
                return (
                  <button
                    key={p.value}
                    type="button"
                    disabled={busy}
                    onClick={() => setDraft(String(p.value))}
                    title={p.label}
                    className={cn(
                      "flex flex-col items-start rounded-lg border px-3 py-2 text-left transition-colors disabled:opacity-50",
                      selected ? "border-ink bg-ink text-white" : current ? "border-ink bg-surface" : "border-line-strong bg-surface hover:border-ink",
                    )}
                  >
                    <span className="text-sm font-semibold tabular-nums">{formatDemoValue(p.value)}</span>
                    <span className={cn("text-[10.5px]", selected ? "text-white/70" : "text-muted")}>
                      {p.review_due === null ? "baseline" : `${p.review_due} due`}
                      {current && " · now"}
                    </span>
                  </button>
                );
              })}
            </div>

            <form
              className="mt-4 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                apply();
              }}
            >
              <input
                inputMode={metric.integer ? "numeric" : "decimal"}
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setInputError(null);
                }}
                placeholder="직접 입력"
                aria-label={`${metric.name} 값 직접 입력`}
                className="h-10 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-sm tabular-nums outline-none transition-colors placeholder:text-faint focus:border-ink"
              />
              <Button type="submit" disabled={busy || !changed}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                Apply
              </Button>
            </form>
            {inputError && <p className="mt-2 text-xs text-danger">{inputError}</p>}
            {draft.trim() !== "" && !valid && !inputError && (
              <p className="mt-2 text-xs text-danger">{metric.integer ? "0 이상의 정수만 가능합니다." : "0 이상의 숫자만 가능합니다."}</p>
            )}

            {/* 미리보기 */}
            {shownPreview && (
              <div className="animate-rise mt-4 border-l-2 border-ink bg-surface px-4 py-3 text-sm">
                <p className="font-medium">
                  {formatDemoValue(shownPreview.value, shownPreview.unit)}로 변경하면{" "}
                  <span className="tabular-nums">{shownPreview.affected}</span> decisions 상태가 바뀝니다
                </p>
                <ul className="mt-2 space-y-1 text-xs text-ink-2">
                  {shownPreview.decisions
                    .filter((d) => d.current !== d.predicted)
                    .map((d) => (
                      <li key={d.decision_id}>
                        • {d.title} <span className="text-muted">({d.trigger})</span> → {d.predicted === "REVIEW_DUE" ? "REVIEW_DUE 예상" : `${d.predicted} 예상`}
                      </li>
                    ))}
                  {shownPreview.affected === 0 && <li className="text-muted">상태가 바뀌는 결정이 없습니다.</li>}
                </ul>
                <p className="mt-2 text-[11px] text-faint">backend 판정 함수로 계산한 예상입니다. 실제 변경은 Review Monitor가 다음 확인 때 합니다.</p>
              </div>
            )}
            {previewError && <p className="mt-2 text-xs text-danger">{previewError}</p>}
          </div>
        ) : (
          <p className="mt-6 border-l-2 border-line-strong pl-3 text-sm text-muted">
            Not simulator-backed — 이 metric의 source는 Demo에서 바꿀 수 없습니다.
          </p>
        )}
      </div>

      {/* 영향받는 결정 */}
      <div className="min-w-0">
        <div className="flex items-baseline justify-between">
          <h4 className="eyebrow">Affected decisions · {metric.name}</h4>
          <span className="text-sm font-semibold tabular-nums">{decisions.length}</span>
        </div>
        {decisions.length === 0 ? (
          <p className="mt-4 text-sm text-muted">이 metric을 쓰는 자동 판정 결정이 없습니다{projectFilter !== "all" && " (선택한 프로젝트)"}.</p>
        ) : (
          <ul className="mt-3 divide-y divide-line border-y border-line">
            {decisions.map((d) => {
              const waiting = d.monitor_status !== null && d.status !== d.monitor_status && d.status !== "UNKNOWN";
              return (
                <li key={d.decision_id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 py-3.5">
                  <Link
                    href={`/decisions/${d.decision_id}`}
                    className="truncate text-sm font-medium text-ink underline-offset-4 hover:underline"
                    title={d.title}
                  >
                    {d.title}
                  </Link>
                  <StatusBadge status={d.monitor_status === "REVIEW_DUE" ? "REVIEW_DUE" : d.monitor_status === "UNKNOWN" ? "UNKNOWN" : "ACTIVE"} />
                  <span className="truncate font-mono text-[11.5px] text-muted">
                    {d.trigger} · {d.decision_id}
                  </span>
                  <span className={cn("text-right text-[11px]", waiting ? "text-accent-ink" : "text-faint")}>
                    {waiting ? `→ ${d.status} · waiting for monitor` : d.monitor_checked_at ? `checked ${clock(d.monitor_checked_at)}` : "not checked yet"}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {metric.compound_decisions.length > 0 && (
          <p className="mt-3 text-xs leading-relaxed text-muted">
            compound 조건에도 쓰입니다 ({metric.compound_decisions.join(", ")}) — compound는 자동 판정하지 않으므로 이 값만으로 REVIEW_DUE가 되지 않습니다.
          </p>
        )}
      </div>
    </article>
  );
}
