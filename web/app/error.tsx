"use client";

import Link from "next/link";
import { RotateCw } from "lucide-react";
import { BACKEND_COMMAND, ServiceAlert } from "@/components/layout/service-alert";
import { Button } from "@/components/ui/button";

export default function Error({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div className="mx-auto max-w-2xl px-4 py-16 sm:py-24">
      <div className="eyebrow">Error</div>
      <h1 className="mb-8 mt-3 text-3xl font-semibold tracking-[-0.03em]">Something went wrong</h1>
      <ServiceAlert
        title="데이터를 불러오지 못했습니다"
        detail={`backend(FastAPI :8001)가 실행 중인지 확인하세요. ${process.env.NODE_ENV === "development" ? error.message : ""}`}
        command={BACKEND_COMMAND}
      />
      <Button variant="secondary" onClick={() => retry()}>
        <RotateCw className="size-4" />
        다시 시도
      </Button>
      <Link href="/" className="ml-4 text-sm text-muted underline-offset-4 hover:text-ink hover:underline">
        All projects
      </Link>
    </div>
  );
}
