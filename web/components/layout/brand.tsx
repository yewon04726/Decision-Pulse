import Link from "next/link";
import { cn } from "@/lib/utils";

export const BRAND = "Decision Pulse";

/** 로고 (pulse line + 이름). 누르면 Project Selector로 */
export function Brand({ className, compact = false }: { className?: string; compact?: boolean }) {
  return (
    <Link
      href="/"
      className={cn("flex shrink-0 items-center gap-2.5 text-ink", className)}
      aria-label={`${BRAND} — all projects`}
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M2 12h4l3-8 6 16 3-8h4" />
      </svg>
      <span className={cn("text-[17px] font-semibold tracking-[-0.02em] sm:text-lg", compact && "max-sm:sr-only")}>{BRAND}</span>
    </Link>
  );
}
