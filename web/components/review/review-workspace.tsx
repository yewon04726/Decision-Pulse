"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, Check, Loader2, Pencil, Sparkles, Trash2, X } from "lucide-react";
import { Card, CardHeader, Field } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { TopicTag } from "@/components/decision/status-badge";
import { ManualTriggerView, V2TriggerView } from "@/components/decision/trigger-gauge";
import {
  approveCandidate,
  deleteIgnoredCandidates,
  ignoreCandidate,
  updateCandidate,
} from "@/lib/client-api";
import { buildCandidatePatch, toDraft, type TriggerKind } from "@/lib/candidate-edit";
import { formatTriggerRaw, METRIC_LABELS, metricKey } from "@/lib/metrics";
import { cn } from "@/lib/utils";
import type { Candidate, KnowledgeSyncStatus, MetricName, Operator, Project } from "@/lib/types";

// Review Queue 전용: 후보 검토(Edit / Ignore / Approve) + 처리 기록. 원문 입력은 Sources의 Add source에서 한다.
// 후보/처리 목록은 서버(backend) 데이터만 표시한다. 이 컴포넌트의 상태는 편집 폼과 진행 중 표시뿐이다.
// 모든 변경은 API 성공 후 router.refresh()로 서버에서 다시 읽어 반영한다.

const inputCls =
  "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors placeholder:text-faint focus:border-ink";

export function ReviewWorkspace({
  project,
  pending,
  processed,
}: {
  project: Project;
  pending: Candidate[];
  processed: Candidate[];
}) {
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  // 마지막 승인 결과 (Decision 저장 + Knowledge 동기화). 동기화 결과는 DB에 저장하지 않으므로 승인 직후에만 표시된다.
  const [approval, setApproval] = useState<{ decisionId: string; title: string; sync: KnowledgeSyncStatus } | null>(null);

  const refresh = () => startRefresh(() => router.refresh());

  return (
    <div className="space-y-16">
      {/* 1차: 검토 대기 후보 (Review Queue의 핵심) */}
      <section className="space-y-4" aria-labelledby="pending-heading">
        <h2 id="pending-heading" className="flex items-baseline gap-2.5 border-t border-ink pt-5 text-xl font-semibold tracking-tight text-ink">
          Pending review <span className="text-base font-medium tabular-nums text-faint">{pending.length}</span>
          {refreshing && <Loader2 className="size-3.5 animate-spin self-center text-faint" />}
        </h2>
        {approval && <ApprovalResult {...approval} onClose={() => setApproval(null)} />}
        {pending.map((c) => (
          <CandidateCard
            key={c.id}
            candidate={c}
            project={project}
            onChanged={refresh}
            onApproved={(r) => setApproval({ decisionId: r.decision.id, title: r.decision.title, sync: r.knowledge_sync })}
          />
        ))}
        {pending.length === 0 && (
          <div className="rounded-xl border border-dashed border-line-strong p-8 text-center text-sm text-muted">
            대기 중인 후보가 없습니다. 원문을 추가하면 AI가 결정 후보를 추출합니다 —{" "}
            <Link href="/sources?add=1" className="font-medium text-ink underline underline-offset-4">
              Add source
            </Link>
          </div>
        )}
      </section>

      {/* 2차: 처리 기록 */}
      {processed.length > 0 && <ProcessedList projectId={project.id} processed={processed} onChanged={refresh} />}
    </div>
  );
}

const SYNC_LABEL: Record<KnowledgeSyncStatus["status"], string> = {
  synced: "Knowledge 동기화 완료",
  already_exists: "Knowledge 동기화 완료 (기존 문서)",
  failed: "Knowledge 동기화 실패",
  not_connected: "Knowledge 미연결 프로젝트",
};

