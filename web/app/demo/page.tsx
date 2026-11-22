import { Suspense } from "react";
import { notFound } from "next/navigation";
import { DemoControlCenter } from "@/components/demo/demo-control-center";
import { DemoNavLink } from "@/components/layout/demo-nav-link";
import { PortfolioHeader } from "@/components/portfolio/portfolio-header";
import { Skeleton } from "@/components/ui/card";
import { getDemoState } from "@/lib/api";

// 발표/개발 전용. backend DEMO_MODE_ENABLED=false면 404 (backend mutation API도 404로 막혀 있다)
export default function DemoPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <PortfolioHeader
        active="demo"
        demoSlot={
          <Suspense fallback={null}>
            <DemoNavLink variant="main" active />
          </Suspense>
        }
      />
      <main className="animate-rise w-full px-9 pb-24 pt-10 sm:px-18 lg:px-27 sm:pt-14">
        <Suspense fallback={<DemoSkeleton />}>
          <DemoContent />
        </Suspense>
      </main>
    </div>
  );
}

async function DemoContent() {
  const state = await getDemoState();
  if (!state) notFound();
  return <DemoControlCenter initial={state} />;
}

function DemoSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading demo state">
      <div className="eyebrow">Demo</div>
      <h1 className="mt-3 text-[34px] font-semibold leading-[1.04] tracking-[-0.035em] sm:text-[44px]">Demo Control Center</h1>
      <Skeleton className="mt-14 h-80" />
    </div>
  );
}
