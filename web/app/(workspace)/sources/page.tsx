import { Suspense } from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, Section, Skeleton } from "@/components/ui/card";
import { HumanAuthoredTag, SourceTypeBadge } from "@/components/decision/status-badge";
import { AudioStatus } from "@/components/sources/audio-status";
import { AddSourceButton } from "@/components/sources/add-source";
import { MeetingCalendar } from "@/components/sources/meeting-calendar";
import { getCandidates, getDecisions, getSources } from "@/lib/api";
import { buildMeetingEntries, sourceLinks, writtenDate } from "@/lib/meeting-date";
import { getCurrentProject } from "@/lib/project";

const TITLE = { eyebrow: "Evidence", title: "Sources", description: "결정의 근거가 되는 원문과 회의 기록. 모든 원문 입력은 여기서 시작합니다." };

// Sources = 모든 원문 입력(Add source) + 원문 archive. ?add=1이면 Add source를 열고 시작한다
export default function SourcesPage({ searchParams }: PageProps<"/sources">) {
  return (
    <Suspense
      fallback={
        <>
          <PageHeader {...TITLE} />
          <Skeleton className="h-96" />
        </>
      }
    >
      {searchParams.then((sp) => (
        <SourcesContent openAdd={sp.add === "1"} />
      ))}
    </Suspense>
  );
}

