import { Suspense } from "react";
import Link from "next/link";
import { Bot, Plus } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { MetricToolAlert } from "@/components/layout/service-alert";
import { ArrowLink, ButtonLink } from "@/components/ui/button";
import { EmptyState, Section, Skeleton } from "@/components/ui/card";
import { AttentionRow } from "@/components/decision/attention-row";
import { StatusBadge, TopicTag } from "@/components/decision/status-badge";
import { TriggerGauge } from "@/components/decision/trigger-gauge";
import { getCandidates, getDecisions, getMetrics, getReviewMemos, getSources } from "@/lib/api";
import { getCurrentProject } from "@/lib/project";
import { formatMetricValue, formatReviewTrigger, metricKey, METRIC_LABELS } from "@/lib/metrics";
import { cn } from "@/lib/utils";
import type { DecisionView, ReviewMemo, ReviewTrigger } from "@/lib/types";

// 프로젝트 Workspace 첫 화면. 우선순위: 1 Review Due → 2 Candidate → 3 Review Memo → 4 Decision 상태 → 5 Knowledge/Integration
export default function OverviewPage() {
  return (
    <Suspense fallback={<OverviewSkeleton />}>
      <OverviewContent />
    </Suspense>
  );
}

function OverviewSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading overview">
      <div className="eyebrow">Overview</div>
      <Skeleton className="mt-4 h-12 w-80 max-w-full" />
      <Skeleton className="mt-14 h-72" />
    </div>
  );
}

const time = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 16) : "-");

