"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { Brand } from "@/components/layout/brand";
import { Button } from "@/components/ui/button";
import { CreateProjectForm } from "@/components/project/create-project-form";
import { setProjectCookie } from "@/lib/project-cookie";

/** 최상위 화면(Projects / Demo) 상단: 로고 + 메뉴 + New project. demoSlot = 서버에서 렌더한 Demo 메뉴 (Demo Mode일 때만) */
export function PortfolioHeader({ active = "projects", demoSlot }: { active?: "projects" | "demo"; demoSlot?: React.ReactNode }) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!creating) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setCreating(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setCreating(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [creating]);

  return (
    <header className="relative z-30 border-b border-line bg-surface">
      <div className="flex h-[72px] items-center gap-8 px-4 sm:gap-14 sm:px-12">
        <Brand />
        <nav aria-label="Main" className="flex flex-1 gap-6 sm:gap-8">
          <Link
            href="/"
            aria-current={active === "projects" ? "page" : undefined}
            className={
              active === "projects"
                ? "hidden border-b-2 border-ink py-[25px] text-sm font-medium text-ink sm:block"
                : "hidden border-b-2 border-transparent py-[25px] text-sm font-medium text-muted transition-colors hover:text-ink sm:block"
            }
          >
            Projects
          </Link>
          {demoSlot}
        </nav>
        <div ref={ref} className={active === "projects" ? "relative ml-auto" : "hidden"}>
          <Button variant="secondary" size="sm" onClick={() => setCreating((v) => !v)} aria-expanded={creating}>
            <Plus className="size-3.5" />
            New project
          </Button>
          {creating && (
            <div
              role="dialog"
              aria-label="New project"
              className="animate-rise absolute right-0 mt-2 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-3 shadow-[0_24px_48px_-24px_rgba(17,17,17,0.25)]"
            >
              <CreateProjectForm
                onCancel={() => setCreating(false)}
                onCreated={(p) => {
                  // 새 프로젝트를 선택된 상태로 다시 그린다
                  setProjectCookie(p.id);
                  setCreating(false);
                  router.refresh();
                }}
              />
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
