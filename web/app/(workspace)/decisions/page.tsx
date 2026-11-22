import { Suspense } from "react";
import { Plus } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { MetricToolAlert } from "@/components/layout/service-alert";
import { ButtonLink } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/card";
import { DecisionList } from "@/components/decision/decision-list";
import { getDecisions, getMetrics } from "@/lib/api";
import { getCurrentProject } from "@/lib/project";

export default function DecisionsPage() {
  return (
    <>
      <PageHeader
        eyebrow="Decision memory"
        title="Decisions"
        description="사람이 승인한 결정만 저장되는 Decision Memory"
        actions={
          <ButtonLink href="/sources?add=1" variant="secondary">
            <Plus className="size-4" />
            Add source
          </ButtonLink>
        }
      />
      <Suspense fallback={<Skeleton className="h-96" />}>
        <DecisionsContent />
      </Suspense>
    </>
  );
}

async function DecisionsContent() {
  const { project } = await getCurrentProject();
  const [decisions, metricsRes] = await Promise.all([getDecisions(project.id), getMetrics(project.id)]);
  return (
    <>
      {metricsRes.connected && metricsRes.metric_tool_url && (
        <MetricToolAlert errors={metricsRes.errors} url={metricsRes.metric_tool_url} />
      )}
      <DecisionList decisions={decisions} />
    </>
  );
}
