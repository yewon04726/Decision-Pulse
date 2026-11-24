"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Search } from "lucide-react";
import { EmptyState } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { reviewBucket, type ReviewBucket } from "@/lib/metrics";
import type { DecisionView } from "@/lib/types";
import { DecisionRow } from "./decision-row";

// Active / Review due / Unknown = 자동 평가되는 결정의 review state. 데이터 연결이 필요하거나 사람이 확인하는 조건은 따로
const FILTERS: { value: ReviewBucket | "ALL"; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "REVIEW_DUE", label: "Review due" },
  { value: "ACTIVE", label: "Active" },
  { value: "NEEDS_MAPPING", label: "Needs mapping" },
  { value: "MANUAL", label: "Manual review" },
  { value: "PENDING_REVIEW", label: "Pending review" },
  { value: "SUPERSEDED", label: "Superseded" },
  { value: "UNKNOWN", label: "Unknown" },
];

// REVIEW_DUE를 항상 맨 앞에
const ORDER: Record<ReviewBucket, number> = {
  REVIEW_DUE: 0, UNKNOWN: 1, PENDING_REVIEW: 2, ACTIVE: 3, NEEDS_MAPPING: 4, MANUAL: 5, SUPERSEDED: 6,
};
const HIDE_WHEN_EMPTY = new Set<ReviewBucket | "ALL">(["UNKNOWN", "NEEDS_MAPPING", "MANUAL"]);

export function DecisionList({ decisions }: { decisions: DecisionView[] }) {
  const searchParams = useSearchParams();
  const initial = (searchParams.get("status") as ReviewBucket | null) ?? "ALL";
  const [filter, setFilter] = useState<ReviewBucket | "ALL">(initial);
  const [query, setQuery] = useState("");

  const q = query.trim().toLowerCase();
  const visible = decisions
    .filter((d) => filter === "ALL" || reviewBucket(d) === filter)
    .filter((d) => !q || [d.topic, d.title, d.decision, d.rationale, d.id].join(" ").toLowerCase().includes(q))
    .sort((a, b) => ORDER[reviewBucket(a)] - ORDER[reviewBucket(b)]);

  const count = (s: ReviewBucket | "ALL") =>
    s === "ALL" ? decisions.length : decisions.filter((d) => reviewBucket(d) === s).length;

  return (
    <>
      <div className="flex flex-col gap-4 border-b border-ink lg:flex-row lg:items-end lg:justify-between">
        <div className="-mb-px flex gap-6 overflow-x-auto [scrollbar-width:none]" role="tablist" aria-label="Filter by status">
          {FILTERS.filter((f) => !HIDE_WHEN_EMPTY.has(f.value) || count(f.value) > 0 || filter === f.value).map((f) => (
            <button
              key={f.value}
              role="tab"
              aria-selected={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={cn(
                "shrink-0 border-b-2 pb-3 text-sm font-medium transition-colors",
                filter === f.value ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {f.label}
              <span className="ml-1.5 tabular-nums text-faint">{count(f.value)}</span>
            </button>
          ))}
        </div>
        <label className="relative mb-3 block lg:w-72">
          <Search className="pointer-events-none absolute left-0 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="topic, 결정, 이유로 검색"
            aria-label="Search decisions"
            className="h-9 w-full border-b border-line-strong bg-transparent pl-6 pr-2 text-sm text-ink outline-none transition-colors placeholder:text-faint focus:border-ink"
          />
        </label>
      </div>

      {visible.length > 0 ? (
        <div>
          {visible.map((d) => (
            <DecisionRow key={d.id} decision={d} />
          ))}
        </div>
      ) : (
        <EmptyState className="mt-8">조건에 맞는 결정이 없습니다.</EmptyState>
      )}
    </>
  );
}
