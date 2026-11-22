import { Suspense } from "react";
import { cookies } from "next/headers";
import { DemoNavLink } from "@/components/layout/demo-nav-link";
import { PortfolioHeader } from "@/components/portfolio/portfolio-header";
import { ProjectPortfolio } from "@/components/portfolio/project-portfolio";
import { Skeleton } from "@/components/ui/card";
import { getProjectSummaries } from "@/lib/portfolio";
import { PROJECT_COOKIE } from "@/lib/project-cookie";

// 앱 진입 화면: Project Selector → Open project → 기존 Workspace(/overview)
export default function ProjectsPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <PortfolioHeader
        demoSlot={
          <Suspense fallback={null}>
            <DemoNavLink variant="main" />
          </Suspense>
        }
      />
      <Suspense fallback={<PortfolioSkeleton />}>
        <Portfolio />
      </Suspense>
    </div>
  );
}

async function Portfolio() {
  const [summaries, store] = await Promise.all([getProjectSummaries(), cookies()]);
  if (summaries.length === 0) {
    return (
      <div className="mx-auto flex max-w-md flex-1 flex-col items-center justify-center px-6 py-24 text-center">
        <div className="eyebrow">Projects</div>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em]">No projects yet</h1>
        <p className="mt-2 text-sm text-muted">상단의 New project로 첫 프로젝트를 만드세요.</p>
      </div>
    );
  }
  const selected = Number(store.get(PROJECT_COOKIE)?.value);
  // 프로젝트 목록이 바뀌면(새 프로젝트 생성) 쿠키의 프로젝트를 다시 선택하도록 remount
  return (
    <ProjectPortfolio
      key={summaries.map((s) => s.project.id).join(",")}
      summaries={summaries}
      initialId={Number.isFinite(selected) ? selected : null}
    />
  );
}

function PortfolioSkeleton() {
  return (
    <div className="flex flex-1 flex-col lg:flex-row" aria-busy="true" aria-label="Loading projects">
      <div className="flex flex-1 flex-col px-4 pt-10 sm:px-12 sm:pt-14 lg:px-14">
        <div className="eyebrow">Projects</div>
        <h1 className="mt-3 text-[38px] font-semibold leading-[1.02] tracking-[-0.04em] sm:text-[52px]">Choose a project</h1>
        <div className="flex flex-1 items-center justify-center py-16">
          <Skeleton className="h-[360px] w-[260px] rounded-2xl sm:h-[400px] sm:w-[300px]" />
        </div>
      </div>
      <div className="flex w-full flex-col gap-6 border-line bg-surface px-4 py-10 sm:px-8 lg:w-[400px] lg:border-l lg:px-10 max-lg:border-t">
        <Skeleton className="h-16" />
        <Skeleton className="h-40 rounded-2xl" />
        <Skeleton className="h-32" />
      </div>
    </div>
  );
}
