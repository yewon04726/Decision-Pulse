// Review Queue 후보 Edit 폼 ↔ PATCH 변환.
// 핵심: 조건을 바꾸지 않았으면 review_trigger를 보내지 않는다 → 기존 JSON(V2 필드 포함)이 그대로 유지된다.
// event / duration / date / compound / 매핑 안 된 metric은 이 폼에서 편집하지 않는다 (legacy metric으로 바꾸지 않음).
import type { CandidatePatch } from "./client-api";
import { metricKey } from "./metrics";
import type { Candidate, MetricName, Operator, ReviewTrigger } from "./types";

// readonly = 이 폼에서 편집할 수 없는 V2 조건
export type TriggerKind = "none" | "manual" | "metric" | "readonly";

export interface CandidateDraft {
  topic: string;
  title: string;
  decision: string;
  rationale: string;
  kind: TriggerKind;
  description: string;
  metric: MetricName;
  operator: Operator;
  threshold: string;
}

/** 이 폼으로 편집 가능한 조건: 없음, manual, 등록된 지표의 단일 metric */
export function isEditableTrigger(t: ReviewTrigger | null) {
  return t === null || t.type === "manual" || (t.type === "metric" && metricKey(t) !== null);
}

export function toDraft(c: Candidate): CandidateDraft {
  const t = c.review_trigger;
  const kind: TriggerKind = !isEditableTrigger(t) ? "readonly" : t ? (t.type as "manual" | "metric") : "none";
  return {
    topic: c.topic,
    title: c.title,
    decision: c.decision,
    rationale: c.rationale,
    kind,
    description: (t && "description" in t ? t.description : null) ?? t?.raw_condition ?? "",
    metric: ((t?.type === "metric" && metricKey(t)) || "user_count") as MetricName,
    operator: (t?.type === "metric" ? t.operator : ">") as Operator,
    threshold: t?.type === "metric" ? String(t.threshold) : "",
  };
}

const TRIGGER_FIELDS = ["kind", "description", "metric", "operator", "threshold"] as const;

/** 저장할 PATCH. 입력이 잘못되면 error (빈 threshold를 0으로 바꾸지 않는다) */
export function buildCandidatePatch(c: Candidate, draft: CandidateDraft): { patch: CandidatePatch } | { error: string } {
  const patch: CandidatePatch = {
    topic: draft.topic,
    title: draft.title,
    decision: draft.decision,
    rationale: draft.rationale,
  };
  const before = toDraft(c);
  const triggerChanged = draft.kind !== "readonly" && TRIGGER_FIELDS.some((k) => draft[k] !== before[k]);
  if (!triggerChanged) return { patch };

  const t = c.review_trigger;
  if (draft.kind === "none") {
    patch.clear_review_trigger = true;
  } else if (draft.kind === "manual") {
    if (!draft.description.trim()) return { error: "manual 조건의 원문 문장을 입력하세요." };
    patch.review_trigger = { ...(t?.type === "manual" ? t : {}), type: "manual", description: draft.description.trim() };
  } else {
    const threshold = draft.threshold.trim() === "" ? NaN : Number(draft.threshold);
    if (!Number.isFinite(threshold)) return { error: "threshold에 숫자를 입력하세요." };
    const description = draft.description.trim() || null;
    patch.review_trigger = {
      ...(t?.type === "metric" ? t : {}), // raw_metric, unit 등 기존 필드 유지
      type: "metric",
      metric: draft.metric,
      metric_key: draft.metric,
      operator: draft.operator,
      threshold,
      description,
      raw_condition: description,
    };
  }
  return { patch };
}