/** 승인 직후 결과: Decision 저장은 항상 성공한 상태, Knowledge 동기화는 성공/실패/미연결 */
function ApprovalResult({
  decisionId,
  title,
  sync,
  onClose,
}: {
  decisionId: string;
  title: string;
  sync: KnowledgeSyncStatus;
  onClose: () => void;
}) {
  const tone =
    sync.status === "failed"
      ? "border-accent bg-accent-soft text-ink"
      : sync.status === "not_connected"
        ? "border-line-strong bg-surface text-ink-2"
        : "border-ok bg-surface text-ink";
  return (
    <div className={cn("animate-rise border-l-2 px-4 py-3 text-sm", tone)} role="status">
      <div className="flex items-start gap-2">
        {sync.status === "failed" ? <AlertTriangle className="mt-0.5 size-4 shrink-0" /> : <Check className="mt-0.5 size-4 shrink-0" />}
        <div className="min-w-0 flex-1">
          <p className="font-semibold">
            Decision Memory 저장됨 · {SYNC_LABEL[sync.status]}
          </p>
          <p className="mt-0.5 text-xs">
            <Link href={`/decisions/${decisionId}`} className="font-medium underline">
              {title}
            </Link>{" "}
            <span className="font-mono opacity-70">({decisionId})</span>
          </p>
          {sync.status !== "synced" && sync.status !== "already_exists" && <p className="mt-1 text-xs">{sync.message}</p>}
          {sync.document_name && sync.status !== "failed" && (
            <p className="mt-1 font-mono text-[11px] opacity-70">Dify 문서: {sync.document_name}</p>
          )}
        </div>
        <button onClick={onClose} className="shrink-0 opacity-60 hover:opacity-100" aria-label="닫기">
          <X className="size-4" />
        </button>
      </div>
    </div>
  );
}

