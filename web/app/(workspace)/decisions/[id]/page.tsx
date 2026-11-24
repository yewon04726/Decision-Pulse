import { Suspense } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Bot, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { Field, Skeleton } from "@/components/ui/card";
import { ButtonLink } from "@/components/ui/button";
import { HumanAuthoredTag, MappingBadge, ReviewStateBadge, SourceTypeBadge, TopicTag } from "@/components/decision/status-badge";
import { TriggerGauge, V2TriggerView } from "@/components/decision/trigger-gauge";
import { METRIC_TOOL_COMMAND, ServiceAlert } from "@/components/layout/service-alert";
import { DeepReviewPanel, type DeepReviewMode } from "@/components/review/deep-review-panel";
import { ReviewMemoPanel } from "@/components/review/review-memo-panel";
import { getDecision, getSources } from "@/lib/api";
import { getCurrentProject } from "@/lib/project";
import { formatMetricValue, formatNumber, formatReviewTrigger, formatTrigger, metricKey, metricLabel } from "@/lib/metrics";
import type { ReviewTrigger, TriggerStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function DecisionDetailPage({ params }: PageProps<"/decisions/[id]">) {
  return (
    <>
      <Link href="/decisions" className="mb-8 inline-flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-ink">
        <span aria-hidden>←</span> Decisions
      </Link>
      <Suspense fallback={<Skeleton className="h-96" />}>
        {params.then(({ id }) => (
          <DecisionDetail id={decodeURIComponent(id)} />
        ))}
      </Suspense>
    </>
  );
}

/** 상세 화면의 구획: 위 hairline + 작은 제목 */
function Block({ title, description, children, className }: { title: string; description?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("border-t border-line pt-5", className)}>
      <h2 className="eyebrow">{title}</h2>
      {description && <p className="mt-1 text-xs text-faint">{description}</p>}
      <div className="mt-5">{children}</div>
    </section>
  );
}

