import Link from "next/link";
import { cn } from "@/lib/utils";

export type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const variants: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-ink-2 disabled:bg-line-strong disabled:text-white",
  secondary: "border border-line-strong bg-surface text-ink hover:border-ink disabled:border-line disabled:text-faint",
  ghost: "text-muted hover:bg-soft hover:text-ink disabled:text-faint",
  danger: "border border-line-strong bg-surface text-danger hover:border-danger disabled:text-faint",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
  lg: "h-14 px-6 text-[15px]",
};

function classes(variant: Variant, size: Size, className?: string) {
  return cn(
    "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors duration-200 disabled:cursor-not-allowed",
    variants[variant],
    sizes[size],
    className,
  );
}

export type ButtonProps = React.ComponentProps<"button"> & { variant?: Variant; size?: Size };

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: React.ComponentProps<"button"> & { variant?: Variant; size?: Size }) {
  return <button className={classes(variant, size, className)} {...props} />;
}

export function ButtonLink({
  variant = "primary",
  size = "md",
  className,
  ...props
}: React.ComponentProps<typeof Link> & { variant?: Variant; size?: Size }) {
  return <Link className={classes(variant, size, className)} {...props} />;
}

/** 텍스트 링크 + 화살표 (editorial row의 보조 동작) */
export function ArrowLink({ className, children, ...props }: React.ComponentProps<typeof Link>) {
  return (
    <Link
      className={cn(
        "group/arrow inline-flex items-center gap-1.5 text-sm font-medium text-ink underline-offset-4 hover:underline",
        className,
      )}
      {...props}
    >
      {children}
      <span aria-hidden className="transition-transform duration-200 group-hover/arrow:translate-x-0.5">
        →
      </span>
    </Link>
  );
}
