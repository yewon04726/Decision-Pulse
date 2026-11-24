"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, ChevronDown, ChevronUp, Circle, ExternalLink, Loader2, Network, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { DeepReviewApiError, getDeepReview, getDeepReviews, getDeepReviewStatus, startDeepReview } from "@/lib/client-api";
import type {
  DeepReviewAnalysis,
  DeepReviewArtifact,
  DeepReviewCritique,
  DeepReviewResearch,
  DeepReviewRun,
  DeepReviewStage,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;
const ACTIVE = new Set(["PENDING", "RESEARCHING", "ANALYZING", "CRITIQUING", "REVISING"]);

const STATUS_TEXT: Record<DeepReviewRun["status"], string> = {
  PENDING: "Starting",
  RESEARCHING: "Evidence research",
  ANALYZING: "Decision analysis",
  CRITIQUING: "Critical review",
  REVISING: "Revising analysis",
  COMPLETED: "Completed",
  HUMAN_REVIEW_REQUIRED: "Human review required",
  FAILED: "Failed",
};

export type DeepReviewMode = "REVIEW_DUE" | "ACTIVE" | "UNKNOWN" | "NEEDS_MAPPING" | "MANUAL";

/** 실행 전 안내 문구. REVIEW_DUE만 강하게, 나머지는 사람이 필요할 때 쓰는 보조 기능으로 표시한다 */
const MODE_TEXT: Record<DeepReviewMode, string> = {
  REVIEW_DUE: "재검토 조건이 충족되었습니다.",
  ACTIVE: "현재 자동 재검토 조건은 충족되지 않았습니다. 필요하면 수동으로 심층 검토할 수 있습니다.",
  UNKNOWN: "현재값을 읽지 못해 자동 판정할 수 없습니다. 필요하면 수동으로 심층 검토할 수 있습니다.",
  NEEDS_MAPPING: "현재 자동화된 관측 데이터가 연결되지 않았습니다. 현재 상태 없이도 저장된 근거를 기반으로 심층 검토할 수 있습니다.",
  MANUAL: "이 Decision은 사람이 직접 재검토 시점을 판단해야 합니다.",
};

const REASON_TEXT: Record<DeepReviewRun["review_reason"], string> = {
  TRIGGERED: "Triggered review",
  MANUAL: "Manual review",
};

const CURRENT_VALUE_TEXT: Record<string, string> = {
  metric_not_mapped: "현재 관측값 없음 — 지표가 데이터 소스에 연결되지 않음 (metric not mapped)",
  manual_trigger: "현재 상태 판단 불가 — 사람이 판단하는 조건 (manual trigger)",
  read_failed: "현재 관측값 없음 — 마지막 확인에서 값을 읽지 못함",
  not_observed: "현재 관측값 없음 — 아직 기록되지 않음",
};

function time(iso: string | null) {
  return iso ? iso.replace("T", " ").slice(0, 19) : "-";
}

function latest<T>(run: DeepReviewRun, role: DeepReviewArtifact["role"]): T | null {
  const arts = run.artifacts.filter((a) => a.role === role).sort((a, b) => b.iteration - a.iteration);
  return (arts[0]?.payload as T | undefined) ?? null;
}

/**
 * Deep Review — Paperclip 조직(Evidence Researcher → Decision Analyst → Review Critic)의 교차 검증 검토.
 * 기존 Review Memo(single agent)와 별개. advisory 산출물: Decision / review 상태를 바꾸지 않는다. 최종 판단은 사람.
 */
export function DeepReviewPanel({
  decisionId,
  projectId,
  mode,
}: {
  decisionId: string;
  projectId: number;
  /** null = 실행할 수 없는 Decision (대체됨 / 승인 대기). 기존 결과는 계속 보여준다 */
  mode: DeepReviewMode | null;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [runs, setRuns] = useState<DeepReviewRun[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await getDeepReviews(decisionId);
      setRuns(list);
      return list;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, [decisionId]);

  useEffect(() => {
    const first = setTimeout(() => {
      load();
      getDeepReviewStatus()
        .then((s) => setEnabled(s.enabled))
        .catch(() => setEnabled(false));
    }, 0);
    return () => clearTimeout(first);
  }, [load]);

  const active = runs?.find((r) => ACTIVE.has(r.status)) ?? null;
  const activeId = active?.id ?? null;

  // 진행 중이면 3초마다 상태 확인 (Review Memo와 같은 polling 방식)
  useEffect(() => {
    if (activeId === null) return;
    const timer = setInterval(async () => {
      try {
        const run = await getDeepReview(activeId);
        setRuns((prev) => prev?.map((r) => (r.id === run.id ? run : r)) ?? [run]);
      } catch {
        // 다음 주기에 다시 확인
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [activeId]);

  async function run() {
    setRequesting(true);
    setError(null);
    try {
      const started = await startDeepReview(decisionId, projectId);
      await load();
      setSelectedId(started.id);
    } catch (e) {
      if (e instanceof DeepReviewApiError && e.code === "deep_review_running") {
        await load(); // 이미 진행 중 → 그 run을 보여준다 (중복 실행 없음)
        if (e.runId) setSelectedId(e.runId);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setRequesting(false);
    }
  }

  if (runs === null && !error) return <div className="mb-14 h-20 animate-pulse rounded-xl bg-soft" />;
  if ((runs?.length ?? 0) === 0 && (mode === null || enabled === false)) return null;

  const selected = runs?.find((r) => r.id === selectedId) ?? runs?.[0] ?? null;
  const busy = requesting || active !== null;
  const canRun = mode !== null && enabled === true;
  const triggered = mode === "REVIEW_DUE";

  return (
    <Card id="deep-review" className="mb-14 scroll-mt-32">
      <CardHeader
        className="flex-col sm:flex-row"
        title={
          <span className="inline-flex items-center gap-1.5">
            <Network className="size-4" />
            Deep Review
          </span>
        }
        description="Multi-agent · Evidence Researcher → Decision Analyst → Review Critic (Paperclip). 단계마다 schema 검증 · 교차 검증 후 사람에게 전달합니다. 결정을 바꾸지 않습니다."
        action={
          canRun && (
            <Button size="sm" variant={triggered && !selected ? "primary" : "secondary"} disabled={busy} onClick={run} className="sm:shrink-0">
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Network className="size-3.5" />}
              {active ? "Running…" : selected ? "Run again" : "Run Deep Review"}
            </Button>
          )
        }
      />
      <div className="space-y-5 p-5 sm:p-6">
        {error && <p className="rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}</p>}
        {canRun && !active && (
          <p className={cn("text-sm", triggered ? "border-l-2 border-ink pl-3 font-medium text-ink" : "text-muted")}>
            {MODE_TEXT[mode]}
            {!triggered && <span className="block text-xs text-faint">수동 실행은 review 상태를 REVIEW_DUE로 바꾸지 않고 알림도 보내지 않습니다.</span>}
          </p>
        )}
        {!selected && (
          <p className="text-sm text-muted">
            아직 Deep Review가 없습니다. Run Deep Review를 누르면 세 역할이 순서대로 검토합니다 (보통 1분 이내).
          </p>
        )}
        {selected && <RunView run={selected} />}
        {runs && runs.length > 1 && (
          <div className="border-t border-line pt-3">
            <div className="eyebrow text-[10px]">History</div>
            <ul className="mt-2 flex flex-wrap gap-2">
              {runs.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(r.id)}
                    className={cn(
                      "rounded-md border px-2 py-1 text-xs",
                      r.id === selected?.id ? "border-ink bg-ink text-white" : "border-line text-ink-2 hover:border-ink",
                    )}
                  >
                    #{r.id} · {STATUS_TEXT[r.status]} · {r.review_reason === "MANUAL" ? "Manual" : "Triggered"} ·{" "}
                    {time(r.completed_at ?? r.created_at)}
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

function StageRow({ stage }: { stage: DeepReviewStage }) {
  const icon = {
    complete: <Check className="size-3.5 text-ink" />,
    running: <Loader2 className="size-3.5 animate-spin text-ink" />,
    failed: <AlertTriangle className="size-3.5 text-danger" />,
    waiting: <Circle className="size-3 text-faint" />,
  }[stage.state];
  return (
    <li className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="inline-flex items-center gap-2 text-ink">
        <span className="flex size-4 items-center justify-center">{icon}</span>
        {stage.label}
        {stage.iteration > 0 && <span className="text-xs text-muted">revision {stage.iteration}</span>}
      </span>
      <span className={cn("text-xs uppercase tracking-wide", stage.state === "failed" ? "text-danger" : "text-muted")}>{stage.state}</span>
    </li>
  );
}

function RunView({ run }: { run: DeepReviewRun }) {
  const [open, setOpen] = useState(false);
  const research = latest<DeepReviewResearch>(run, "researcher");
  const analysis = latest<DeepReviewAnalysis>(run, "analyst");
  const critique = latest<DeepReviewCritique>(run, "critic");
  const done = run.status === "COMPLETED" || run.status === "HUMAN_REVIEW_REQUIRED";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-lg bg-soft px-3 py-2 text-xs text-ink-2">
        <span>run #{run.id}</span>
        <span>{STATUS_TEXT[run.status]}</span>
        <span className="text-muted">{REASON_TEXT[run.review_reason ?? "TRIGGERED"]}</span>
        <span>시작 {time(run.created_at)}</span>
        {run.completed_at && <span>종료 {time(run.completed_at)}</span>}
        {run.paperclip.parent_identifier && <span>Paperclip {run.paperclip.parent_identifier}</span>}
      </div>

      <ul className="divide-y divide-line border-y border-line">
        {run.stages.map((s) => (
          <StageRow key={s.key} stage={s} />
        ))}
      </ul>

      {run.status === "FAILED" && (
        <div className="rounded-lg border border-danger/25 bg-danger-soft p-3 text-sm text-danger">
          <div className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle className="size-4" />
            Deep Review failed — 다시 실행할 수 있습니다.
          </div>
          {run.error && <p className="mt-1 break-words font-mono text-xs">{run.error}</p>}
          <p className="mt-1 text-xs">Decision, review 상태, Review Memo는 바뀌지 않았습니다.</p>
        </div>
      )}

      {done && critique && (
        <div className="grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
          <Stat label="Verdict" value={run.final_verdict === "PASS" ? "PASS" : "HUMAN REVIEW REQUIRED"} strong />
          <Stat label="Unsupported claims" value={String(critique.unsupported_claims.length + critique.contradictions.length)} />
          <Stat label="Missing checks" value={String(critique.missing_checks.length)} />
          <Stat label="Revisions" value={`${run.revision_count} / 1`} />
        </div>
      )}

      {(research || analysis || critique) && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-ink underline-offset-4 hover:underline"
        >
          {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
          {open ? "Hide agent outputs" : "Show agent outputs"}
        </button>
      )}

      {open && (
        <div className="space-y-8">
          {research && <ResearchView research={research} meta={run.artifacts.find((a) => a.role === "researcher")?.meta ?? null} />}
          {analysis && <AnalysisView analysis={analysis} research={research} />}
          {critique && <CritiqueView critique={critique} />}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="border-l-2 border-ink bg-soft px-3 py-2 text-xs font-medium text-ink">
          Final decision is human-controlled. Deep Review는 Decision, trigger, review 상태를 변경하지 않습니다.
        </p>
        {run.paperclip_url && (
          <a
            href={run.paperclip_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted underline-offset-4 hover:text-ink hover:underline"
          >
            View orchestration details in Paperclip
            <ExternalLink className="size-3" />
          </a>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="bg-surface px-4 py-3">
      <div className="eyebrow text-[10px]">{label}</div>
      <div className={cn("mt-1 text-sm tabular-nums text-ink", strong && "font-semibold")}>{value}</div>
    </div>
  );
}

/** Authoritative (backend 기록 그대로) vs AI (agent 작성) 구분 표시 */
function Block({ title, kind, children }: { title: string; kind: "fact" | "ai"; children: React.ReactNode }) {
  return (
    <section className={cn("border-l-2 pl-4", kind === "fact" ? "border-ink" : "border-dashed border-line-strong")}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
            kind === "fact" ? "bg-ink text-white" : "border border-line-strong text-muted",
          )}
        >
          {kind === "fact" ? "Authoritative record" : "AI inference"}
        </span>
      </div>
      <div className="mt-3 space-y-2 text-sm text-ink-2">{children}</div>
    </section>
  );
}

function Refs({ refs }: { refs: string[] }) {
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {refs.map((r) => (
        <span key={r} className="rounded bg-soft px-1 py-px font-mono text-[10px] text-ink-2">
          {r}
        </span>
      ))}
    </span>
  );
}

function List({ items, empty = "없음" }: { items: React.ReactNode[]; empty?: string }) {
  if (items.length === 0) return <p className="text-muted">{empty}</p>;
  return (
    <ul className="list-disc space-y-1.5 pl-4 marker:text-faint">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

function factText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function ResearchView({ research, meta }: { research: DeepReviewResearch; meta: DeepReviewArtifact["meta"] }) {
  const f = research.authoritative_facts;
  const assumptions = (f.assumptions as string[] | undefined) ?? [];
  const observed = research.observed_state.observed as { metric?: string; value?: number; unit?: string; observed_at?: string } | undefined;
  const current = research.observed_state.current_value as { available?: boolean; reason?: string | null } | undefined;
  const missing = current && current.available === false ? (CURRENT_VALUE_TEXT[current.reason ?? ""] ?? "현재 관측값 없음") : null;
  return (
    <div className="space-y-6">
      <Block title="Authoritative Evidence" kind="fact">
        <dl className="grid gap-3 sm:grid-cols-2">
          <Fact label="Decision" value={factText(f.decision)} wide />
          <Fact label="Rationale" value={factText(f.rationale)} wide />
          <Fact label="Assumptions" value={assumptions.length ? assumptions.map((a, i) => `[${i}] ${a}`).join("\n") : "명시적으로 기록된 전제 없음"} wide />
          <Fact label="Trigger" value={factText(f.trigger)} />
          <Fact
            label="Observed"
            value={missing ?? (observed ? `${observed.metric} = ${observed.value} ${observed.unit ?? ""} · ${observed.observed_at ?? ""}` : "-")}
          />
          <Fact label="Raw condition" value={factText(f.raw_condition)} wide />
          {typeof f.review_reason_note === "string" && <Fact label="Review reason" value={f.review_reason_note} wide />}
          <Fact label="Detection" value={factText(f.detection)} wide />
        </dl>
        <div className="pt-2">
          <div className="eyebrow text-[10px]">Evidence (원문 그대로)</div>
          <ul className="mt-2 space-y-2">
            {research.evidence.map((e) => (
              <li key={e.ref} className="text-sm">
                <span className="mr-1.5 font-mono text-[11px] text-muted">{e.ref}</span>
                <span className="text-ink">{e.text}</span>
                <span className="block pl-7 text-xs text-muted">AI 선택 이유 · {e.relevance}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-faint">{research.evidence[0]?.source_title ?? ""}</p>
        </div>
      </Block>
      <Block title="Related past decisions (gbrain)" kind="ai">
        <List
          items={research.related_decisions.map((d) => (
            <>
              <Link href={`/decisions/${d.decision_id}`} className="font-mono text-xs text-ink underline underline-offset-4">
                {d.decision_id}
              </Link>{" "}
              <span className="text-xs text-muted">({d.relation})</span> {d.summary}
            </>
          ))}
        />
        {meta?.tool_audit && (
          <p className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px] text-muted">
            <ShieldCheck className="size-3.5 text-ink" />
            Researcher tools (read-only):
            {meta.tool_audit.tools_used.map((t) => (
              <span key={t} className="rounded bg-soft px-1.5 py-0.5 font-mono text-ink-2">
                {t.replace(/^mcp__/, "")}
              </span>
            ))}
          </p>
        )}
      </Block>
    </div>
  );
}

function Fact({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={cn(wide && "sm:col-span-2")}>
      <dt className="eyebrow text-[10px]">{label}</dt>
      <dd className="mt-1 whitespace-pre-line text-sm text-ink">{value}</dd>
    </div>
  );
}

function AnalysisView({ analysis, research }: { analysis: DeepReviewAnalysis; research: DeepReviewResearch | null }) {
  const unknowns = [...(research?.unknowns ?? []), ...analysis.unknowns];
  return (
    <div className="space-y-6">
      <Block title="What Changed" kind="ai">
        <List items={analysis.known_changes.map((c) => <>{c.statement}<Refs refs={c.evidence_refs} /></>)} />
      </Block>
      <Block title="Implications" kind="ai">
        <List
          items={analysis.implications.map((c) => (
            <>
              <span className="mr-1 text-[10px] uppercase tracking-wide text-muted">{c.confidence}</span>
              {c.statement}
              <Refs refs={c.evidence_refs} />
            </>
          ))}
        />
      </Block>
      <Block title="Unknowns" kind="ai">
        <List items={[...new Set(unknowns)]} />
      </Block>
      <Block title="Recommended Checks" kind="ai">
        <List items={analysis.recommended_checks.map((c) => <><span className="text-ink">{c.check}</span> — {c.reason}</>)} />
      </Block>
    </div>
  );
}

function CritiqueView({ critique }: { critique: DeepReviewCritique }) {
  return (
    <Block title={`Critic Verification · ${critique.verdict}`} kind="ai">
      <div className="eyebrow text-[10px]">Unsupported claims</div>
      <List items={critique.unsupported_claims.map((c) => <>{c.claim} — <span className="text-muted">{c.reason}</span></>)} />
      <div className="eyebrow pt-2 text-[10px]">Contradictions</div>
      <List items={critique.contradictions.map((c) => <>{c.claim} — <span className="text-muted">{c.reason}</span><Refs refs={[c.evidence_ref]} /></>)} />
      <div className="eyebrow pt-2 text-[10px]">Missing evidence / checks</div>
      <List items={critique.missing_checks} />
      {critique.revision_instructions.length > 0 && (
        <>
          <div className="eyebrow pt-2 text-[10px]">Revision instructions</div>
          <List items={critique.revision_instructions} />
        </>
      )}
    </Block>
  );
}
