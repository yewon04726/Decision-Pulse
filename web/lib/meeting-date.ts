// 회의록 캘린더 (화면 adapter. backend 데이터는 바꾸지 않는다)
// 표시 대상: 결정 활동과 연결된 회의록만 — sourceLinks() 참고. 전체 Source 목록(All sources)은 필터하지 않는다.
// 날짜:
//   1) 회의록 본문 첫머리(제목 줄)에 적힌 날짜   예) "# 2026-10-29 데이터베이스 운영 회의", "문서: 2026-03-14 인프라 회의"
//   2) 없으면 이 Source에서 나온 Candidate를 처음 approve한 날짜
//   3) 둘 다 없으면 캘린더에 올리지 않는다 (하단 전체 목록에만)
// Source.date는 쓰지 않는다: 오디오 Source는 업로드 날짜라 '회의록에 적힌 날짜'가 아니다.

import type { Candidate, Decision, Source, SourceType } from "./types";

export type MeetingDateBasis = "written" | "approved";

export interface MeetingEntry {
  id: string;
  /** 캘린더에 보이는 짧은 제목 (제목 줄에서 날짜를 뺀 부분, 없으면 Source 제목) */
  title: string;
  sourceTitle: string;
  type: SourceType;
  /** YYYY-MM-DD */
  date: string;
  basis: MeetingDateBasis;
  decisions: number;
}

/** 회의록으로 보는 Source 유형 (issue / ADR / PR은 회의가 아니다) */
export const MEETING_TYPES: SourceType[] = ["meeting", "audio_transcript"];

const HEADER_LINES = 5;
const PATTERNS = [
  /(\d{4})[-./](\d{1,2})[-./](\d{1,2})/, // 2026-10-29, 2026.10.29, 2026/10/29
  /(\d{4})_(\d{1,2})_(\d{1,2})/, // 2026_10_29
  /(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일/, // 2026년 10월 29일
];

function validDate(y: number, m: number, d: number): string | null {
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return null;
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** 본문 첫 몇 줄에서 처음 나오는 날짜와 그 줄 (본문 중간의 '~까지' 같은 날짜는 회의 날짜로 보지 않는다) */
export function writtenDate(content: string): { date: string; line: string; match: string } | null {
  const lines = content
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, HEADER_LINES);
  for (const line of lines) {
    for (const re of PATTERNS) {
      const m = re.exec(line);
      if (!m) continue;
      const date = validDate(Number(m[1]), Number(m[2]), Number(m[3]));
      if (date) return { date, line, match: m[0] };
    }
  }
  return null;
}

const MAX_TITLE = 40;

/**
 * 제목 줄 → 짧은 제목: "# 2026-10-29 데이터베이스 운영 회의" → "데이터베이스 운영 회의"
 * 한 줄에 " | "로 이어 쓴 문서("문서: 2026-03-14 인프라 회의 | 주제: …")는 날짜가 있는 구간만 쓴다.
 */
export function shortTitle(line: string, match: string): string {
  const segment = line.split(/\s+\|\s+/).find((part) => part.includes(match)) ?? line;
  const title = segment
    .replace(match, "")
    .replace(/^#+\s*/, "")
    .replace(/^(문서|회의|일시|날짜|date)\s*[:：]\s*/i, "")
    .replace(/^[\s:：\-–—·|,]+|[\s:：\-–—·|,]+$/g, "")
    .trim();
  return title.length > MAX_TITLE ? `${title.slice(0, MAX_TITLE - 1)}…` : title;
}

function stripExtension(title: string) {
  return title.replace(/\.(md|txt|markdown)$/i, "");
}

export interface SourceLinks {
  /** 결정 활동과 연결된 Source: 살아 있는 Candidate(pending / approved) 또는 Decision evidence(decision_sources) */
  linked: Set<string>;
  /** Source별 처음 Approve한 시각 (날짜 fallback) */
  approvedAt: Map<string, string>;
  /** Source를 evidence로 쓰는 Decision 수 */
  decisionCount: Map<string, number>;
  /** Source에서 나온 살아 있는 Candidate 수 (pending + approved, ignored 제외) */
  candidateCount: Map<string, number>;
}

/**
 * Source ↔ 결정 활동 연결 (DB 기준: candidates.source_id, decision_sources.source_id).
 * ignored Candidate만 남은 Source는 연결로 보지 않는다 (사람이 버린 후보). 삭제된 Candidate는 row가 없다.
 */
export function sourceLinks(candidates: Pick<Candidate, "source_id" | "status" | "resolved_at">[], decisions: Pick<Decision, "evidence">[]): SourceLinks {
  const linked = new Set<string>();
  const approvedAt = new Map<string, string>();
  const decisionCount = new Map<string, number>();
  const candidateCount = new Map<string, number>();
  for (const c of candidates) {
    if (c.status === "ignored") continue;
    linked.add(c.source_id);
    candidateCount.set(c.source_id, (candidateCount.get(c.source_id) ?? 0) + 1);
    if (c.status === "approved" && c.resolved_at) {
      const prev = approvedAt.get(c.source_id);
      if (!prev || c.resolved_at < prev) approvedAt.set(c.source_id, c.resolved_at);
    }
  }
  for (const d of decisions) {
    for (const id of new Set(d.evidence.map((e) => e.source_id))) {
      linked.add(id);
      decisionCount.set(id, (decisionCount.get(id) ?? 0) + 1);
    }
  }
  return { linked, approvedAt, decisionCount, candidateCount };
}

/** 캘린더 항목: 회의록 유형 + 결정 활동과 연결 + 날짜를 정할 수 있는 Source */
export function buildMeetingEntries(sources: Source[], links: SourceLinks): MeetingEntry[] {
  const { linked, approvedAt, decisionCount } = links;
  const out: MeetingEntry[] = [];
  for (const s of sources) {
    if (!MEETING_TYPES.includes(s.type) || !linked.has(s.id)) continue;
    const written = writtenDate(s.content ?? "");
    const approved = approvedAt.get(s.id);
    const date = written?.date ?? approved?.slice(0, 10) ?? null;
    if (!date) continue;
    const fromLine = written ? shortTitle(written.line, written.match) : "";
    out.push({
      id: s.id,
      title: fromLine || stripExtension(s.title),
      sourceTitle: s.title,
      type: s.type,
      date,
      basis: written ? "written" : "approved",
      decisions: decisionCount.get(s.id) ?? 0,
    });
  }
  return out.sort((a, b) => b.date.localeCompare(a.date) || a.title.localeCompare(b.title));
}
