// Project Selector(초기 화면)용 frontend adapter (서버 컴포넌트 전용).
// 기존 API(/api/projects, /api/decisions, /api/candidates)만 읽어 화면용 요약으로 바꾼다. backend 계약은 바꾸지 않는다.

import { getCandidates, getDecisions, getProjects } from "./api";
import { formatMetricValue, formatReviewTrigger } from "./metrics";
import { projectCode } from "./project-code";
import type { DecisionView, DisplayStatus, Project } from "./types";

export interface ReviewDuePreview {
  id: string;
  title: string;
  trigger: string;
  /** 현재 관측값 (metric 조건일 때만) */
  observed: string | null;
}

export interface ProjectSummary {
  project: Project;
  /** 짧은 식별자 (프로젝트명 머리글자) */
  code: string;
  /** null = 이 프로젝트의 결정/후보를 불러오지 못함 */
  counts: Record<DisplayStatus, number> | null;
  total: number;
  candidates: number | null;
  reviewDue: ReviewDuePreview[];
}

const REVIEW_PREVIEW_LIMIT = 5;

function countByStatus(decisions: DecisionView[]) {
  const counts: Record<DisplayStatus, number> = { ACTIVE: 0, REVIEW_DUE: 0, PENDING_REVIEW: 0, SUPERSEDED: 0, UNKNOWN: 0 };
  for (const d of decisions) counts[d.display_status] += 1;
  return counts;
}

function preview(d: DecisionView): ReviewDuePreview {
  const metric = d.evaluation?.metric;
  return {
    id: d.id,
    title: d.title,
    trigger: formatReviewTrigger(d.review_trigger),
    observed: metric ? formatMetricValue(metric.value, metric.unit) : null,
  };
}

async function summarize(project: Project): Promise<ProjectSummary> {
  const [decisions, candidates] = await Promise.allSettled([
    getDecisions(project.id),
    getCandidates(project.id, "pending"),
  ]);
  const list = decisions.status === "fulfilled" ? decisions.value : null;
  return {
    project,
    code: projectCode(project.name),
    counts: list ? countByStatus(list) : null,
    total: list?.length ?? 0,
    candidates: candidates.status === "fulfilled" ? candidates.value.length : null,
    reviewDue: (list ?? []).filter((d) => d.display_status === "REVIEW_DUE").slice(0, REVIEW_PREVIEW_LIMIT).map(preview),
  };
}

/** 모든 프로젝트의 요약. 프로젝트 목록 조회가 실패하면 예외(error.tsx), 개별 프로젝트 조회 실패는 counts=null */
export async function getProjectSummaries(): Promise<ProjectSummary[]> {
  const projects = await getProjects();
  return Promise.all(projects.map(summarize));
}