/** 처리된 후보 목록. 편집 모드에서 Ignored 기록을 여러 개 골라 한 번에 삭제한다. */
function ProcessedList({
  projectId,
  processed,
  onChanged,
}: {
  projectId: number;
  processed: Candidate[];
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const deletable = processed.filter((c) => c.status === "ignored").map((c) => c.id);
  // 서버에서 목록이 갱신돼 사라진 항목은 선택에서 제외
  const chosen = [...selected].filter((id) => deletable.includes(id));
  const allChosen = deletable.length > 0 && chosen.length === deletable.length;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function exitEdit() {
    setEditing(false);
    setSelected(new Set());
    setError(null);
  }

  async function onDelete() {
    if (chosen.length === 0) return;
    if (!window.confirm(`Ignored 기록 ${chosen.length}개를 삭제할까요? 되돌릴 수 없습니다.`)) return;
    setDeleting(true);
    setError(null);
    try {
      const { skipped } = await deleteIgnoredCandidates(projectId, chosen);
      if (skipped.length > 0) setError(`${skipped.length}개는 삭제하지 못했습니다 (이미 처리 상태가 바뀌었거나 없음).`);
      setSelected(new Set());
      if (skipped.length === 0) setEditing(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Processed"
        description={editing ? "삭제할 Ignored 기록을 선택하세요 (승인 기록은 삭제 불가)" : "처리된 후보 (backend 저장 기준)"}
        action={
          editing ? (
            <div className="flex items-center gap-1.5">
              <Button variant="ghost" size="sm" onClick={exitEdit} disabled={deleting}>
                취소
              </Button>
              <Button variant="danger" size="sm" onClick={onDelete} disabled={deleting || chosen.length === 0}>
                {deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
                삭제 ({chosen.length})
              </Button>
            </div>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => setEditing(true)} disabled={deletable.length === 0}>
              <Pencil className="size-3.5" />
              편집
            </Button>
          )
        }
      />
      {editing && (
        <label className="flex cursor-pointer items-center gap-3 border-b border-line bg-soft px-5 py-2 text-xs text-ink-2">
          <input
            type="checkbox"
            className="size-4 accent-ink"
            checked={allChosen}
            onChange={() => setSelected(allChosen ? new Set() : new Set(deletable))}
          />
          Ignored 전체 선택 ({deletable.length})
        </label>
      )}
      {error && <p className="border-b border-danger/25 bg-danger-soft px-5 py-2 text-xs text-danger">{error}</p>}
      <ul className="divide-y divide-line">
        {processed.map((c) => {
          const canDelete = c.status === "ignored";
          const isChosen = selected.has(c.id);
          const row = (
            <>
              {editing && (
                <input
                  type="checkbox"
                  className="size-4 shrink-0 accent-ink disabled:opacity-30"
                  checked={isChosen}
                  disabled={!canDelete}
                  onChange={() => toggle(c.id)}
                  aria-label={canDelete ? "삭제 선택" : "승인 기록은 삭제할 수 없음"}
                />
              )}
              {c.status === "approved" ? (
                <Check className="size-4 shrink-0 text-ok" />
              ) : (
                <X className="size-4 shrink-0 text-faint" />
              )}
              <span className="min-w-0 flex-1">
                <span className={cn("block truncate", c.status === "ignored" && "text-faint line-through")}>
                  {c.decision}
                </span>
                <span className="block font-mono text-[11px] text-faint">
                  {c.source_title} · {c.resolved_at?.replace("T", " ")}
                </span>
              </span>
              {c.status === "approved" && c.decision_id ? (
                editing ? (
                  <span className="shrink-0 text-xs text-faint">승인 기록</span>
                ) : (
                  <Link href={`/decisions/${c.decision_id}`} className="shrink-0 text-xs font-medium text-ink hover:underline">
                    Decision Memory 저장됨
                  </Link>
                )
              ) : (
                <span className="shrink-0 text-xs text-faint">Ignored</span>
              )}
            </>
          );
          return (
            <li key={c.id}>
              {editing && canDelete ? (
                <label className={cn("flex cursor-pointer items-center gap-3 px-5 py-3 text-sm", isChosen && "bg-danger-soft")}>
                  {row}
                </label>
              ) : (
                <div className={cn("flex items-center gap-3 px-5 py-3 text-sm", editing && "opacity-60")}>{row}</div>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}


function CandidateCard({
  candidate: c,
  project,
  onChanged,
  onApproved,
}: {
  candidate: Candidate;
  project: Project;
  onChanged: () => void;
  onApproved: (result: Awaited<ReturnType<typeof approveCandidate>>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<null | "approve" | "ignore" | "save">(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(() => toDraft(c));

  async function run(kind: "approve" | "ignore" | "save", fn: () => Promise<unknown>) {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      if (kind === "save") setEditing(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function save() {
    const result = buildCandidatePatch(c, draft);
    if ("error" in result) return setError(result.error);
    run("save", () => updateCandidate(c.id, result.patch));
  }

  const t = c.review_trigger;

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line bg-soft px-5 py-2.5 text-xs text-muted">
        <Sparkles className="size-3.5 text-ink" />
        Decision Candidate
        <span className="font-mono">{c.id}</span>
        {c.edited && <span className="rounded bg-surface px-1.5 py-0.5 text-[10px] font-medium text-ink-2">edited</span>}
        <span className="ml-auto font-mono">{c.extractor}</span>
      </div>

      {editing ? (
        <div className="grid gap-3 p-5 sm:grid-cols-2">
          <label className="text-xs text-muted">
            Topic
            <input value={draft.topic} onChange={(e) => setDraft({ ...draft, topic: e.target.value })} className={cn(inputCls, "mt-1")} />
          </label>
          <label className="text-xs text-muted">
            Title
            <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} className={cn(inputCls, "mt-1")} />
          </label>
          <label className="text-xs text-muted sm:col-span-2">
            Decision
            <input value={draft.decision} onChange={(e) => setDraft({ ...draft, decision: e.target.value })} className={cn(inputCls, "mt-1")} />
          </label>
          <label className="text-xs text-muted sm:col-span-2">
            Reason
            <input value={draft.rationale} onChange={(e) => setDraft({ ...draft, rationale: e.target.value })} className={cn(inputCls, "mt-1")} />
          </label>
          {draft.kind === "readonly" && c.review_trigger ? (
            <div className="space-y-2 text-xs text-muted sm:col-span-2">
              Review Trigger
              <div className="rounded-md border border-line bg-soft p-3">
                <V2TriggerView trigger={c.review_trigger} size="sm" />
              </div>
              <p className="text-[11px] text-faint">
                이 조건 형식(event, duration, date, compound, 매핑 안 된 지표)은 아직 화면에서 수정할 수 없습니다. 저장해도 그대로 유지됩니다.
              </p>
            </div>
          ) : (
            <div className="space-y-2 text-xs text-muted sm:col-span-2">
              Review Trigger
              <select
                value={draft.kind}
                onChange={(e) => setDraft({ ...draft, kind: e.target.value as TriggerKind })}
                className={cn(inputCls, "mt-1")}
              >
                <option value="none">없음</option>
                <option value="manual">사람이 확인 (manual)</option>
                {project.metrics_connected && <option value="metric">metric 자동 판정</option>}
              </select>
              {draft.kind === "metric" && (
                <div className="grid grid-cols-[1fr_auto_1fr] gap-2">
                  <select value={draft.metric} onChange={(e) => setDraft({ ...draft, metric: e.target.value as MetricName })} className={inputCls}>
                    {(Object.keys(METRIC_LABELS) as MetricName[]).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <select value={draft.operator} onChange={(e) => setDraft({ ...draft, operator: e.target.value as Operator })} className={inputCls}>
                    {[">", ">=", "<", "<=", "==", "!="].map((op) => (
                      <option key={op}>{op}</option>
                    ))}
                  </select>
                  <input
                    type="number"
                    value={draft.threshold}
                    onChange={(e) => setDraft({ ...draft, threshold: e.target.value })}
                    className={inputCls}
                  />
                </div>
              )}
              {draft.kind !== "none" && (
                <input
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  placeholder="원문 조건 문장"
                  className={inputCls}
                />
              )}
            </div>
          )}
        </div>
      ) : (
        <dl className="grid gap-4 p-5 sm:grid-cols-2">
          <Field label="Topic">
            <TopicTag topic={c.topic} />
          </Field>
          <Field label="Source">
            <span className="font-mono text-xs">{c.source_title}</span>
          </Field>
          <Field label="Decision" className="sm:col-span-2">
            <span className="font-medium text-ink">{c.decision}</span>
          </Field>
          <Field label="Reason">{c.rationale || <span className="text-faint">문서에 이유 없음</span>}</Field>
          <Field label="Review Trigger">
            {t === null ? (
              <span className="text-faint">없음</span>
            ) : t.type === "metric" && metricKey(t) ? (
              <span className="font-mono text-xs">{formatTriggerRaw(t)}</span>
            ) : t.type === "manual" ? (
              <ManualTriggerView trigger={t} size="sm" />
            ) : (
              <V2TriggerView trigger={t} size="sm" />
            )}
          </Field>
          <Field label="Evidence" className="sm:col-span-2">
            {/* 원문 문장들이 줄바꿈으로 구분되어 저장된다 (서로 떨어진 문장을 이어 붙이지 않음) */}
            <span className="block space-y-1 border-l-2 border-ink py-1 pl-4 text-ink">
              {c.evidence
                .split("\n")
                .filter((line) => line.trim())
                .map((line, i) => (
                  <span key={i} className="block">
                    “{line}”
                  </span>
                ))}
            </span>
            {!c.evidence_in_source && (
              <span className="mt-1.5 flex items-center gap-1 text-xs text-accent-ink">
                <AlertTriangle className="size-3.5" />
                이 문장이 원문에 그대로 있지 않습니다. 원문과 비교해 확인하세요.
              </span>
            )}
          </Field>
        </dl>
      )}

      {error && <p className="border-t border-danger/25 bg-danger-soft px-5 py-2 text-xs text-danger">{error}</p>}

      <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
        {editing ? (
          <>
            <Button variant="ghost" size="sm" disabled={busy !== null} onClick={() => (setDraft(toDraft(c)), setEditing(false))}>
              Cancel
            </Button>
            <Button size="sm" disabled={busy !== null} onClick={save}>
              {busy === "save" && <Loader2 className="size-3.5 animate-spin" />}
              Save
            </Button>
          </>
        ) : (
          <>
            <Button variant="danger" size="sm" disabled={busy !== null} onClick={() => run("ignore", () => ignoreCandidate(c.id))}>
              {busy === "ignore" ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
              Ignore
            </Button>
            <Button variant="secondary" size="sm" disabled={busy !== null} onClick={() => (setDraft(toDraft(c)), setEditing(true))}>
              <Pencil className="size-3.5" />
              Edit
            </Button>
            <Button size="sm" disabled={busy !== null} onClick={() => run("approve", async () => onApproved(await approveCandidate(c.id)))}>
              {busy === "approve" ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
              Approve
            </Button>
          </>
        )}
      </div>
    </Card>
  );
}
