import { Suspense } from "react";
import { DemoNavLink } from "@/components/layout/demo-nav-link";
import { WorkspaceHeader } from "@/components/layout/workspace-header";
import { CurrentProjectSelector, CurrentReviewAlerts, ProjectSelectorFallback } from "@/components/project/current-project";

// 프로젝트 Workspace 공통 layout (Overview / Decisions / Candidates / Sources / Ask Agent / Decision 상세)
export default function WorkspaceLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <WorkspaceHeader
        projectSlot={
          <Suspense fallback={<ProjectSelectorFallback />}>
            <CurrentProjectSelector />
          </Suspense>
        }
        demoSlot={
          <Suspense fallback={null}>
            <DemoNavLink variant="workspace" />
          </Suspense>
        }
      />
      <main className="animate-rise w-full px-9 pb-24 pt-10 sm:px-18 lg:px-27 sm:pt-14">
        {/* backend review monitor가 감지한 REVIEW_DUE 알림 (모든 화면 상단, 새로고침 없이 polling) */}
        <Suspense fallback={null}>
          <CurrentReviewAlerts />
        </Suspense>
        {children}
      </main>
    </>
  );
}
