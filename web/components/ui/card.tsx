import { cn } from "@/lib/utils";

/** 흰 surface + hairline. 그림자 없음 — 꼭 필요한 곳(입력 폼, 메모 본문)에만 쓴다 */
export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("rounded-xl border border-line bg-surface", className)} {...props} />;
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b border-line px-5 py-4", className)}>
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

/**
 * Editorial section: eyebrow + 제목 + 위쪽 hairline. 박스 대신 공간과 선으로 구획한다.
 * count가 있으면 제목 옆에 숫자를 붙인다.
 */
export function Section({
  eyebrow,
  title,
  count,
  description,
  action,
  className,
  children,
  id,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  count?: number;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className={cn("scroll-mt-28 border-t border-ink pt-5", className)}>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
          <h2 className="flex items-baseline gap-2.5 text-xl font-semibold tracking-tight text-ink sm:text-2xl">
            {title}
            {count !== undefined && <span className="text-base font-medium tabular-nums text-faint">{count}</span>}
          </h2>
          {description && <p className="mt-1 text-sm text-muted">{description}</p>}
        </div>
        {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
      </div>
      {children}
    </section>
  );
}

/** 라벨 + 값 한 쌍 */
export function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <dt className="eyebrow text-[10px]">{label}</dt>
      <dd className="mt-1.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

/** 비어 있는 목록 안내 */
export function EmptyState({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-xl border border-dashed border-line-strong px-6 py-10 text-center text-sm text-muted", className)}>
      {children}
    </div>
  );
}

/** 로딩 자리 표시 */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-xl bg-soft", className)} />;
}
