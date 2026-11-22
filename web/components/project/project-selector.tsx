"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Check, ChevronDown, Plus } from "lucide-react";
import { setProjectCookie } from "@/lib/project-cookie";
import { projectCode } from "@/lib/project-code";
import { cn } from "@/lib/utils";
import type { Project } from "@/lib/types";
import { CreateProjectForm } from "./create-project-form";

export function ProjectMark({ name, className, inverted = false }: { name: string; className?: string; inverted?: boolean }) {
  return (
    <span
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-md text-[10px] font-semibold tracking-[0.06em]",
        inverted ? "bg-ink text-white" : "border border-line-strong bg-surface text-ink",
        className,
      )}
      aria-hidden
    >
      {projectCode(name)}
    </span>
  );
}

/** Workspace header의 breadcrumb 프로젝트 전환기 (현재 프로젝트는 쿠키, 전환 후 router.refresh) */
export function ProjectSelector({ projects, currentId }: { projects: Project[]; currentId: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = projects.find((p) => p.id === currentId) ?? projects[0];

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function switchTo(id: number) {
    setOpen(false);
    setCreating(false);
    if (id === currentId) return;
    setProjectCookie(id);
    // 결정 상세는 특정 프로젝트에 속하므로 전환 시 목록으로 이동한다
    if (pathname.startsWith("/decisions/")) router.push("/decisions");
    router.refresh();
  }

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex min-w-0 items-center gap-2 rounded-lg px-1.5 py-1 text-left transition-colors hover:bg-soft"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Project: ${current.name}. Switch project`}
      >
        <span className="truncate text-[15px] font-medium text-ink">{current.name}</span>
        <ChevronDown className={cn("size-4 shrink-0 text-muted transition-transform duration-200", open && "rotate-180")} />
      </button>

      {open && (
        <div className="animate-rise absolute left-0 z-40 mt-2 w-[min(18rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-1.5 shadow-[0_24px_48px_-24px_rgba(17,17,17,0.25)]">
          <div className="eyebrow px-2.5 pb-1.5 pt-2 text-[10px]">Switch project</div>
          <ul role="listbox" aria-label="Projects" className="max-h-64 overflow-y-auto">
            {projects.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => switchTo(p.id)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-soft",
                    p.id === current.id && "bg-soft",
                  )}
                  role="option"
                  aria-selected={p.id === current.id}
                >
                  <ProjectMark name={p.name} className="size-6" inverted={p.id === current.id} />
                  <span className="min-w-0 flex-1 truncate font-medium text-ink">{p.name}</span>
                  {p.id === current.id && <Check className="size-4 text-ink" />}
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-1 border-t border-line pt-1">
            {creating ? (
              <div className="p-2">
                <CreateProjectForm onCreated={(p) => switchTo(p.id)} onCancel={() => setCreating(false)} />
              </div>
            ) : (
              <>
                <button
                  onClick={() => setCreating(true)}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium text-ink hover:bg-soft"
                >
                  <Plus className="size-4" />
                  New project
                </button>
                <Link
                  href="/"
                  onClick={() => setOpen(false)}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-sm text-muted hover:bg-soft hover:text-ink"
                >
                  All projects
                  <span aria-hidden>→</span>
                </Link>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
