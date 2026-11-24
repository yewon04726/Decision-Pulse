// 클라이언트 컴포넌트용 쓰기/AI API. /api/* 는 next.config rewrite로 backend(:8001)에 전달된다.
// 성공 응답을 받은 뒤에만 UI 상태를 바꾸고, 목록은 router.refresh()로 서버에서 다시 읽는다.

import type {
  AgentResponse,
  Candidate,
  Decision,
  DeepReviewRun,
  DemoChange,
  DemoPreview,
  DemoState,
  KnowledgeSyncStatus,
  Project,
  ReviewEvent,
  ReviewMemo,
  ReviewTrigger,
  Source,
  SourceType,
} from "./types";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("backend(:8001)에 연결할 수 없습니다.");
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => null);
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${res.status}`);
  }
  return res.json();
}

export function createProject(name: string, description: string) {
  return request<Project>("POST", "/api/projects", { name, description });
}

export function extractDecisions(input: { projectId: number; title: string; type: SourceType; content: string }) {
  // warnings: 후보로 만들지 않은 항목 안내 (예: 재검토 조건만 있고 현재 결정이 명시되지 않음)
  return request<{ candidates: Candidate[]; warnings?: string[] }>("POST", "/api/extract", {
    project_id: input.projectId,
    title: input.title,
    type: input.type,
    content: input.content,
  });
}

export interface CandidatePatch {
  topic?: string;
  title?: string;
  decision?: string;
  rationale?: string;
  review_trigger?: ReviewTrigger;
  clear_review_trigger?: boolean;
}

export function updateCandidate(id: string, patch: CandidatePatch) {
  return request<Candidate>("PATCH", `/api/candidates/${id}`, patch);
}

export function approveCandidate(id: string) {
  return request<{ candidate: Candidate; decision: Decision; knowledge_sync: KnowledgeSyncStatus }>(
    "POST",
    `/api/candidates/${id}/approve`,
  );
}

/** Processed 기록 정리: ignored 후보만 삭제된다 (approved 승인 기록·Decision·Source는 유지) */
export function deleteIgnoredCandidates(projectId: number, ids: string[]) {
  return request<{ deleted: string[]; skipped: { id: string; reason: string }[] }>("POST", "/api/candidates/delete", {
    project_id: projectId,
    ids,
  });
}

export function ignoreCandidate(id: string) {
  return request<Candidate>("POST", `/api/candidates/${id}/ignore`);
}

/** Ask Agent: backend가 프로젝트에 연결된 Dify Chatflow를 호출한다 (API Key는 backend에만 있음) */
export function askAgent(projectId: number, query: string) {
  return request<AgentResponse>("POST", "/api/agent/chat", { project_id: projectId, query });
}

/** backend review monitor가 감지했고 아직 확인하지 않은 REVIEW_DUE 알림 */
export function getOpenReviewAlerts(projectId: number) {
  return request<ReviewEvent[]>(
    "GET",
    `/api/review-events?project_id=${projectId}&acknowledged=false&new_status=REVIEW_DUE`,
  );
}

/** 알림 확인 (이벤트만 숨김. Decision 상태는 바뀌지 않는다) */
export function acknowledgeReviewEvent(eventId: number) {
  return request<ReviewEvent>("POST", `/api/review-events/${eventId}/acknowledge`);
}

/** 오디오 업로드 (multipart). 업로드 실패(형식/용량)는 여기서 오류로 끝난다. 전사는 backend에서 따로 진행된다 */
export async function uploadAudioSource(projectId: number, title: string, file: File): Promise<Source> {
  const form = new FormData();
  form.append("project_id", String(projectId));
  form.append("title", title);
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch("/api/sources/audio", { method: "POST", body: form });
  } catch {
    throw new Error("backend(:8001)에 연결할 수 없습니다.");
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => null);
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return res.json();
}

export function getSource(id: string) {
  return request<Source>("GET", `/api/sources/${id}`);
}

/** 전사 실패한 오디오를 다시 전사 (원본 오디오는 그대로) */
export function retranscribeSource(id: string) {
  return request<Source>("POST", `/api/sources/${id}/transcribe`);
}

/** 등록된 Source(전사 완료된 오디오 포함)에서 결정 후보 추출. 기존 /api/extract와 같은 정책(auto)을 쓴다 */
export function extractFromSource(projectId: number, sourceId: string) {
  return request<{ candidates: Candidate[]; warnings?: string[]; extraction?: { final_provider?: string; escalated?: boolean } }>(
    "POST",
    "/api/extract",
    { project_id: projectId, source_id: sourceId },
  );
}


/** Review Memo API 오류. code: not_review_due | memo_generating | review_memo_disabled | decision_not_found */
export class ReviewMemoApiError extends Error {
  readonly code: string | null;
  readonly memoId: number | null;

  constructor(message: string, code: string | null, memoId: number | null) {
    super(message);
    this.code = code;
    this.memoId = memoId;
  }
}

/**
 * REVIEW_DUE 결정의 Review Memo 생성 요청 (Hermes가 backend에서 실행, 1~2분). 응답은 바로 온다: status=generating.
 * 같은 review 상태의 완료 memo가 있으면 그것을 돌려준다. regenerate=true면 새 memo (이전 memo는 남는다).
 */
export async function generateReviewMemo(decisionId: string, regenerate = false): Promise<ReviewMemo> {
  let res: Response;
  try {
    res = await fetch(`/api/decisions/${encodeURIComponent(decisionId)}/review-memo`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ regenerate }),
    });
  } catch {
    throw new ReviewMemoApiError("backend(:8001)에 연결할 수 없습니다.", null, null);
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => null);
    if (detail && typeof detail === "object") {
      throw new ReviewMemoApiError(detail.message ?? `HTTP ${res.status}`, detail.code ?? null, detail.memo_id ?? null);
    }
    throw new ReviewMemoApiError(typeof detail === "string" ? detail : `HTTP ${res.status}`, null, null);
  }
  return res.json();
}

/** 이 결정의 Review Memo 목록 (최신순, 재생성한 이전 memo 포함) */
export function getReviewMemos(decisionId: string) {
  return request<ReviewMemo[]>("GET", `/api/decisions/${encodeURIComponent(decisionId)}/review-memos`);
}

export function getReviewMemo(memoId: number) {
  return request<ReviewMemo>("GET", `/api/review-memos/${memoId}`);
}

// ---- Demo Control Center (외부 상태값만 바꾼다. REVIEW_DUE / Telegram은 backend Review Monitor가 처리)

export function getDemoState() {
  return request<DemoState>("GET", "/api/demo/state");
}

export function previewDemoMetric(metric: string, value: number) {
  return request<DemoPreview>("GET", `/api/demo/state/${encodeURIComponent(metric)}/preview?value=${value}`);
}

export function setDemoMetric(metric: string, value: number) {
  return request<DemoChange>("PATCH", `/api/demo/state/${encodeURIComponent(metric)}`, { value });
}

export function resetDemoState() {
  return request<DemoChange[]>("POST", "/api/demo/reset");
}


// ---------------------------------------------------------------- Deep Review (Paperclip multi-agent)

export class DeepReviewApiError extends Error {
  readonly code: string | null;
  readonly runId: number | null;

  constructor(message: string, code: string | null, runId: number | null) {
    super(message);
    this.code = code;
    this.runId = runId;
  }
}

/** Deep Review 사용 가능 여부 (DEEP_REVIEW_ENABLED). 꺼져 있으면 버튼을 숨긴다 */
export function getDeepReviewStatus() {
  return request<{ enabled: boolean }>("GET", "/api/deep-review/status");
}

/** REVIEW_DUE 결정의 Deep Review 시작 (Researcher → Analyst → Critic). 응답은 바로: 202 + PENDING */
export async function startDeepReview(decisionId: string, projectId?: number): Promise<DeepReviewRun> {
  const qs = projectId ? `?project_id=${projectId}` : "";
  let res: Response;
  try {
    res = await fetch(`/api/decisions/${encodeURIComponent(decisionId)}/deep-review${qs}`, { method: "POST" });
  } catch {
    throw new DeepReviewApiError("backend(:8001)에 연결할 수 없습니다.", null, null);
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => null);
    if (detail && typeof detail === "object") {
      throw new DeepReviewApiError(detail.message ?? `HTTP ${res.status}`, detail.code ?? null, detail.run_id ?? null);
    }
    throw new DeepReviewApiError(typeof detail === "string" ? detail : `HTTP ${res.status}`, null, null);
  }
  return res.json();
}

export function getDeepReviews(decisionId: string) {
  return request<DeepReviewRun[]>("GET", `/api/decisions/${encodeURIComponent(decisionId)}/deep-reviews`);
}

export function getDeepReview(runId: number) {
  return request<DeepReviewRun>("GET", `/api/deep-reviews/${runId}`);
}
