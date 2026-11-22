"use client";

import { Suspense } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Brand } from "./brand";

// 실제 존재하는 화면만 메뉴로 둔다
const NAV = [
  { href: "/overview", label: "Overview" },
  { href: "/decisions", label: "Decisions" },
  { href: "/review", label: "Candidates" },
  { href: "/sources", label: "Sources" },
  { href: "/agent", label: "Ask Agent" },
];

// usePathname은 런타임 값이라 cacheComponents에서는 Suspense 안에서만 읽는다.
// fallback은 active 표시 없는 동일한 메뉴라 레이아웃이 흔들리지 않는다.
function ActiveNav() {
  return <NavLinks pathname={usePathname()} />;
}

function NavLinks({ pathname }: { pathname: string }) {
  return (
    <>
      {NAV.map(({ href, label }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative shrink-0 py-3.5 text-sm font-medium transition-colors duration-200",
              "after:absolute after:inset-x-0 after:bottom-0 after:h-[2px] after:origin-left after:bg-ink after:transition-transform after:duration-300",
              active ? "text-ink after:scale-x-100" : "text-muted after:scale-x-0 hover:text-ink",
            )}
          >
            {label}
          </Link>
        );
      })}
    </>
  );
}

/** projectSlot: 서버에서 렌더한 프로젝트 전환기 (layout에서 Suspense로 감싸 전달) */
export function WorkspaceHeader({ projectSlot, demoSlot }: { projectSlot: React.ReactNode; demoSlot?: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface">
      <div className="flex h-16 items-center gap-2 px-9 sm:px-18 lg:px-27">
        <Brand compact />
        <span className="px-1 text-line-strong" aria-hidden>
          /
        </span>
        {projectSlot}
        <div className="ml-auto flex shrink-0 items-center gap-6">
          {demoSlot}
          <Link href="/" className="hidden items-center gap-1.5 text-sm text-muted transition-colors hover:text-ink sm:inline-flex">
            <span aria-hidden>←</span> All projects
          </Link>
        </div>
      </div>
      <nav aria-label="Project" className="flex gap-6 overflow-x-auto px-9 [scrollbar-width:none] sm:gap-8 sm:px-18 lg:px-27">
        <Suspense fallback={<NavLinks pathname="" />}>
          <ActiveNav />
        </Suspense>
      </nav>
    </header>
  );
}
