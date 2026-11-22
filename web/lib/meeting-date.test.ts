// Calendar 표시 정책 / 날짜 규칙 테스트. 실행: npm test (Node 내장 test runner, TypeScript 그대로 실행)
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildMeetingEntries, shortTitle, sourceLinks, writtenDate } from "./meeting-date.ts";
import type { Candidate, Decision, Source } from "./types.ts";

function source(id: string, content: string, type: Source["type"] = "meeting", date: string | null = null): Source {
  return { id, project_id: 1, title: `${id}.md`, type, date, summary: "", content, human_authored: true };
}
function candidate(source_id: string, status: Candidate["status"], resolved_at: string | null = null) {
  return { source_id, status, resolved_at };
}
function decision(...sourceIds: string[]) {
  return { evidence: sourceIds.map((source_id) => ({ source_id, quote: "q" })) } as Pick<Decision, "evidence">;
}

const SOURCES: Source[] = [
  source("pending", "# 2026-10-29 데이터베이스 운영 회의\n본문"), // 1. pending Candidate만
  source("decision-only", "문서: 2026-03-14 인프라 회의 | 주제: 데이터베이스 | 결정: SQLite"), // 2. Decision evidence만 (seed)
  source("unlinked", "# 2026-11-02 검색 기능 아이디어 회의\n결정 없음"), // 3. Candidate 0 / Decision 0
  source("ignored", "# 2026-11-01 캐시 도입 논의\n본문"), // ignored Candidate만
  source("audio-approved", "[00:00:00] 오늘은 배포 얘기", "audio_transcript", "2026-11-06"), // 7. 오디오: 적힌 날짜 없음 → Approve 날짜
  source("audio-unlinked", "[00:00:00] 테스트", "audio_transcript", "2026-11-04"), // 7. 오디오: 연결 없음
  source("adr", "# ADR 001\n2026-01-01", "adr"), // 회의록이 아닌 문서
];
const CANDIDATES = [
  candidate("pending", "pending"),
  candidate("ignored", "ignored", "2026-11-01T10:00:00"),
  candidate("audio-approved", "approved", "2026-11-07T09:00:00"),
  candidate("audio-approved", "approved", "2026-11-06T16:34:28"),
];
const DECISIONS = [decision("decision-only"), decision("audio-approved"), decision("adr")];

const links = sourceLinks(CANDIDATES, DECISIONS);
const entries = buildMeetingEntries(SOURCES, links);
const ids = new Set(entries.map((e) => e.id));

test("1. Candidate가 연결된 Source → Calendar 표시", () => {
  assert.ok(ids.has("pending"));
});

test("2. Decision만 연결된 Source → Calendar 표시", () => {
  assert.ok(ids.has("decision-only"));
});

test("3. Candidate 0 / Decision 0, ignored Candidate만 있는 Source → Calendar 미표시", () => {
  assert.ok(!ids.has("unlinked"));
  assert.ok(!ids.has("ignored"));
});

test("4~5. All sources는 필터하지 않고, Calendar count는 표시 대상 수", () => {
  // All sources는 sources 배열 그대로 (페이지는 필터 없이 렌더) — 미표시 Source도 원본 목록에 남는다
  assert.equal(SOURCES.length, 7);
  assert.ok(SOURCES.some((s) => s.id === "unlinked"));
  assert.deepEqual([...ids].sort(), ["audio-approved", "decision-only", "pending"]);
  assert.equal(entries.length, 3);
});

test("6. 날짜 규칙 유지: 적힌 날짜 우선, 없으면 처음 Approve한 날짜", () => {
  const byId = new Map(entries.map((e) => [e.id, e]));
  assert.deepEqual([byId.get("pending")?.date, byId.get("pending")?.basis], ["2026-10-29", "written"]);
  assert.deepEqual([byId.get("decision-only")?.date, byId.get("decision-only")?.title], ["2026-03-14", "인프라 회의"]);
  // 오디오 Source.date(업로드 날짜 2026-11-06)가 아니라 Approve 날짜 중 가장 이른 것
  assert.deepEqual([byId.get("audio-approved")?.date, byId.get("audio-approved")?.basis], ["2026-11-06", "approved"]);
});

test("7. audio / text 모두 같은 연결 규칙", () => {
  assert.ok(ids.has("audio-approved")); // 연결된 오디오 → 표시
  assert.ok(!ids.has("audio-unlinked")); // 연결 없는 오디오 → 미표시
  assert.ok(!ids.has("adr")); // 회의록이 아닌 문서는 연결돼 있어도 Calendar 대상 아님 (All sources에만)
});

test("연결 정보: decision 수 / approve 시각", () => {
  assert.equal(links.decisionCount.get("audio-approved"), 1);
  assert.equal(links.approvedAt.get("audio-approved"), "2026-11-06T16:34:28");
  assert.ok(!links.approvedAt.has("ignored"));
});

test("날짜 / 제목 파싱", () => {
  assert.equal(writtenDate("2026년 10월 9일 회의")?.date, "2026-10-09");
  assert.equal(writtenDate("본문1\n본문2\n본문3\n본문4\n본문5\n2026-01-01 마감"), null); // 첫 5줄 밖의 날짜는 회의 날짜 아님
  assert.equal(writtenDate("# 2026-02-30 잘못된 날짜"), null);
  assert.equal(shortTitle("# 2026-10-29 데이터베이스 운영 회의", "2026-10-29"), "데이터베이스 운영 회의");
});
