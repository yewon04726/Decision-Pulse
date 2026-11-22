import { Suspense } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Skeleton } from "@/components/ui/card";
import { AgentChat } from "@/components/agent/agent-chat";
import { getSources } from "@/lib/api";
import { getCurrentProject } from "@/lib/project";

export default function AgentPage() {
  return (
    <>
      <PageHeader
        eyebrow="Knowledge"
        title="Ask Agent"
        description="프로젝트에 연결된 Dify Agent · Qwen2.5-7B-Instruct · RAG + check_metric Function Calling"
      />
      <Suspense fallback={<Skeleton className="h-[560px]" />}>
        <AgentContent />
      </Suspense>
    </>
  );
}

async function AgentContent() {
  const { project } = await getCurrentProject();
  const sources = (await getSources(project.id)).map(({ id, title, type }) => ({ id, title, type }));
  // key: 프로젝트가 바뀌면 대화를 초기화 (다른 프로젝트 대화가 섞이지 않게)
  return <AgentChat key={project.id} project={project} sources={sources} />;
}