async function DecisionDetail({ id }: { id: string }) {
  const [decision, { project: current, projects }] = await Promise.all([getDecision(id), getCurrentProject()]);
  if (!decision) notFound();
  const project = projects.find((p) => p.id === decision.project_id);
  // Evidence는 결정이 속한 프로젝트의 Source만 연결한다
  const sources = await getSources(decision.project_id);

  const { display_status: status, evaluation } = decision;
  const trigger = decision.review_trigger;
  const ts = decision.trigger_status ?? null;
  // Deep Review는 유효한(active) 모든 Decision에서 사람이 실행할 수 있다. 실행해도 review 상태는 바뀌지 않는다
  const deepReviewMode: DeepReviewMode | null =
    decision.status !== "active"
      ? null
      : status === "REVIEW_DUE"
        ? "REVIEW_DUE"
        : ts?.mapping_status === "NEEDS_MAPPING"
          ? "NEEDS_MAPPING"
          : !ts || ts.mapping_status === "MANUAL"
            ? "MANUAL"
            : status === "UNKNOWN"
              ? "UNKNOWN"
              : "ACTIVE";
  const sourceById = new Map(sources.map((s) => [s.id, s]));
  const linkedSources = [...new Set(decision.evidence.map((e) => e.source_id))]
    .map((sid) => sourceById.get(sid))
    .filter((s) => s !== undefined);
  const askQuery = `현재 ${decision.topic} 결정을 다시 검토해야 해?`;

  return (
    <>
      <PageHeader
        eyebrow={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1 normal-case tracking-normal">
            <span className="eyebrow">{project?.name ?? `project ${decision.project_id}`}</span>
            <TopicTag topic={decision.topic} />
            <span className="font-mono text-[11px] font-normal text-faint">{decision.id}</span>
          </span>
        }
        title={decision.title}
        description={
          <span className="flex flex-col gap-3">
            <ReviewStateBadge decision={decision} />
            <span className="text-[17px] text-ink-2">{decision.decision}</span>
          </span>
        }
        actions={
          <>
            <ButtonLink href={`/agent?q=${encodeURIComponent(askQuery)}`} variant="secondary">
              <Bot className="size-4" />
              Agent에게 묻기
            </ButtonLink>
            {status === "REVIEW_DUE" && (
              <ButtonLink href="#review-memo">
                <Sparkles className="size-4" />
                Review Memo
              </ButtonLink>
            )}
          </>
        }
      />

      {decision.project_id !== current.id && (
        <p className="mb-8 border-l-2 border-ink bg-surface px-4 py-3 text-sm text-ink-2">
          이 결정은 현재 선택된 프로젝트({current.name})가 아니라 <b className="text-ink">{project?.name}</b> 프로젝트에 속합니다.
        </p>
      )}

      {evaluation?.error && evaluation.reason === "unavailable" && (
        <ServiceAlert
          title="check_metric 서버에 연결할 수 없어 재검토 조건을 판정하지 못했습니다"
          detail={evaluation.error}
          command={METRIC_TOOL_COMMAND}
        />
      )}

      {decision.superseded_by && (
        <p className="mb-8 border-l-2 border-line-strong bg-surface px-4 py-3 text-sm text-ink-2">
          이 결정은{" "}
          <Link href={`/decisions/${decision.superseded_by}`} className="font-mono font-medium text-ink underline underline-offset-4">
            {decision.superseded_by}
          </Link>
          로 대체되었습니다.
        </p>
      )}

      <ReviewMemoPanel decisionId={decision.id} reviewDue={status === "REVIEW_DUE"} />
      <DeepReviewPanel decisionId={decision.id} projectId={decision.project_id} mode={deepReviewMode} />

      <div className="grid gap-14 lg:grid-cols-[1.5fr_1fr] lg:gap-12">
        <div className="space-y-14">
          <Block title="Decision">
            <dl className="grid gap-7 sm:grid-cols-2">
              <Field label="Decision" className="sm:col-span-2">
                <span className="text-lg font-medium leading-relaxed text-ink">{decision.decision}</span>
              </Field>
              <Field label="Rationale" className="sm:col-span-2">
                <span className="leading-relaxed text-ink-2">{decision.rationale}</span>
              </Field>
              <Field label="Assumptions">
                {decision.assumptions.length > 0 ? (
                  <ul className="list-disc space-y-1 pl-4 text-ink-2 marker:text-faint">
                    {decision.assumptions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                ) : (
                  <span className="text-faint">없음</span>
                )}
              </Field>
              <Field label="Verification">
                {decision.human_verified ? (
                  <span className="inline-flex flex-wrap items-center gap-x-2">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="size-1.5 rounded-full bg-ok" aria-hidden />
                      Human verified
                    </span>
                    <span className="text-xs text-faint">(Review Queue에서 승인)</span>
                  </span>
                ) : (
                  <span className="inline-flex flex-wrap items-center gap-x-2 text-ink-2">
                    문서에서 가져옴
                    <span className="text-xs text-faint">(Candidate 승인 절차 없음)</span>
                  </span>
                )}
              </Field>
              {decision.notes.map((n) => (
                <Field key={n.label} label={n.label} className="sm:col-span-2">
                  <span className="text-ink-2">{n.text}</span>
                </Field>
              ))}
              <Field label="Decided">
                {decision.created_at ?? <span className="text-faint">문서에 날짜 없음</span>}
              </Field>
            </dl>
          </Block>

          <Block title="Evidence" description="결정의 근거가 된 원문 인용">
            <ul className="space-y-8">
              {decision.evidence.map((e) => {
                const src = sourceById.get(e.source_id);
                return (
                  <li key={e.source_id + e.quote}>
                    <blockquote className="border-l-2 border-ink pl-5 text-[17px] leading-relaxed text-ink">“{e.quote}”</blockquote>
                    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 pl-5 text-xs text-muted">
                      {src && <SourceTypeBadge type={src.type} />}
                      {src?.date && <span className="tabular-nums">{src.date}</span>}
                      {src?.summary && <span className="text-ink-2">{src.summary}</span>}
                      <Link href={`/sources#${e.source_id}`} className="font-mono text-ink underline-offset-4 hover:underline">
                        {src?.title ?? e.source_id}
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Block>
        </div>

        <div className="space-y-14">
          <Block title="Review trigger" description="재검토 조건 원문과 현재 상태 (자동 판정은 backend 코드)">
            {trigger ? (
              <div className="space-y-6">
                <Field label="원문 조건">
                  <span className="block border-l-2 border-line-strong pl-3 leading-relaxed text-ink-2">
                    {trigger.raw_condition ?? ("description" in trigger ? trigger.description : null) ?? "-"}
                  </span>
                </Field>
                {ts?.mapping_status === "NEEDS_MAPPING" ? (
                  <NeedsMappingPanel trigger={trigger} status={ts} />
                ) : ts?.mapping_status === "MANUAL" ? (
                  <ManualPanel trigger={trigger} status={ts} />
                ) : trigger.type !== "metric" || !metricKey(trigger) ? (
                  <V2TriggerView trigger={trigger} status={ts} />
                ) : (
                  evaluation && (
                    <>
                      <ConnectedHeader status={ts} />
                      <dl className="grid grid-cols-2 gap-4">
                        <Field label="Condition">{formatTrigger(trigger)}</Field>
                        <Field label="Current metric">
                          <span className={cn("text-2xl font-semibold tracking-[-0.03em] tabular-nums", evaluation.met && "text-accent-ink")}>
                            {evaluation.metric ? formatMetricValue(evaluation.metric.value, evaluation.metric.unit) : "-"}
                          </span>
                        </Field>
                      </dl>
                      <TriggerGauge trigger={trigger} evaluation={evaluation} />
                      {evaluation.metric && evaluation.met !== null && (
                        <div
                          className={cn(
                            "border-l-2 px-4 py-3 text-sm",
                            evaluation.met ? "border-accent bg-accent-soft text-ink" : "border-ok bg-surface text-ink",
                          )}
                        >
                          <div className={cn("font-semibold", evaluation.met && "text-accent-ink")}>
                            {evaluation.met ? "재검토 조건 충족" : "재검토 조건 미충족"}
                          </div>
                          <p className="mt-1 font-mono text-xs text-ink-2">
                            {metricLabel(trigger)}: {formatNumber(evaluation.metric.value)} {trigger.operator}{" "}
                            {formatNumber(trigger.threshold)} → {evaluation.met ? "true" : "false"}
                          </p>
                          {evaluation.met && (
                            <p className="mt-2 text-xs text-ink-2">조건 충족은 자동 변경이 아닙니다. 사람의 재검토가 필요합니다.</p>
                          )}
                        </div>
                      )}
                      {trigger.note && <p className="text-xs text-muted">※ {trigger.note}</p>}
                    </>
                  )
                )}
              </div>
            ) : (
              <p className="text-sm text-muted">재검토 조건이 없는 영구 결정입니다.</p>
            )}
          </Block>

          <Block title="Sources">
            <ul className="divide-y divide-line border-y border-line">
              {linkedSources.map((s) => (
                <li key={s.id}>
                  <Link href={`/sources#${s.id}`} className="group flex items-center gap-3 py-3">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-xs text-ink group-hover:underline group-hover:underline-offset-4">
                        {s.title}
                      </span>
                      <span className="block text-[11px] text-faint">{s.date ?? "날짜 없음"}</span>
                    </span>
                    {s.human_authored && <HumanAuthoredTag />}
                    <SourceTypeBadge type={s.type} />
                  </Link>
                </li>
              ))}
            </ul>
          </Block>

          <Block title="Revision history">
            <ol className="relative space-y-5 before:absolute before:bottom-2 before:left-[3px] before:top-2 before:w-px before:bg-line-strong">
              {decision.history.map((h, i) => (
                <li key={i} className="relative flex gap-4">
                  <span className="z-10 mt-1.5 size-[7px] shrink-0 rounded-full bg-ink ring-4 ring-paper" />
                  <div>
                    <div className="text-sm font-medium text-ink">{h.action}</div>
                    <div className="text-xs text-muted">
                      {h.date ?? "날짜 없음"} · {h.actor}
                      {h.note && ` · ${h.note}`}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </Block>
        </div>
      </div>
    </>
  );
}

/** CONNECTED: ● 자동 모니터링 + 데이터 소스. 값을 읽지 못하면(UNKNOWN) 이유 표시 — '연결 안 됨'과 다르다 */
function ConnectedHeader({ status }: { status: TriggerStatus | null }) {
  if (!status) return null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <MappingBadge status="CONNECTED" />
        {status.data_source && (
          <span className="text-xs text-muted">
            데이터 소스 <span className="font-mono text-ink-2">{status.data_source}</span>
          </span>
        )}
      </div>
      {status.evaluation_status === "UNKNOWN" && (
        <p className="border-l-2 border-danger bg-danger-soft px-3 py-2 text-xs text-danger">{status.reason}</p>
      )}
    </div>
  );
}

/** NEEDS_MAPPING: ○ 데이터 연결 필요 — 측정 가능한 조건이지만 연결된 데이터 소스가 없다 (UNKNOWN이 아님) */
function NeedsMappingPanel({ trigger, status }: { trigger: ReviewTrigger; status: TriggerStatus }) {
  return (
    <div className="space-y-4 rounded-xl border border-line-strong p-4">
      <MappingBadge status="NEEDS_MAPPING" />
      <p className="font-mono text-base text-ink">{formatReviewTrigger(trigger)}</p>
      <p className="text-sm text-ink-2">{status.reason}</p>
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="eyebrow text-[10px]">Canonical metric</dt>
          <dd className="mt-1 font-mono text-ink-2">{status.canonical_metric ?? "미등록 지표"}</dd>
        </div>
        <div>
          <dt className="eyebrow text-[10px]">자동 평가</dt>
          <dd className="mt-1 text-ink-2">보류 (REVIEW_DUE 알림 없음)</dd>
        </div>
      </dl>
      {/* Mapping UI는 아직 없다: 버튼은 비활성 (가짜 connector를 만들지 않는다) */}
      <button
        type="button"
        disabled
        title="데이터 소스 연결 기능은 아직 준비 중입니다"
        className="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium text-faint"
      >
        지표 연결 (준비 중)
      </button>
    </div>
  );
}

/** MANUAL: ○ 수동 확인 필요 */
function ManualPanel({ trigger, status }: { trigger: ReviewTrigger; status: TriggerStatus }) {
  const summary = trigger.type === "manual" || trigger.type === "event" ? null : formatReviewTrigger(trigger);
  return (
    <div className="space-y-3 rounded-xl bg-soft p-4">
      <MappingBadge status="MANUAL" />
      {summary && <p className="font-mono text-sm text-ink">{summary}</p>}
      <p className="text-sm text-ink-2">{status.reason}</p>
    </div>
  );
}
