import { METRIC_LABELS } from "@/lib/metrics";
import type { MetricName } from "@/lib/types";

export function formatDemoValue(value: number | null, unit?: string | null) {
  if (value === null) return "—";
  const n = Number.isInteger(value) ? value.toLocaleString("en-US") : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return unit ? `${n} ${unit}` : n;
}

export function metricTitle(name: string) {
  return METRIC_LABELS[name as MetricName] ?? name;
}

/** backend가 내려 주는 시각(ISO, UTC 또는 로컬) → HH:MM:SS */
export function clock(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString("en-GB", { hour12: false });
}

/** 다음 Review Monitor 확인까지 남은 초 (last_run_at + interval). 지났으면 0 */
export function secondsToNextCheck(lastRunAt: string | null, intervalSeconds: number, now = Date.now()) {
  if (!lastRunAt) return null;
  const last = new Date(lastRunAt).getTime();
  if (Number.isNaN(last)) return null;
  return Math.max(0, Math.round((last + intervalSeconds * 1000 - now) / 1000));
}
