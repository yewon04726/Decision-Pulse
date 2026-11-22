"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { SourceTypeBadge } from "@/components/decision/status-badge";
import { cn } from "@/lib/utils";
import type { MeetingEntry } from "@/lib/meeting-date";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MAX_TITLES = 2;

function shiftMonth(month: string, delta: number) {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string) {
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}

function dayLabel(date: string) {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", weekday: "short", timeZone: "UTC" });
}

/** 하단 전체 목록에서 해당 Source를 펼치고 그 위치로 스크롤 */
function openSource(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el instanceof HTMLDetailsElement) el.open = true;
  history.replaceState(null, "", `#${id}`);
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * 회의록 캘린더(가운데) + 그 달의 회의록 목록(오른쪽). 전후 달로 이동하면 두 영역이 함께 바뀐다.
 * 날짜를 누르면 오른쪽 목록이 그 날로 좁혀지고, 회의록을 누르면 하단 전체 목록의 원문이 열린다.
 */
export function MeetingCalendar({ entries, initialMonth, today }: { entries: MeetingEntry[]; initialMonth: string; today: string }) {
  const [month, setMonth] = useState(initialMonth);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const byDate = useMemo(() => {
    const map = new Map<string, MeetingEntry[]>();
    for (const e of entries) map.set(e.date, [...(map.get(e.date) ?? []), e]);
    return map;
  }, [entries]);

  const [y, m] = month.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const cells: (string | null)[] = [
    ...Array.from({ length: first }, () => null),
    ...Array.from({ length: days }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`),
  ];
  while (cells.length % 7) cells.push(null);

  const inMonth = entries.filter((e) => e.date.startsWith(month));
  const listed = selectedDay ? inMonth.filter((e) => e.date === selectedDay) : inMonth;
  const latest = entries[0]?.date.slice(0, 7);

  function go(delta: number) {
    setMonth((cur) => shiftMonth(cur, delta));
    setSelectedDay(null);
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)] lg:gap-12">
      {/* 캘린더 */}
      <div className="min-w-0">
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-2xl font-semibold tracking-[-0.03em] tabular-nums" aria-live="polite">
            {monthLabel(month)}
          </h3>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                setMonth(today.slice(0, 7));
                setSelectedDay(null);
              }}
              className="h-9 rounded-lg px-3 text-sm text-muted transition-colors hover:bg-soft hover:text-ink"
            >
              Today
            </button>
            <button
              type="button"
              onClick={() => go(-1)}
              aria-label="Previous month"
              className="flex size-9 items-center justify-center rounded-full border border-line-strong bg-surface transition-colors hover:border-ink hover:bg-ink hover:text-white"
            >
              <ChevronLeft className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => go(1)}
              aria-label="Next month"
              className="flex size-9 items-center justify-center rounded-full border border-line-strong bg-surface transition-colors hover:border-ink hover:bg-ink hover:text-white"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-7 border-b border-ink pb-2">
          {WEEKDAYS.map((w) => (
            <div key={w} className="eyebrow text-center text-[10px]">
              {w}
            </div>
          ))}
        </div>
        <div key={month} className="animate-rise grid grid-cols-7 border-l border-line">
          {cells.map((date, i) => {
            if (!date) return <div key={`blank-${i}`} className="min-h-14 border-b border-r border-line bg-soft/40 sm:min-h-24" />;
            const list = byDate.get(date) ?? [];
            const has = list.length > 0;
            // 같은 제목의 회의록(같은 문서를 여러 번 등록한 경우)은 한 줄로: "API 성능 회의 ×4"
            const titles = [...list.reduce((acc, e) => acc.set(e.title, (acc.get(e.title) ?? 0) + 1), new Map<string, number>())];
            const selected = selectedDay === date;
            const isToday = date === today;
            const content = (
              <>
                <span
                  className={cn(
                    "flex size-7 items-center justify-center rounded-full text-[13px] tabular-nums transition-colors",
                    selected ? "bg-ink font-semibold text-white" : has ? "font-semibold text-ink ring-[1.5px] ring-ink" : "text-muted",
                    isToday && !has && !selected && "font-semibold text-accent-ink",
                  )}
                >
                  {Number(date.slice(8))}
                </span>
                {has && (
                  <>
                    <span className="mt-1.5 hidden w-full flex-col gap-0.5 sm:flex">
                      {titles.slice(0, MAX_TITLES).map(([title, n]) => (
                        <span key={title} className="truncate text-[11px] leading-snug text-ink-2" title={title}>
                          {title}
                          {n > 1 && <span className="text-muted"> ×{n}</span>}
                        </span>
                      ))}
                      {titles.length > MAX_TITLES && <span className="text-[10.5px] text-muted">+{titles.length - MAX_TITLES} more</span>}
                    </span>
                    <span className="mt-1 text-[10px] tabular-nums text-muted sm:hidden">{list.length}</span>
                  </>
                )}
              </>
            );
            return has ? (
              <button
                key={date}
                type="button"
                onClick={() => setSelectedDay(selected ? null : date)}
                aria-pressed={selected}
                aria-label={`${date}: ${list.map((e) => e.title).join(", ")}`}
                className={cn(
                  "flex min-h-14 min-w-0 flex-col items-center border-b border-r border-line p-1 text-left transition-colors sm:min-h-24 sm:items-start sm:p-2",
                  selected ? "bg-surface" : "hover:bg-surface",
                )}
              >
                {content}
              </button>
            ) : (
              <div key={date} className="flex min-h-14 flex-col items-center border-b border-r border-line p-1 sm:min-h-24 sm:items-start sm:p-2">
                {content}
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-xs text-faint">
          날짜는 회의록에 적힌 날짜 기준입니다. 적힌 날짜가 없으면 그 회의록에서 나온 결정을 처음 Approve한 날짜로 표시합니다.
        </p>
      </div>

      {/* 그 달의 회의록 */}
      <aside className="min-w-0" aria-label={`${monthLabel(month)} meeting notes`}>
        <div className="flex items-baseline justify-between border-b border-ink pb-3">
          <h3 className="text-[15px] font-semibold">{selectedDay ? dayLabel(selectedDay) : monthLabel(month)}</h3>
          <span className="flex items-center gap-3 text-sm tabular-nums text-muted">
            {listed.length} notes
            {selectedDay && (
              <button type="button" onClick={() => setSelectedDay(null)} className="text-xs underline-offset-4 hover:text-ink hover:underline">
                Show month
              </button>
            )}
          </span>
        </div>
        {listed.length === 0 ? (
          <div key={`empty-${month}`} className="animate-rise py-8 text-sm text-muted">
            이 달에 등록된 회의록이 없습니다.
            {latest && latest !== month && (
              <button
                type="button"
                onClick={() => {
                  setMonth(latest);
                  setSelectedDay(null);
                }}
                className="ml-1 font-medium text-ink underline underline-offset-4"
              >
                최근 회의가 있는 달로 이동
              </button>
            )}
          </div>
        ) : (
          <ul
            key={`${month}-${selectedDay}`}
            className="animate-rise divide-y divide-line lg:max-h-[620px] lg:overflow-y-auto lg:overscroll-contain lg:pr-2"
          >
            {listed.map((e) => (
              <li key={e.id}>
                <button type="button" onClick={() => openSource(e.id)} className="group grid w-full grid-cols-[4.5rem_minmax(0,1fr)] gap-3 py-3.5 text-left">
                  <span className="text-xs tabular-nums text-muted">{dayLabel(e.date)}</span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-ink group-hover:underline group-hover:underline-offset-4">
                      {e.title}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted">
                      <SourceTypeBadge type={e.type} />
                      <span>{e.basis === "written" ? "회의록 날짜" : "Approve 날짜 기준"}</span>
                      <span>· 결정 {e.decisions}</span>
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
