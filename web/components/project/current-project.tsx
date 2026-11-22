import { getCurrentProject } from "@/lib/project";
import { ReviewAlerts } from "@/components/review/review-alerts";
import { ProjectSelector } from "./project-selector";

/** Workspace header용: 서버에서 현재 프로젝트를 읽어 전환기에 전달 (Suspense 안에서 렌더) */
export async function CurrentProjectSelector() {
  const { project, projects } = await getCurrentProject();
  return <ProjectSelector projects={projects} currentId={project.id} />;
}

export function ProjectSelectorFallback() {
  return <div className="h-8 w-40 animate-pulse rounded-lg bg-soft" />;
}

/** Workspace 공통: 현재 프로젝트의 재검토 알림 (프로젝트를 바꾸면 key가 바뀌어 새로 polling) */
export async function CurrentReviewAlerts() {
  const { project } = await getCurrentProject();
  return <ReviewAlerts key={project.id} projectId={project.id} />;
}
