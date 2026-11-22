"use client";

import { useEffect, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { ReviewAlerts } from "@/components/review/review-alerts";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/card";
import { resetDemoState, setDemoMetric } from "@/lib/client-api";
import { cn } from "@/lib/utils";
import type { DemoChange, DemoState, ReviewStatus } from "@/lib/types";
import { clock, formatDemoValue, secondsToNextCheck } from "./format";
import { MetricControl } from "./metric-control";
import { ReviewConditions } from "./review-conditions";
import { pendingDecisions, useDemoState } from "./use-demo-state";

/** Apply / Reset 1회의 결과. 변경 직전 Review Monitor 기록을 저장해 두고, 이후 monitor가 바꾼 결정을 보여 준다 */
interface Activity {
  id: number;
  label: string;
  changes: DemoChange[];
  before: Record<string, ReviewStatus | null>;
  titles: Record<string, string>;
}

function monitorSnapshot(state: DemoState) {
  const before: Record<string, ReviewStatus | null> = {};
  const titles: Record<string, string> = {};
  for (const d of state.metrics.flatMap((m) => m.decisions)) {
    before[d.decision_id] = d.monitor_status;
    titles[d.decision_id] = d.title;
  }
  return { before, titles };
}

export function DemoControlCenter({ initial }: { initial: DemoState }) {
  const { state, error, refresh, waiting } = useDemoState(initial);
  const [projectFilter, setProjectFilter] = useState<number | "all">("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // 다음 monitor 확인까지 카운트다운 표시용
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  async function run(label: string, key: string, fn: () => Promise<DemoChange[]>) {
    setBusy(key);
    setActionError(null);
    const snap = monitorSnapshot(state);
    try {
      const changes = await fn();
      setActivity({ id: Date.now(), label, changes, ...snap });
      await refresh();
      return true;
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const apply = (metric: string, value: number) =>
    run("Updated", metric, async () => [await setDemoMetric(metric, value)]);

  const simulatedProjects = state.projects.filter((p) => p.simulated);
  const next = secondsToNextCheck(state.monitor.last_run_at, state.monitor.interval_seconds, now);
  const visibleConditions = state.conditions.filter((c) => projectFilter === "all" || c.project_id === projectFilter);

  return (
    <>
      <div className="mb-10 flex flex-col gap-6 sm:mb-12 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="eyebrow mb-3 flex items-center gap-2">
            Demo
            <span className="rounded border border-line-strong px-1 py-px text-[9px] tracking-[0.08em]">DEV · Presentation only</span>
          </div>
          <h1 className="text-[34px] font-semibold leading-[1.04] tracking-[-0.035em] sm:text-[44px]">Demo Control Center</h1>
          <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-muted">
            Simulate external system state and trigger real Decision review workflows.
            <br />
            실제 Review Monitor가 읽는 외부 상태값만 바꿉니다. Decision 상태와 알림은 기존 시스템이 판정합니다.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-start gap-2 lg:items-end">
          <Button variant="secondary" onClick={() => run("Reset", "reset", resetDemoState)} disabled={busy !== null}>
            {busy === "reset" ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
            Reset Demo State
          </Button>
          <p className="text-xs text-muted">
            Review Monitor {state.monitor.enabled ? `· every ${state.monitor.interval_seconds}s · last check ${clock(state.monitor.last_run_at)}` : "· disabled"}
          </p>
        </div>
      </div>

      {/* 실제 Web Alert (기존 review alert polling 그대로) */}
      {simulatedProjects.map((p) => (
        <ReviewAlerts key={p.id} projectId={p.id} />
      ))}

      {(actionError || error) && (
        <p className="mb-8 border-l-2 border-danger bg-danger-soft px-4 py-3 text-sm text-danger" role="alert">
          {actionError ?? `상태를 새로 읽지 못했습니다: ${error}`}
        </p>
      )}

      {activity && <ActivityFeedback activity={activity} state={state} waiting={waiting} next={next} onClose={() => setActivity(null)} />}

      <div className="mb-8 flex flex-wrap items-center gap-3 text-sm">
        <label htmlFor="demo-project" className="eyebrow">
          Project
        </label>
        <select
          id="demo-project"
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value === "all" ? "all" : Number(e.target.value))}
          className="h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm outline-none focus:border-ink"
        >
          <option value="all">All projects</option>
          {state.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.simulated ? "" : " (no metric source)"}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted">
          metric source <span className="font-mono">{state.metric_source}</span> · {state.metric_tool_url ?? "not configured"}
        </span>
      </div>

      <Section eyebrow="01 — External state simulator" title="Current state" count={state.metrics.length}
        description="State Resolver(check_metric)가 실제로 읽는 sample DB 값입니다.">
        <div>
          {state.metrics.map((m) => (
            <MetricControl key={m.name} metric={m} projectFilter={projectFilter} busy={busy !== null} onApply={apply} />
          ))}
        </div>
      </Section>

      <Section eyebrow="02 — Read-only" title="Review conditions" count={visibleConditions.length} className="mt-16"
        description="시뮬레이터가 없는 조건은 조회만 합니다. 가짜 입력으로 조건을 만족시키지 않습니다.">
        <ReviewConditions conditions={visibleConditions} />
      </Section>

      <Section eyebrow="03 — Presentation" title="Demo scenarios" className="mt-16"
        description="Run은 외부 상태값 하나만 바꿉니다. REVIEW_DUE / Telegram은 Review Monitor가 다음 확인 때 처리합니다.">
        <ul className="divide-y divide-line border-y border-line">
          {state.scenarios.map((s) => {
            const metric = state.metrics.find((m) => m.name === s.metric);
            const current = metric?.value ?? null;
            return (
              <li key={s.id} className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:gap-8">
                <div className="min-w-0 flex-1">
                  <div className="text-[15px] font-medium">{s.title}</div>
                  <div className="mt-1 font-mono text-xs text-muted">
                    {s.metric} {formatDemoValue(current)} → {formatDemoValue(s.value, s.unit)}
                  </div>
                </div>
                <div className={cn("text-sm tabular-nums", s.review_due ? "text-accent-ink" : "text-muted")}>
                  {s.review_due} decisions REVIEW_DUE expected
                </div>
                <Button
                  size="sm"
                  disabled={busy !== null || current === s.value}
                  onClick={() => run(s.title, s.id, async () => [await setDemoMetric(s.metric, s.value)])}
                  className="w-20"
                >
                  {busy === s.id ? <Loader2 className="size-3.5 animate-spin" /> : current === s.value ? "Applied" : "Run"}
                </Button>
              </li>
            );
          })}
          <li className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:gap-8">
            <div className="min-w-0 flex-1">
              <div className="text-[15px] font-medium">Reset all</div>
              <div className="mt-1 font-mono text-xs text-muted">
                {state.metrics
                  .filter((m) => m.baseline !== null)
                  .map((m) => `${m.name} = ${formatDemoValue(m.baseline)}`)
                  .join(" · ")}
              </div>
            </div>
            <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => run("Reset", "reset", resetDemoState)} className="w-20">
              {busy === "reset" ? <Loader2 className="size-3.5 animate-spin" /> : "Reset"}
            </Button>
          </li>
        </ul>
      </Section>
    </>
  );
}

/** Apply 후: 바뀐 값 → Review Monitor 대기 → 결과(어떤 결정이 REVIEW_DUE / ACTIVE가 됐는지) */
function ActivityFeedback({
  activity,
  state,
  waiting,
  next,
  onClose,
}: {
  activity: Activity;
  state: DemoState;
  waiting: boolean;
  next: number | null;
  onClose: () => void;
}) {
  const nowStatus = new Map(state.metrics.flatMap((m) => m.decisions).map((d) => [d.decision_id, d.monitor_status]));
  const transitions = Object.entries(activity.before)
    .filter(([id, before]) => nowStatus.has(id) && nowStatus.get(id) !== before)
    .map(([id, before]) => ({ id, title: activity.titles[id], before, after: nowStatus.get(id) }));
  const becameDue = transitions.filter((t) => t.after === "REVIEW_DUE");
  const recovered = transitions.filter((t) => t.after === "ACTIVE");
  const pending = pendingDecisions(state);

  return (
    <div className="animate-rise mb-10 border border-ink bg-surface px-5 py-5 sm:px-6" role="status" aria-live="polite">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="eyebrow">{activity.label}</div>
          {activity.changes.length === 0 ? (
            <p className="mt-2 text-sm text-muted">이미 baseline 상태입니다. 바뀐 값이 없습니다.</p>
          ) : (
            <ul className="mt-2 space-y-1">
              {activity.changes.map((c) => (
                <li key={c.metric} className="text-[15px]">
                  <span className="font-mono text-sm text-muted">{c.metric}</span>{" "}
                  <span className="font-semibold tabular-nums">
                    {formatDemoValue(c.before)} → {formatDemoValue(c.after, c.unit)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button onClick={onClose} className="text-sm text-muted hover:text-ink" aria-label="닫기">
          ×
        </button>
      </div>
      <div className="mt-4 border-t border-line pt-4 text-sm">
        {waiting ? (
          <p className="flex items-center gap-2 text-ink-2">
            <Loader2 className="size-4 animate-spin" />
            Waiting for Review Monitor… {pending.length} decisions to re-check
            {next !== null && <span className="text-muted">· next check {next > 0 ? `in ~${next}s` : "any moment"}</span>}
          </p>
        ) : transitions.length === 0 ? (
          <p className="text-muted">Review Monitor 확인 완료 — 상태가 바뀐 결정이 없습니다.</p>
        ) : (
          <div className="space-y-1.5">
            {becameDue.length > 0 && (
              <p className="font-semibold text-accent-ink">
                {becameDue.length} decisions became REVIEW_DUE
                <span className="ml-2 font-normal text-muted">→ Web alert · Telegram 알림은 Review Monitor가 이벤트마다 1회 발송</span>
              </p>
            )}
            {recovered.length > 0 && <p className="font-semibold">{recovered.length} decisions recovered to ACTIVE</p>}
            <ul className="text-xs text-ink-2">
              {transitions.map((t) => (
                <li key={t.id}>
                  • {t.title} <span className="text-muted">{t.before ?? "—"} → {t.after}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