async function OverviewContent() {
  const { project } = await getCurrentProject();
  const [decisions, metricsRes, sources, candidates] = await Promise.all([
    getDecisions(project.id),
    getMetrics(project.id),
    getSources(project.id),
    getCandidates(project.id, "pending"),
  ]);

  const live = decisions.filter((d) => d.display_status !== "SUPERSEDED");
  const reviewDue = decisions.filter((d) => d.display_status === "REVIEW_DUE");
  // 자동 판정 = 등록된 지표의 단일 metric 조건. 그 외 조건(manual, event, compound, 매핑 안 된 metric 등)은 사람이 확인
  const autoChecked = (t: ReviewTrigger | null) => t?.type === "metric" && metricKey(t) !== null;
  const monitored = live.filter((d) => autoChecked(d.review_trigger) && d.display_status !== "REVIEW_DUE");
  const manual = live.filter((d) => d.review_trigger && !autoChecked(d.review_trigger));

  // 최근 Review Memo: 재검토 대상 결정별 최신 메모 (읽기 전용 조회)
  const memoLists = await Promise.all(reviewDue.map((d) => getReviewMemos(d.id).catch(() => [] as ReviewMemo[])));
  const memos = reviewDue
    .map((d, i) => ({ decision: d, memo: memoLists[i][0] }))
    .filter((m): m is { decision: DecisionView; memo: ReviewMemo } => m.memo !== undefined)
    .sort((a, b) => (b.memo.created_at > a.memo.created_at ? 1 : -1));

  const count = (s: DecisionView["display_status"]) => decisions.filter((d) => d.display_status === s).length;
  const stats = [
    { label: "Active", value: count("ACTIVE"), href: "/decisions?status=ACTIVE" },
    { label: "Review due", value: reviewDue.length, href: "/decisions?status=REVIEW_DUE", accent: reviewDue.length > 0 },
    { label: "Pending review", value: count("PENDING_REVIEW"), href: "/decisions?status=PENDING_REVIEW" },
    { label: "Superseded", value: count("SUPERSEDED"), href: "/decisions?status=SUPERSEDED" },
  ];

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title={project.name}
        description={project.description || undefined}
        actions={
          <>
            <ButtonLink href="/sources?add=1" variant="secondary">
              <Plus className="size-4" />
              Add source
            </ButtonLink>
            <ButtonLink href="/agent">
              <Bot className="size-4" />
              Ask Agent
            </ButtonLink>
          </>
        }
      />
      {metricsRes.connected && metricsRes.metric_tool_url && (
        <MetricToolAlert errors={metricsRes.errors} url={metricsRes.metric_tool_url} />
      )}

      {/* 1. 지금 사람이 확인해야 하는 결정 */}
      <Section
        eyebrow="01 — Review due"
        title="Decisions requiring attention"
        count={reviewDue.length}
        description="조건 충족은 자동 변경을 의미하지 않습니다. 사람의 재검토가 필요하다는 신호입니다."
      >
        {reviewDue.length > 0 ? (
          <div>
            {reviewDue.map((d) => (
              <AttentionRow key={d.id} decision={d} />
            ))}
          </div>
        ) : decisions.length === 0 ? (
          <EmptyState>
            아직 이 프로젝트의 Decision Memory가 비어 있습니다.{" "}
            <Link href="/sources?add=1" className="font-medium text-ink underline underline-offset-4">
              Add source
            </Link>
            로 회의록을 추가하고, Candidates에서 결정을 승인하세요.
          </EmptyState>
        ) : (
          Object.keys(metricsRes.errors).length === 0 && (
            <p className="flex items-center gap-2.5 border-b border-line pb-8 text-[15px] text-ink-2">
              <span className="size-1.5 rounded-full bg-ok" aria-hidden />
              재검토 조건을 충족한 결정이 없습니다.
            </p>
          )
        )}
      </Section>

      <div className="mt-16 grid gap-16 lg:grid-cols-2 lg:gap-12">
        {/* 2. 최근 Candidate */}
        <Section
          eyebrow="02 — Awaiting approval"
          title="Candidates"
          count={candidates.length}
          action={<ArrowLink href="/review">Review queue</ArrowLink>}
        >
          {candidates.length === 0 ? (
            <p className="text-sm text-muted">대기 중인 후보가 없습니다.</p>
          ) : (
            <ul className="divide-y divide-line border-y border-line">
              {candidates.slice(0, 4).map((c) => (
                <li key={c.id}>
                  <Link href="/review" className="group flex items-start gap-4 py-4 transition-colors hover:bg-surface/70">
                    <span className="min-w-0 flex-1">
                      <span className="line-clamp-2 text-[15px] font-medium text-ink">{c.decision}</span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                        <TopicTag topic={c.topic} />
                        <span className="truncate font-mono">{c.source_title}</span>
                      </span>
                    </span>
                    <span aria-hidden className="pt-0.5 text-faint transition-transform duration-200 group-hover:translate-x-0.5">
                      →
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {candidates.length > 4 && <p className="mt-3 text-xs text-muted">+{candidates.length - 4} more</p>}
        </Section>

        {/* 3. 최근 Review Memo */}
        <Section eyebrow="03 — Advisory" title="Review memos" count={memos.length}>
          {memos.length === 0 ? (
            <p className="text-sm text-muted">
              {reviewDue.length > 0
                ? "아직 생성된 Review Memo가 없습니다. 재검토 대상 결정에서 생성할 수 있습니다."
                : "재검토 대상 결정이 없습니다."}
            </p>
          ) : (
            <ul className="divide-y divide-line border-y border-line">
              {memos.map(({ decision, memo }) => (
                <li key={memo.id}>
                  <Link
                    href={`/decisions/${decision.id}#review-memo`}
                    className="group flex items-start gap-4 py-4 transition-colors hover:bg-surface/70"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-medium text-ink">{decision.title}</span>
                      <span className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
                        <span className={cn(memo.status === "failed" && "text-danger")}>
                          memo #{memo.id} · {memo.status}
                        </span>
                        <span className="tabular-nums">{time(memo.generated_at ?? memo.created_at)}</span>
                      </span>
                    </span>
                    <span aria-hidden className="pt-0.5 text-faint transition-transform duration-200 group-hover:translate-x-0.5">
                      →
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      {/* 4. 현재 Decision 상태 */}
      <Section
        eyebrow="04 — Decision memory"
        title="Decision status"
        count={decisions.length}
        className="mt-16"
        action={<ArrowLink href="/decisions">All decisions</ArrowLink>}
      >
        <dl className="grid grid-cols-2 border-y border-line sm:grid-cols-4">
          {stats.map((s, i) => (
            <Link
              key={s.label}
              href={s.href}
              className={cn(
                "group px-1 py-5 transition-colors hover:bg-surface sm:px-5",
                i % 2 === 1 && "max-sm:border-l max-sm:border-line max-sm:pl-5",
                i > 0 && "sm:border-l sm:border-line",
                i > 1 && "max-sm:border-t max-sm:border-line",
              )}
            >
              <dt className="text-xs text-muted">{s.label}</dt>
              <dd className={cn("mt-2 text-[40px] font-semibold leading-none tracking-[-0.04em] tabular-nums", s.accent && "text-accent-ink")}>
                {s.value}
              </dd>
            </Link>
          ))}
        </dl>

        <div className="mt-10 grid gap-12 lg:grid-cols-[1.4fr_1fr]">
          <div>
            <h3 className="eyebrow">Monitored triggers · check_metric</h3>
            {monitored.length === 0 ? (
              <p className="mt-4 text-sm text-muted">자동 판정 대상인 다른 결정이 없습니다.</p>
            ) : (
              <ul className="mt-3 divide-y divide-line">
                {monitored.map((d) => (
                  <li key={d.id}>
                    <Link href={`/decisions/${d.id}`} className="group grid gap-3 py-4 sm:grid-cols-[minmax(0,13rem)_1fr] sm:items-center sm:gap-6">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-ink group-hover:underline group-hover:underline-offset-4">
                          {d.title}
                        </span>
                        <span className="mt-1 flex items-center gap-3">
                          <StatusBadge status={d.display_status} />
                          <TopicTag topic={d.topic} />
                        </span>
                      </span>
                      {d.review_trigger?.type === "metric" && d.evaluation && (
                        <TriggerGauge trigger={d.review_trigger} evaluation={d.evaluation} size="sm" />
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h3 className="eyebrow">Checked by people · {manual.length}</h3>
            <p className="mt-1 text-xs text-faint">check_metric에 해당 지표가 없어 자동 판정하지 않습니다</p>
            <ul className="mt-3 divide-y divide-line">
              {manual.map((d) => (
                <li key={d.id}>
                  <Link href={`/decisions/${d.id}`} className="group block py-3.5">
                    <span className="block truncate text-sm font-medium text-ink group-hover:underline group-hover:underline-offset-4">
                      {d.title}
                    </span>
                    <span className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">
                      {d.review_trigger?.type === "manual" ? d.review_trigger.description : formatReviewTrigger(d.review_trigger)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Section>

      {/* 5. Knowledge / Integration */}
      <Section eyebrow="05 — Integrations" title="Knowledge & metrics" className="mt-16">
        <div className="grid gap-12 lg:grid-cols-[1fr_1.4fr]">
          <dl className="divide-y divide-line border-y border-line text-sm">
            {[
              { label: "Metric source", on: project.metrics_connected, value: project.metric_source },
              { label: "Knowledge / Agent", on: project.agent_connected, value: project.agent_source },
            ].map((c) => (
              <div key={c.label} className="flex items-center justify-between gap-4 py-3.5">
                <dt className="text-ink-2">{c.label}</dt>
                <dd className="flex items-center gap-2 text-right">
                  <span className={cn("size-1.5 rounded-full", c.on ? "bg-ok" : "bg-line-strong")} aria-hidden />
                  <span className={c.on ? "text-ink" : "text-muted"}>{c.on ? "Connected" : "Not connected"}</span>
                  {c.on && c.value && <span className="hidden font-mono text-[11px] text-faint sm:inline">{c.value}</span>}
                </dd>
              </div>
            ))}
            <div className="flex items-center justify-between gap-4 py-3.5">
              <dt className="text-ink-2">Sources</dt>
              <dd>
                <ArrowLink href="/sources" className="tabular-nums">
                  {sources.length}
                </ArrowLink>
              </dd>
            </div>
          </dl>

          <div>
            <h3 className="eyebrow">Current metrics</h3>
            <p className="mt-1 text-xs text-faint">
              {metricsRes.connected ? `check_metric 실시간 조회 · ${metricsRes.metric_tool_url ?? ""}` : "Metric not connected"}
            </p>
            {metricsRes.connected ? (
              <ul className="mt-3 divide-y divide-line border-y border-line">
                {Object.keys(metricsRes.errors).map((name) => (
                  <li key={name} className="flex items-center justify-between py-3.5">
                    <span>
                      <span className="block text-sm text-ink">{METRIC_LABELS[name as keyof typeof METRIC_LABELS] ?? name}</span>
                      <span className="font-mono text-[11px] text-faint">{name}</span>
                    </span>
                    <span className="text-sm font-semibold text-danger">조회 실패</span>
                  </li>
                ))}
                {metricsRes.metrics.map((m) => (
                  <li key={m.metric_name} className="flex items-center justify-between py-3.5">
                    <span>
                      <span className="block text-sm text-ink">{METRIC_LABELS[m.metric_name]}</span>
                      <span className="font-mono text-[11px] text-faint">{m.metric_name}</span>
                    </span>
                    <span className="text-xl font-semibold tracking-[-0.02em] tabular-nums">{formatMetricValue(m.value, m.unit)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm leading-relaxed text-muted">
                이 프로젝트에는 metric source가 연결되지 않았습니다. 현재 check_metric은 Decision Debt MVP 프로젝트의 지표만 제공하므로, 다른
                프로젝트 값으로 표시하지 않습니다.
              </p>
            )}
          </div>
        </div>
      </Section>
    </>
  );
}
