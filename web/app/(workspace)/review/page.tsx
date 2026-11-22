import { Suspense } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Skeleton } from "@/components/ui/card";
import { ReviewWorkspace } from "@/components/review/review-workspace";
import { AddSourceButton } from "@/components/sources/add-source";
import { getCandidates } from "@/lib/api";
import { getCurrentProject } from "@/lib/project";

const TITLE = { eyebrow: "Review queue", title: "Candidates", description: "AI가 추출한 결정 후보를 검토하고 수정 · 승인합니다." };

// Review Queue 전용 화면. 원문 입력은 Sources와 같은 Add source(공통 컴포넌트)로 연다.
export default function ReviewPage() {
  return (
    <Suspense
      fallback={
        <>
          <PageHeader {...TITLE} />
          <Skeleton className="h-96" />
        </>
      }
    >
      <ReviewContent />
    </Suspense>
  );
}

async function ReviewContent() {
  const { project } = await getCurrentProject();
  const [pending, processed] = await Promise.all([
    getCandidates(project.id, "pending"),
    getCandidates(project.id, "processed"),
  ]);
  return (
    <>
      <PageHeader
        {...TITLE}
        description={
          <>
            {TITLE.description}
            <span className="mt-2 block text-sm font-medium text-ink">
              <span className="tabular-nums">{pending.length}</span> pending
            </span>
          </>
        }
        actions={<AddSourceButton project={project} variant="secondary" />}
      />
      {/* key: 프로젝트가 바뀌면 편집 상태도 초기화 */}
      <ReviewWorkspace key={project.id} project={project} pending={pending} processed={processed} />
    </>
  );
}
