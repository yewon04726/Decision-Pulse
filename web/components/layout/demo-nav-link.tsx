import Link from "next/link";
import { isDemoEnabled } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 발표/개발용 Demo 메뉴. backend DEMO_MODE_ENABLED=true일 때만 보인다 (Suspense 안에서 렌더) */
export async function DemoNavLink({ active = false, variant }: { active?: boolean; variant: "main" | "workspace" }) {
  if (!(await isDemoEnabled())) return null;
  return (
    <Link
      href="/demo"
      aria-current={active ? "page" : undefined}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 text-sm font-medium transition-colors",
        variant === "main" ? "border-b-2 py-[25px]" : "",
        active ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink",
      )}
    >
      Demo
      <span className="rounded border border-line-strong px-1 py-px text-[9px] font-semibold tracking-[0.08em] text-muted">DEV</span>
    </Link>
  );
}
