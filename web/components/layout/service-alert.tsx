import { ServerCrash } from "lucide-react";

/** 필요한 로컬 서버가 꺼져 있을 때 어떤 서버를 켜야 하는지 안내 */
export function ServiceAlert({
  title,
  detail,
  command,
}: {
  title: string;
  detail?: string;
  command: string;
}) {
  return (
    <div className="mb-8 border-l-2 border-danger bg-danger-soft px-5 py-4 text-sm text-ink" role="alert">
      <div className="flex items-center gap-2 font-semibold text-danger">
        <ServerCrash className="size-4" />
        {title}
      </div>
      {detail && <p className="mt-1 text-xs text-ink-2">{detail}</p>}
      <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs text-ink">{command}</pre>
    </div>
  );
}

export const METRIC_TOOL_COMMAND = `cd ~/decision-debt-tool
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000`;

export const BACKEND_COMMAND = `cd ~/Projects/decision-debt/backend
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8001`;

/** check_metric(:8000) 조회 실패가 하나라도 있으면 안내 표시 */
export function MetricToolAlert({ errors, url }: { errors: Record<string, string>; url: string }) {
  const failed = Object.keys(errors);
  if (failed.length === 0) return null;
  return (
    <ServiceAlert
      title={`check_metric 서버(${url})에 연결할 수 없어 재검토 조건을 판정하지 못했습니다`}
      detail={`조회 실패: ${failed.join(", ")} → 해당 결정은 UNKNOWN으로 표시됩니다. 아래 명령으로 기존 metric 서버를 실행하세요.`}
      command={METRIC_TOOL_COMMAND}
    />
  );
}
