// 서버 컴포넌트용 데이터 접근 계층. backend(FastAPI :8001)를 호출한다.
// 모든 목록 조회는 project 범위다. 현재 지표는 매 요청마다 새로 조회해야 하므로 캐시하지 않는다.

import type { Candidate, DecisionView, DemoState, MetricsResponse, Project, ReviewMemo, Source } from "./types";

export const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8001";

export class BackendUnavailableError extends Error {
  constructor(path: string, cause: unknown) {
    super(`backend(${BACKEND_URL})에 연결할 수 없습니다: GET ${path}`, { cause });
    this.name = "BackendUnavailableError";
  }
}

async function get<T>(path: string): Promise<T | null> {
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}${path}`, { cache: "no-store" });
  } catch (e) {
    throw new BackendUnavailableError(path, e);
  }
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET ${path} → HTTP ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export async function getProjects(): Promise<Project[]> {
  return (await get<Project[]>("/api/projects")) ?? [];
}

export async function getDecisions(projectId: number): Promise<DecisionView[]> {
  return (await get<DecisionView[]>(`/api/decisions?project_id=${projectId}`)) ?? [];
}

export async function getDecision(id: string): Promise<DecisionView | null> {
  return get<DecisionView>(`/api/decisions/${encodeURIComponent(id)}`);
}

export async function getMetrics(projectId: number): Promise<MetricsResponse> {
  return (await get<MetricsResponse>(`/api/metrics?project_id=${projectId}`))!;
}

export async function getSources(projectId: number): Promise<Source[]> {
  return (await get<Source[]>(`/api/sources?project_id=${projectId}`)) ?? [];
}

export async function getCandidates(
  projectId: number,
  status?: "pending" | "approved" | "ignored" | "processed",
): Promise<Candidate[]> {
  const q = status ? `&status=${status}` : "";
  return (await get<Candidate[]>(`/api/candidates?project_id=${projectId}${q}`)) ?? [];
}

/** 결정의 Review Memo 목록 (최신순). 읽기 전용 — Overview의 최근 메모 표시용 */
export async function getReviewMemos(decisionId: string): Promise<ReviewMemo[]> {
  return (await get<ReviewMemo[]>(`/api/decisions/${encodeURIComponent(decisionId)}/review-memos`)) ?? [];
}

/** Demo Mode가 켜져 있는지 (backend DEMO_MODE_ENABLED). 꺼져 있거나 backend에 연결할 수 없으면 false — 메뉴를 숨긴다 */
export async function isDemoEnabled(): Promise<boolean> {
  try {
    return (await get<{ enabled: boolean }>("/api/demo/status"))?.enabled === true;
  } catch {
    return false;
  }
}

/** Demo Control Center 상태. Demo Mode가 꺼져 있으면 null (404) */
export async function getDemoState(): Promise<DemoState | null> {
  return get<DemoState>("/api/demo/state");
}