async function SourcesContent({ openAdd }: { openAdd: boolean }) {
  const { project } = await getCurrentProject();
  const [sources, decisions, candidates] = await Promise.all([
    getSources(project.id),
    getDecisions(project.id),
    getCandidates(project.id), // 전체 상태 (pending / approved / ignored)
  ]);

  // Calendar = 결정 활동과 연결된 회의록만 / All sources = 모든 Source (필터 없음)
  const links = sourceLinks(candidates, decisions);
  const { approvedAt, decisionCount, candidateCount } = links;
  const entries = buildMeetingEntries(sources, links);

  // 전체 목록 날짜: 회의록 캘린더와 같은 규칙 (적힌 날짜 → Approve 날짜), 회의록이 아닌 문서는 Source 날짜. 최신순, 날짜 없는 것은 맨 뒤
  const entryById = new Map(entries.map((e) => [e.id, e]));
  const DATE_BASIS = { written: null, approved: "Approve 기준", source: "등록 날짜" } as const;
  const datedOf = (s: (typeof sources)[number]): { date: string; basis: keyof typeof DATE_BASIS } | null => {
    const written = entryById.get(s.id)?.basis === "approved" ? null : writtenDate(s.content ?? "")?.date;
    if (written) return { date: written, basis: "written" };
    const approvedDate = approvedAt.get(s.id)?.slice(0, 10);
    if (approvedDate) return { date: approvedDate, basis: "approved" };
    return s.date ? { date: s.date, basis: "source" } : null;
  };
  const dateOf = (s: (typeof sources)[number]) => datedOf(s)?.date ?? null;
  const sorted = [...sources].sort((a, b) => {
    const da = dateOf(a);
    const db = dateOf(b);
    if (da && db && da !== db) return db.localeCompare(da);
    if (!da !== !db) return da ? -1 : 1;
    return b.id.localeCompare(a.id);
  });

  // 캘린더 첫 화면: 이번 달 (회의가 없으면 가장 최근 회의가 있는 달)
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const thisMonth = today.slice(0, 7);
  const initialMonth = entries.some((e) => e.date.startsWith(thisMonth)) || entries.length === 0 ? thisMonth : entries[0].date.slice(0, 7);

  return (
    <>
      <PageHeader {...TITLE} actions={<AddSourceButton project={project} defaultOpen={openAdd} />} />
      <Section
        eyebrow="Decision timeline"
        title="Calendar"
        count={entries.length}
        description="결정이나 후보가 나온 회의 기록만 날짜별로 보여 줍니다. 등록된 모든 원문은 아래 All sources에 있습니다."
        className="mb-16"
      >
        <MeetingCalendar entries={entries} initialMonth={initialMonth} today={today} />
      </Section>

      <h2 className="mb-4 flex items-baseline gap-2.5 text-xl font-semibold tracking-tight">
        All sources <span className="text-base font-medium tabular-nums text-faint">{sorted.length}</span>
        <span className="text-xs font-normal text-muted">프로젝트에 등록된 모든 원문 · 최신순</span>
      </h2>
      {sorted.length === 0 && (
        <EmptyState>이 프로젝트에 등록된 Source가 없습니다. Add source로 회의록 · 문서 · 오디오를 추가하세요.</EmptyState>
      )}
      <div className={sorted.length === 0 ? "hidden" : "divide-y divide-line border-y border-ink"}>
        {sorted.map((s) => {
          const linked = decisions.filter((d) => d.evidence.some((e) => e.source_id === s.id));
          const dated = datedOf(s);
          return (
            <details key={s.id} id={s.id} className="group scroll-mt-32 target:bg-soft">
              <summary className="flex cursor-pointer list-none items-center gap-3 px-1 py-5 transition-colors hover:bg-surface sm:px-3">
                <SourceTypeBadge type={s.type} />
                {s.audio && (
                  <span
                    className={
                      s.audio.transcription_status === "ready"
                        ? "text-[10px] font-semibold uppercase tracking-[0.08em] text-muted"
                        : s.audio.transcription_status === "failed"
                          ? "text-[10px] font-semibold uppercase tracking-[0.08em] text-danger"
                          : "animate-pulse text-[10px] font-semibold uppercase tracking-[0.08em] text-ink"
                    }
                  >
                    {{ uploaded: "Transcribing…", transcribing: "Transcribing…", ready: "Ready", failed: "Failed" }[
                      s.audio.transcription_status
                    ]}
                  </span>
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-medium text-ink">{s.title}</span>
                  <span className="block truncate text-xs text-muted">{s.summary}</span>
                </span>
                {s.human_authored && <HumanAuthoredTag />}
                <span className="hidden text-right text-xs tabular-nums text-faint sm:block">
                  {dated?.date ?? "날짜 없음"}
                  {dated && DATE_BASIS[dated.basis] && <span className="block text-[10px]">{DATE_BASIS[dated.basis]}</span>}
                </span>
                {/* 연결 상태 (Calendar 표시 기준과 같은 sourceLinks) */}
                <span className="shrink-0 text-right text-[11px] leading-snug tabular-nums text-muted">
                  {decisionCount.get(s.id) || candidateCount.get(s.id) ? (
                    <>
                      <span className="block">Decisions {decisionCount.get(s.id) ?? 0}</span>
                      <span className="block">Candidates {candidateCount.get(s.id) ?? 0}</span>
                    </>
                  ) : (
                    <span className="text-faint">Unlinked</span>
                  )}
                </span>
                <ChevronDown className="size-4 text-faint transition-transform group-open:rotate-180" />
              </summary>
              <div className="space-y-4 px-1 pb-6 sm:px-3">
                {s.audio && <AudioStatus sourceId={s.id} projectId={project.id} audio={s.audio} />}
                {/* 오디오면 timestamp가 붙은 raw transcript (추출 전에 STT 결과 자체를 확인) */}
                {s.content ? (
                  <pre className="overflow-x-auto rounded-xl border border-line bg-surface p-5 font-sans text-sm leading-relaxed whitespace-pre-wrap text-ink-2">
                    {s.content}
                  </pre>
                ) : (
                  <p className="rounded-lg bg-soft p-4 text-sm text-faint">아직 transcript가 없습니다.</p>
                )}
                {linked.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {linked.map((d) => (
                      <Link
                        key={d.id}
                        href={`/decisions/${d.id}`}
                        className="rounded-md border border-line-strong px-2 py-1 font-mono text-xs text-ink transition-colors hover:border-ink"
                      >
                        {d.id}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </details>
          );
        })}
      </div>
    </>
  );
}
