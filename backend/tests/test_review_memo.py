"""Review Memo (services/review_memo + API). 임시 DB + 가짜 Hermes(tests/fake_hermes.py)만 쓴다 (LLM/네트워크 없음).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_review_memo -v
"""

import asyncio
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from schemas import Decision, Evidence, Metric, MetricTrigger  # noqa: E402
from services import metrics as metric_service  # noqa: E402
from services import review_memo, review_monitor  # noqa: E402

PID = 1
BASELINE = "DEC-2026-0314-DB"  # 처음 확인할 때부터 due (이벤트 없음)
TRANSITION = "DEC-2026-0321-CACHE"  # ACTIVE → REVIEW_DUE 이벤트
ACTIVE = "DEC-ADR-001-STORAGE"
EXACT_DEC = "DEC-TEST-EXACT"  # v3: 원문 그대로 인용 확인용 (실제 DATABASE와 같은 evidence 형태)
PROTECTED_TABLES = ("decisions", "decision_sources", "decision_review_state", "review_events", "candidates", "sources")


def _obs(key, value):
    return {key: {"value": value, "unit": "users", "source": "sqlite:users", "observed_at": "2026-11-06T00:00:00+00:00"}}


def _protected_dump() -> dict:
    with sqlite3.connect(db.DB_PATH) as conn:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in PROTECTED_TABLES}


class ReviewMemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls._orig = (db.DB_PATH, review_memo.HERMES_BIN, review_memo.HERMES_HOME, review_memo.ENABLED,
                     review_memo.HERMES_REVIEW_MEMO_TIMEOUT_SECONDS)
        db.DB_PATH = tmp / "test.db"
        db.READ_ONLY = False
        db.init_db()
        db.record_review_check(PID, BASELINE, "REVIEW_DUE", "REVIEW_DUE", _obs("user_count", 1400), None, None)
        db.record_review_check(PID, TRANSITION, "ACTIVE", "ACTIVE", _obs("search_api_p95_ms", 700), None, None)
        cls.event_id = db.record_review_check(
            PID, TRANSITION, "REVIEW_DUE", "REVIEW_DUE", _obs("search_api_p95_ms", 850), None,
            {"previous_status": "ACTIVE", "new_status": "REVIEW_DUE", "trigger": {"type": "metric"}},
        )
        db.record_review_check(PID, ACTIVE, "ACTIVE", "ACTIVE", None, None, None)
        # v3 exact quote fixture: 여러 줄 evidence 한 행 (실제 DATABASE와 같은 형태), assumptions 없음
        with db.connect() as conn:
            db.insert_decision(conn, Decision(
                id=EXACT_DEC, project_id=PID, topic="database", title="SQLite 유지", decision="SQLite 그대로 간다.",
                rationale="나중에 옮길 수 있다", assumptions=[], review_trigger=MetricTrigger(
                    raw_metric="실제 사용자 수", metric_key="user_count", operator=">", threshold=1000,
                    raw_condition="1,000명을 넘으면 프로그레스 SQL로 전환을 다시 본다"),
                status="active", origin="review_approved", human_verified=True,
                evidence=[Evidence(source_id="meeting_01_database", quote=(
                    "[00:00:57] 그러면 일단 MVP에서는 SQLite 그대로 가죠.\n"
                    "[00:01:00] 지금 굳이 프로그레스 SQL로 옮기지는 않는 걸로요.\n"
                    "[00:01:22] 네, 그러면 실제 사용자 수가 1,000명을 넘으면 프로그레스 SQL로 전환을 다시 보는 걸로 하죠."))],
                created_at="2026-11-06", updated_at="2026-11-06", history=[],
            ))
        db.record_review_check(PID, EXACT_DEC, "REVIEW_DUE", "REVIEW_DUE", _obs("user_count", 1400), None, None)

        # 가짜 Hermes 실행 파일 (shell 없이 argv로 실행되는지까지 확인)
        cls.hermes_home = tmp / "hermes_home"
        cls.hermes_home.mkdir()
        cls.argv_log = tmp / "argv.json"
        fake = tmp / "hermes"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).parent / "fake_hermes.py"}" "$@"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        review_memo.HERMES_BIN = str(fake)
        review_memo.HERMES_HOME = cls.hermes_home
        review_memo.ENABLED = True
        review_memo.HERMES_REVIEW_MEMO_TIMEOUT_SECONDS = 20

    @classmethod
    def tearDownClass(cls):
        (db.DB_PATH, review_memo.HERMES_BIN, review_memo.HERMES_HOME, review_memo.ENABLED,
         review_memo.HERMES_REVIEW_MEMO_TIMEOUT_SECONDS) = cls._orig
        cls._tmp.cleanup()

    def setUp(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM decision_review_memos")
        os.environ.pop("FAKE_DETECTED_BY", None)
        self._mode("ok")
        os.environ["FAKE_HERMES_ARGV_LOG"] = str(self.argv_log)

    def _mode(self, mode: str):
        os.environ["FAKE_HERMES_MODE"] = mode

    def _generate(self, decision_id=BASELINE, regenerate=False):
        memo, created = review_memo.start(decision_id, regenerate=regenerate)
        self.assertTrue(created)
        self.assertEqual(memo.status, "generating")
        return asyncio.run(review_memo.run(memo.id))

    # ---------------------------------------------------------------- 생성 / 거부

    def test_generates_memo_for_baseline_due(self):
        before = _protected_dump()
        memo = self._generate()
        self.assertEqual(memo.status, "completed", memo.error)
        self.assertTrue(memo.content.startswith("# Decision Review Memo"))  # 앞 군더더기 제거
        self.assertIn(review_memo.DETECTION_TEXT_V4["review_state_baseline"], memo.content)
        self.assertEqual(memo.detected_by, "review_state_baseline")
        self.assertIsNone(memo.review_event_id)
        self.assertEqual(memo.prompt_version, "v4")
        self.assertEqual((memo.model, memo.provider), ("z-ai/glm-5.3", "openrouter"))
        self.assertEqual(memo.observed_snapshot["observed"]["value"], 1400)
        self.assertEqual(memo.observed_snapshot["trigger_text"], "user_count > 1000")
        self.assertTrue(memo.source_snapshot["evidence"])
        self.assertTrue(memo.tool_audit["ok"])
        self.assertEqual(memo.tool_audit["offered"]["top_level"], ["tool_search", "tool_describe", "tool_call"])
        self.assertEqual(memo.tool_audit["offered"]["deferred_counts"], {"decision_debt": 3, "gbrain": 2})
        self.assertEqual(memo.tool_audit["executed_counts"]["mcp__decision_debt__list_review_due"], 1)
        self.assertEqual(memo.tool_audit["requested_counts"]["mcp__decision_debt__list_review_due"], 1)
        self.assertEqual(memo.tool_audit["tools_used"][:3], ["tool_describe", "mcp__decision_debt__list_review_due",
                                                             "mcp__decision_debt__get_decision_detail"])
        self.assertEqual(_protected_dump(), before)  # Decision / review 상태 / 이벤트 / 후보 변경 없음

    def test_transition_due_memo(self):
        os.environ["FAKE_DETECTED_BY"] = "review_event"
        memo = self._generate(TRANSITION)
        self.assertEqual(memo.status, "completed", memo.error)
        self.assertEqual((memo.detected_by, memo.review_event_id), ("review_event", self.event_id))
        self.assertIn(review_memo.DETECTION_TEXT_V4["review_event"], memo.content)
        self.assertNotIn(review_memo.DETECTION_TEXT_V4["review_state_baseline"], memo.content)

    def test_rejects_active_decision(self):
        with self.assertRaises(review_memo.ReviewMemoError) as ctx:
            review_memo.start(ACTIVE)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_review_due"))
        self.assertEqual(db.list_review_memos(ACTIVE), [])

    def test_missing_decision(self):
        with self.assertRaises(review_memo.ReviewMemoError) as ctx:
            review_memo.start("DEC-NOPE")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (404, "decision_not_found"))

    def test_disabled(self):
        review_memo.ENABLED = False
        try:
            with self.assertRaises(review_memo.ReviewMemoError) as ctx:
                review_memo.start(BASELINE)
            self.assertEqual(ctx.exception.status, 503)
        finally:
            review_memo.ENABLED = True

    # ---------------------------------------------------------------- baseline / transition 표현 검증

    def _pinned(self, version):
        """특정 prompt 버전으로 memo를 만든다 (이전 버전 경로도 계속 동작하는지)"""
        orig = review_memo.PROMPT_VERSION
        review_memo.PROMPT_VERSION = version
        self.addCleanup(setattr, review_memo, "PROMPT_VERSION", orig)

    def test_wrong_detection_sentence_fails(self):
        self._pinned("v3")  # v1~v3: 감지 방식 문장을 모델이 썼다 (v4는 backend가 렌더링)
        self._mode("wrong_detection")  # baseline인데 '전환됨'으로 씀
        memo = self._generate()
        self.assertEqual(memo.status, "failed")
        self.assertIn("감지 방식", memo.error)
        self.assertIsNone(memo.content)

    def test_validate_memo_sections(self):
        content, problems = review_memo.validate_memo("# Decision Review Memo\n\n## 당시 결정\nx", "review_state_baseline")
        self.assertTrue(any("저장된 Rationale" in p for p in problems))
        self.assertTrue(any("최종 상태" in p for p in problems))
        self.assertEqual(review_memo.validate_memo("NOT_REVIEW_DUE", "review_event")[0], None)

    # ---------------------------------------------------------------- v2 사실성 (기록에 없는 전제/변화)

    def test_prompt_v2(self):
        prompt = review_memo.build_prompt(BASELINE, "v2")
        self.assertIn("evidence 해석 규칙", prompt)
        self.assertIn("그 이전 값이나 threshold 통과 시점은 기록되어 있지 않다", prompt)
        self.assertIn("사용자가 적었다", prompt)
        self.assertIn("v1", review_memo.PROMPTS)  # v1 memo가 어떤 prompt였는지 남겨 둔다
        self.assertNotIn("evidence 해석 규칙", review_memo.PROMPTS["v1"])

    def test_invented_premise_fails(self):
        self._mode("invented_premise")
        os.environ["FAKE_DETECTED_BY"] = "review_event"
        memo = self._generate(TRANSITION)  # assumptions = []
        self.assertEqual(memo.source_snapshot["assumptions"], [])
        self.assertEqual(memo.status, "failed")
        self.assertIn("기록에 없는 전제를 단정함", memo.error)
        self.assertIsNone(memo.content)

    def _memo(self, **sections) -> str:
        body = {s: "- (내용)" for s in review_memo.REQUIRED_SECTIONS}
        body["## 당시 전제"] = review_memo.NO_ASSUMPTIONS_TEXT
        body.update(sections)
        return "\n\n".join(f"{k}\n{v}" for k, v in body.items())

    def test_fact_guard_rejects_v1_sentence(self):
        memo = self._memo(**{"### 달라진 점": '- 당시 전제였던 "사용자가 아직 적은 MVP 단계"는 더 이상 성립하지 않는다.'})
        problems = review_memo.check_facts(memo, "review_state_baseline", [])
        self.assertEqual(len(problems), 1)  # 달라진 점 ⊂ AI 검토 의견이어도 한 번만
        for text in ("- 사용자가 적다는 전제가 깨졌다.", "- 당시 전제는 더 이상 유효하지 않다."):
            self.assertTrue(review_memo.check_facts(self._memo(**{"### 사람이 추가로 확인할 사항": text}), "review_event", []))

    def test_fact_guard_allows_honest_wording(self):
        memo = self._memo(**{
            "## 당시 전제": "명시적으로 기록된 전제 없음 (assumptions 비어 있음)",
            "### 달라진 점": "- 첫 모니터링 시점부터 user_count=1400으로 threshold를 초과한 상태가 확인되었다. "
                         "그 이전 값이나 threshold 통과 시점은 기록되어 있지 않다.",
            "### 아직 확인되지 않은 정보": "- 명시된 전제가 없어 전제 유효성은 판단할 데이터가 없음\n"
                                    '- evidence 발언: "사용자 늘어나면 옮겨야 할 수도"',
        })
        self.assertEqual(review_memo.check_facts(memo, "review_state_baseline", []), [])

    def test_fact_guard_requires_no_assumptions_text(self):
        memo = self._memo(**{"## 당시 전제": "- 사용자가 적었다"})
        self.assertTrue(any("명시적으로 기록된 전제 없음" in p for p in review_memo.check_facts(memo, "review_event", [])))
        # 기록된 전제가 있으면 전제 문구 검사는 하지 않는다 (사람이 기록한 전제를 인용·평가하는 것은 정상)
        recorded = self._memo(**{"## 당시 전제": "- 초기 가입자 수가 많지 않다", "### 달라진 점": "- 그 전제가 더 이상 맞지 않을 수 있다"})
        self.assertEqual(review_memo.check_facts(recorded, "review_event", ["초기 가입자 수가 많지 않다"]), [])

    def test_fact_guard_baseline_increase(self):
        memo = self._memo(**{"### 달라진 점": "- 사용자가 1000 이하에서 1400으로 증가했다."})
        self.assertTrue(review_memo.check_facts(memo, "review_state_baseline", []))
        self.assertEqual(review_memo.check_facts(memo, "review_event", []), [])  # 전환 이벤트면 증가는 기록된 사실
        for honest in (  # 기록이 없음을 밝히는 문장은 통과 (실제 v4 memo #4에서 오탐으로 실패했던 형태 포함)
            "- 기록으로 확인 가능한 변화 없음. 감지 방식이 baseline이므로 threshold 통과 전의 user_count가 언제 증가했는지는 기록에 없다.",
            "- 이전 값이 없어 사용자가 증가했다고 단정할 수 없다.",
            "- user_count가 증가한 시점은 알 수 없다.",
        ):
            self.assertEqual(review_memo.check_facts(self._memo(**{"### 달라진 점": honest}), "review_state_baseline", []), [], honest)
        for asserted in ("- 최근 사용자가 늘었다.", "- user_count가 1400으로 늘어났다."):
            self.assertTrue(review_memo.check_facts(self._memo(**{"### 달라진 점": asserted}), "review_state_baseline", []), asserted)

    # ---------------------------------------------------------------- v3 Evidence ID (원문을 LLM이 다시 쓰지 않음)

    def test_prompt_versions_preserved(self):
        self.assertEqual(review_memo.PROMPT_VERSION, "v4")
        self.assertEqual(sorted(review_memo.PROMPTS), ["v1", "v2", "v3", "v4"])
        self.assertIn("Decision Debt의 source/evidence 내용만 쓴다", review_memo.PROMPTS["v2"])  # v2 그대로
        items = [{"id": "E1", "source_id": "s", "source_title": "T", "text": "[00:01:00] {중괄호} 원문"}]
        prompt = review_memo.build_prompt(BASELINE, "v3", items)
        self.assertIn("- E1 [T] [00:01:00] {중괄호} 원문", prompt)  # format()과 무관하게 원문 그대로
        self.assertIn("Evidence 문장을 다시 작성하거나 요약하거나 교정하지 마라", prompt)
        self.assertIn("evidence 해석 규칙", prompt)  # v2 사실성 규칙 유지

    def test_evidence_items_are_exact_substrings(self):
        items = review_memo.evidence_items(EXACT_DEC, [])
        stored = db.get_decision(EXACT_DEC).evidence[0].quote
        self.assertEqual([i["id"] for i in items], ["E1", "E2", "E3"])
        self.assertEqual(items[1]["text"], "[00:01:00] 지금 굳이 프로그레스 SQL로 옮기지는 않는 걸로요.")
        for item in items:
            self.assertIn(item["text"], stored)

    def test_v3_memo_quotes_snapshot_exactly(self):
        os.environ["FAKE_DETECTED_BY"] = "review_state_baseline"
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "completed", memo.error)
        stored_lines = [l.strip() for l in db.get_decision(EXACT_DEC).evidence[0].quote.splitlines() if l.strip()]
        rendered = review_memo._section(memo.content, "## 원래 근거")
        quoted = [l.split(" · ", 1)[1] for l in rendered.splitlines() if l.startswith("- E")]
        self.assertEqual(quoted, stored_lines[:2])  # LLM 출력이 아니라 snapshot 원문 (character-for-character)
        self.assertIn("[00:01:00] 지금 굳이 프로그레스 SQL로 옮기지는 않는 걸로요.", quoted)
        self.assertNotIn("프그레스", memo.content)
        self.assertIn("- 원문 (기록 그대로): 1,000명을 넘으면 프로그레스 SQL로 전환을 다시 본다", memo.content)
        self.assertEqual(memo.source_snapshot["evidence_items"][1]["text"], stored_lines[1])

    def test_unknown_evidence_id_fails(self):
        self._mode("bad_evidence_id")
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "failed")
        self.assertIn("없는 Evidence ID: E99", memo.error)

    def test_direct_quote_in_source_section_fails(self):
        self._mode("quoted_evidence")
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "failed")
        self.assertIn("Evidence ID가 아닌 텍스트", memo.error)
        self.assertIsNone(memo.content)

    def test_timestamp_quote_outside_source_section_fails(self):
        self._mode("timestamp_outside")
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "failed")
        self.assertIn("타임스탬프", memo.error)

    def test_duplicate_ids_deduplicated(self):
        self._mode("dup_ids")
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "completed", memo.error)
        rendered = review_memo._section(memo.content, "## 원래 근거")
        self.assertEqual([l.split(" · ")[0] for l in rendered.splitlines() if l.startswith("- E")], ["- E2", "- E1"])

    def test_render_evidence_unit(self):
        items = [{"id": "E1", "source_id": "s1", "source_title": "데이터베이스 · 검색 회의", "text": "[00:01:00] 프로그레스 SQL"}]
        memo = self._memo(**{"## 원래 근거": "- E1\n- E1.", "## 재검토 조건": "- user_count > 1000"})
        out, problems = review_memo.render_evidence(memo, items, "원문 조건")
        self.assertEqual(problems, [])
        self.assertIn("출처: 데이터베이스 · 검색 회의 (source_id: s1)\n- E1 · [00:01:00] 프로그레스 SQL", out)
        self.assertIn("- user_count > 1000\n- 원문 (기록 그대로): 원문 조건", out)
        self.assertTrue(review_memo.render_evidence(self._memo(**{"## 원래 근거": "- E1 (신수동)"}), items)[1])
        self.assertEqual(review_memo.render_evidence(self._memo(**{"## 원래 근거": "없음"}), [])[1], [])

    def test_v3_path_still_works(self):
        self._pinned("v3")
        memo = self._generate(EXACT_DEC)
        self.assertEqual((memo.status, memo.prompt_version), ("completed", "v3"), memo.error)
        self.assertIn("[00:01:00] 지금 굳이 프로그레스 SQL로 옮기지는 않는 걸로요.", memo.content)

    # ---------------------------------------------------------------- v4 Authoritative Facts (backend 렌더링)

    def _body(self, memo, heading):
        return review_memo._section(memo, heading).strip()

    def test_v4_authoritative_sections_exact(self):
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "completed", memo.error)
        d = db.get_decision(EXACT_DEC)
        c = memo.content
        self.assertEqual(self._body(c, "## 당시 결정"), d.decision)  # character-for-character
        self.assertEqual(self._body(c, "## 저장된 Rationale"), d.rationale)
        self.assertEqual(self._body(c, "## 당시 전제"), review_memo.NO_ASSUMPTIONS_TEXT)
        self.assertIn(f"- 원문 (기록 그대로): {d.review_trigger.raw_condition}\n", c)
        self.assertIn("- 조건: user_count > 1000\n", c)
        o = memo.observed_snapshot["observed"]  # 생성 시점 snapshot 그대로 (memo 생성 후 metric이 바뀌어도 고정)
        self.assertEqual(self._body(c, "## 현재 관측값").splitlines()[:3],
                         [f"- {o['metric']} = {o['value']} {o['unit']}", f"- source: {o['source']}", f"- observed_at: {o['observed_at']}"])
        self.assertEqual(o["value"], 1400)
        self.assertTrue(self._body(c, "## 감지 방식").startswith(review_memo.DETECTION_TEXT_V4["review_state_baseline"]))
        quoted = [l.split(" · ", 1)[1] for l in c.splitlines() if l.startswith("- E")]
        stored = [l.strip() for l in d.evidence[0].quote.splitlines() if l.strip()]
        self.assertTrue(quoted and all(q in stored for q in quoted))
        self.assertTrue(c.rstrip().endswith("## 최종 상태\n" + review_memo.FINAL_STATUS_LINE))
        self.assertIn(review_memo.FACTS_NOTE, c)
        self.assertIn(review_memo.ANALYSIS_NOTE, c)

    def test_v4_tampered_facts_from_model_are_discarded(self):
        self._mode("tamper_facts")  # 모델이 당시 결정/rationale/관측값/최종 상태를 바꿔 써도
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "completed", memo.error)
        for bad in ("프그레스", "9999", "사용자가 적었기", "ACTIVE\n"):
            self.assertNotIn(bad, memo.content)
        self.assertEqual(self._body(memo.content, "## 당시 결정"), db.get_decision(EXACT_DEC).decision)
        self.assertEqual(memo.content.count("## 당시 결정"), 1)

    def test_v4_baseline_increase_guard(self):
        self._mode("baseline_increase")
        memo = self._generate(EXACT_DEC)
        self.assertEqual(memo.status, "failed")
        self.assertIn("baseline인데 증가를 단정함", memo.error)

    def test_v4_missing_ai_section_fails(self):
        memo, problems = review_memo.assemble_v4("## 원래 근거\n- E1\n\n## AI 검토 의견\nx", {}, {"evidence_items": []})
        self.assertIsNone(memo)
        self.assertTrue(any("### 달라진 점" in p for p in problems))

    def test_v4_render_facts_variants(self):
        obs = {"trigger": {"type": "metric", "metric_key": "user_count", "operator": ">", "threshold": 1000},
               "observed": None, "detected_by": "review_event", "review_status": "REVIEW_DUE",
               "event": {"event_id": 7, "detected_at": "2026-11-01T00:00:00"}}
        src = {"decision_record": {"decision_id": "D", "decision": "  원문 그대로  ", "rationale": "",
                                   "assumptions": ["전제 A", "전제 B"]}}
        facts = review_memo.render_facts(obs, src)
        self.assertIn("## 당시 결정\n  원문 그대로  \n", facts)  # 공백도 바꾸지 않는다
        self.assertIn("## 저장된 Rationale\n" + review_memo.NO_RATIONALE_TEXT, facts)
        self.assertIn("## 당시 전제\n- 전제 A\n- 전제 B", facts)
        self.assertIn("관측값 기록 없음", facts)
        self.assertIn(review_memo.DETECTION_TEXT_V4["review_event"] + "\n\nREVIEW_DUE 전환 이벤트 #7", facts)
        self.assertNotIn("원문 (기록 그대로)", facts)  # raw condition이 없으면 만들지 않는다

    def test_v4_prompt_shows_facts_and_forbids_copy(self):
        prompt = review_memo.build_prompt(EXACT_DEC, "v4", [{"id": "E1", "source_id": "s", "text": "[00:00:01] x"}],
                                          "## 당시 결정\n{원문}")
        self.assertIn("## 당시 결정\n{원문}", prompt)
        self.assertIn("- E1 [s] [00:00:01] x", prompt)
        self.assertIn("다시 옮겨 쓰거나 요약·교정하지 마라", prompt)
        self.assertNotIn("<<", prompt)

    # ---------------------------------------------------------------- 실패 처리

    def test_hermes_failure_changes_nothing_else(self):
        ok = self._generate()
        before = _protected_dump()
        self._mode("fail")
        failed = self._generate(regenerate=True)
        self.assertEqual(failed.status, "failed")
        self.assertIn("Hermes 실행 실패", failed.error)
        self.assertNotIn("Traceback", failed.error)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", failed.error)  # key 형태 문자열은 가린다
        self.assertIsNone(failed.content)
        self.assertEqual(_protected_dump(), before)
        self.assertEqual(db.get_review_memo(ok.id).status, "completed")  # 기존 memo는 그대로
        self.assertEqual(db.get_review_memo(ok.id).content, ok.content)

    def test_timeout(self):
        self._mode("slow")
        review_memo.HERMES_REVIEW_MEMO_TIMEOUT_SECONDS = 1
        try:
            memo = self._generate()
        finally:
            review_memo.HERMES_REVIEW_MEMO_TIMEOUT_SECONDS = 20
        self.assertEqual(memo.status, "failed")
        self.assertIn("시간 초과", memo.error)

    def test_missing_hermes_binary(self):
        bin_ = review_memo.HERMES_BIN
        review_memo.HERMES_BIN = str(Path(self._tmp.name) / "no-such-hermes")
        try:
            memo = self._generate()
        finally:
            review_memo.HERMES_BIN = bin_
        self.assertEqual(memo.status, "failed")
        self.assertIn("HERMES_BIN", memo.error)

    def test_not_due_answer_fails(self):
        self._mode("not_due")
        self.assertEqual(self._generate().status, "failed")

    # ---------------------------------------------------------------- tool 제한 / 경로 검증

    def test_argv_uses_only_two_mcp_toolsets(self):
        self._generate()
        argv = json.loads(self.argv_log.read_text())
        self.assertEqual(argv[argv.index("-t") + 1], "decision-debt,gbrain")
        self.assertIn("--ignore-rules", argv)
        self.assertIn("--usage-file", argv)
        self.assertIn(BASELINE, argv[argv.index("-z") + 1])
        for forbidden in ("terminal", "file", "browser", "memory", "all", "--yolo"):
            self.assertNotIn(forbidden, argv[argv.index("-t") + 1].split(","))
            self.assertNotIn(forbidden, argv)

    def test_terminal_tool_use_fails(self):
        self._mode("terminal")
        memo = self._generate()
        self.assertEqual(memo.status, "failed")
        self.assertEqual(memo.tool_audit["disallowed"], ["terminal"])
        self.assertIsNone(memo.content)

    def test_terminal_offered_fails_even_if_unused(self):
        self._mode("exposed_terminal")  # 호출은 안 했지만 terminal이 제공됨 → toolset 제한 실패
        memo = self._generate()
        self.assertEqual(memo.status, "failed")
        self.assertEqual(memo.tool_audit["disallowed"], ["offered:terminal"])

    def test_missing_gbrain_search_fails(self):
        self._mode("no_gbrain")
        memo = self._generate()
        self.assertEqual(memo.status, "failed")
        self.assertIn("mcp__gbrain__* (search/query)", memo.tool_audit["missing_required"])

    def test_backend_env_not_passed(self):
        from config import BACKEND_ENV_KEYS

        os.environ.setdefault("OPENROUTER_API_KEY", "x")
        BACKEND_ENV_KEYS.add("OPENROUTER_API_KEY")
        env = review_memo._hermes_env()
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertEqual(env["HERMES_HOME"], str(self.hermes_home))

    # ---------------------------------------------------------------- 중복 / 재생성

    def test_duplicate_generating_blocked(self):
        memo, created = review_memo.start(BASELINE)
        self.assertTrue(created)
        with self.assertRaises(review_memo.ReviewMemoError) as ctx:
            review_memo.start(BASELINE, regenerate=True)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "memo_generating"))
        with self.assertRaises(db.MemoInProgress):  # DB 수준에서도 결정당 generating 1개
            db.create_review_memo(PID, BASELINE, None, None, "review_state_baseline", "v1", {}, {})
        asyncio.run(review_memo.run(memo.id))

    def test_existing_memo_returned_and_regenerate_keeps_old(self):
        first = self._generate()
        again, created = review_memo.start(BASELINE)
        self.assertFalse(created)
        self.assertEqual(again.id, first.id)
        second = self._generate(regenerate=True)
        self.assertNotEqual(second.id, first.id)
        self.assertEqual([m.id for m in db.list_review_memos(BASELINE)], [second.id, first.id])
        self.assertEqual(db.get_review_memo(first.id).content, first.content)

    def test_interrupted_generating_marked_failed(self):
        memo, _ = review_memo.start(BASELINE)
        self.assertEqual(db.fail_interrupted_review_memos(), 1)
        self.assertEqual(db.get_review_memo(memo.id).status, "failed")

    # ---------------------------------------------------------------- API

    def test_api(self):
        from fastapi.testclient import TestClient

        import app

        client = TestClient(app.app)  # with 없이 → lifespan(review monitor) 실행 안 함
        r = client.post(f"/api/decisions/{BASELINE}/review-memo")
        self.assertEqual(r.status_code, 202)
        memo_id = r.json()["id"]
        self.assertEqual(client.get(f"/api/review-memos/{memo_id}").json()["status"], "completed")  # background 완료
        self.assertEqual(client.post(f"/api/decisions/{BASELINE}/review-memo").status_code, 200)  # 기존 memo 반환
        self.assertEqual(client.post(f"/api/decisions/{BASELINE}/review-memo", json={"regenerate": True}).status_code, 202)
        self.assertEqual(len(client.get(f"/api/decisions/{BASELINE}/review-memos").json()), 2)
        r = client.post(f"/api/decisions/{ACTIVE}/review-memo")
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "not_review_due"))
        self.assertEqual(client.post("/api/decisions/DEC-NOPE/review-memo").status_code, 404)
        self.assertEqual(client.get("/api/decisions/DEC-NOPE/review-memos").status_code, 404)
        self.assertEqual(client.get("/api/review-memos/99999").status_code, 404)

    # ---------------------------------------------------------------- review monitor regression

    def test_review_monitor_unaffected_by_memos(self):
        self._generate()
        memos_before = [m.model_dump() for m in db.list_review_memos(BASELINE)]
        project = db.get_project(PID)

        async def fake_check_metrics(_project, names):
            return {n: Metric(metric_name=n, value=1400 if n == "user_count" else 850, unit="u", source="t", observed_at="now")
                    for n in names}, {}

        from services import telegram_notifier

        orig, orig_enabled = metric_service.check_metrics, telegram_notifier.ENABLED
        metric_service.check_metrics = fake_check_metrics
        telegram_notifier.ENABLED = False  # 테스트에서 실제 Telegram 발송 금지
        try:
            created = asyncio.run(review_monitor.check_project(project))
        finally:
            metric_service.check_metrics = orig
            telegram_notifier.ENABLED = orig_enabled
        states = db.get_review_states(PID)
        self.assertEqual(states[BASELINE]["last_review_status"], "REVIEW_DUE")
        self.assertIsInstance(created, list)
        self.assertEqual([m.model_dump() for m in db.list_review_memos(BASELINE)], memos_before)


if __name__ == "__main__":
    unittest.main()
