import type { MetricName, MetricTrigger, ReviewTrigger } from "./types";

export const METRIC_LABELS: Record<MetricName, string> = {
  user_count: "User Count",
  search_api_p95_ms: "Search API p95",
  server_count: "Server Count",
};

export function formatNumber(n: number) {
  return n.toLocaleString("en-US");
}

export function formatMetricValue(value: number, unit: string) {
  return `${formatNumber(value)} ${unit}`;
}

/** 등록된 지표 키. 매핑되지 않은 지표(null)는 null — 다른 지표로 대체하지 않는다 */
export function metricKey(t: MetricTrigger): MetricName | null {
  return t.metric_key ?? t.metric ?? null;
}

/** 지표 이름: 등록된 지표는 라벨, 매핑되지 않은 지표는 원문 표현 */
export function metricLabel(t: MetricTrigger) {
  const key = metricKey(t);
  return key ? METRIC_LABELS[key] : (t.raw_metric ?? "미확인 지표");
}

export function formatTrigger(t: MetricTrigger) {
  return `${metricLabel(t)} ${t.operator} ${formatNumber(t.threshold)}`;
}

/** 코드 표기 그대로: user_count > 1000 / 매핑 안 된 지표는 원문 표현 + 단위 (null로 표시하지 않음) */
export function formatTriggerRaw(t: MetricTrigger) {
  const key = metricKey(t);
  if (key) return `${key} ${t.operator} ${t.threshold}`;
  return `${t.raw_metric ?? "미확인 지표"} ${t.operator} ${t.threshold}${t.unit ? ` ${t.unit}` : ""}`;
}

const DURATION_UNITS = { minute: "분", hour: "시간", day: "일", week: "주", month: "개월" } as const;
const DATE_OPS: Record<string, string> = { ">=": "이후", ">": "초과(이후)", "<=": "이전", "<": "미만(이전)", "==": "당일", "!=": "제외" };

/** 지표 매핑이 필요한 metric 조건이 있는지 (compound 안 포함) */
export function needsMapping(t: ReviewTrigger): boolean {
  if (t.type === "metric") return metricKey(t) === null || t.needs_mapping === true;
  if (t.type === "compound") return t.conditions.some(needsMapping);
  return false;
}

function formatNode(t: ReviewTrigger, nested: boolean): string {
  switch (t.type) {
    case "metric":
      return formatTriggerRaw(t);
    case "duration":
      return `${formatNumber(t.value)}${DURATION_UNITS[t.unit] ?? t.unit} ${t.mode === "consecutive" ? "연속" : "동안"}`;
    case "date":
      return `${t.date} ${DATE_OPS[t.operator] ?? t.operator}`;
    case "event":
      return nested ? t.description : `이벤트: ${t.description}`;
    case "manual":
      return nested ? t.description : `사람 확인 필요: ${t.description}`;
    case "compound":
      return t.conditions.map((c) => (c.type === "compound" ? `(${formatNode(c, true)})` : formatNode(c, true))).join(` ${t.logic} `);
  }
}

/** 재검토 조건 한 줄 표시 (backend format_review_trigger_v2와 같은 규칙 + 매핑 필요 표시)
 *  예) server_count >= 2 / search_api_p95_ms > 800 AND 3일 연속 / 일주일 프로덕션 배포 횟수 > 5 · 지표 매핑 필요 */
export function formatReviewTrigger(t: ReviewTrigger | null): string {
  if (!t) return "없음";
  const text = formatNode(t, false);
  return needsMapping(t) ? `${text} · 지표 매핑 필요` : text;
}
