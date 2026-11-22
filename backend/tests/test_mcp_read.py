"""Decision Debt read-only MCP (services/decision_read + mcp_server) 테스트. 임시 DB만 쓴다 (LLM/metric 호출 없음).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_mcp_read -v
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from schemas import Decision, Evidence, MetricTrigger  # noqa: E402
from services import decision_read  # noqa: E402

PID = 1
OBS = lambda v: {"user_count": {"value": v, "unit": "users", "source": "sqlite:users", "observed_at": "2026-11-06T00:00:00+00:00"}}  # noqa: E731


def _check(decision_id, current, observed, prev=None):
    """review monitor와 같은 방식으로 판정 기록 (상태가 바뀌면 이벤트)"""
    event = None
    if prev and prev != current:
        event = {"previous_status": prev, "new_status": current, "trigger": {"type": "metric"}}
    return db.record_review_check(PID, decision_id, current, current, observed, None, event)


def _dump() -> list[str]:
    with sqlite3.connect(db.DB_PATH) as conn:
        return list(conn.iterdump())


class ReadOnlyMcpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig_path = db.DB_PATH
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.READ_ONLY = False
        db.init_db()  # 최신 스키마 + seed

        # human_verified Decision (근거 2개: 짧은 인용 + 원문보다 긴 인용)
        with db.connect() as conn:
            db.insert_decision(conn, Decision(
                id="DEC-TEST-VERIFIED", project_id=PID, topic="database", title="SQLite 유지",
                decision="MVP에서는 SQLite를 유지한다.", rationale="운영 부담보다 기능 개발이 우선",
                assumptions=["초기 사용자는 적다"], review_trigger=MetricTrigger(
                    raw_metric="실제 사용자 수", metric_key="user_count", operator=">", threshold=1000,
                    raw_condition="실제 사용자 수가 1,000명을 넘으면 다시 본다"),
                status="active", origin="review_approved", human_verified=True,
                evidence=[Evidence(source_id="meeting_01_database", quote="SQLite를 유지한다"),
                          Evidence(source_id="meeting_01_database", quote="가" * 2000)],
                created_at="2026-11-06", updated_at="2026-11-06", history=[],
            ))
            # 대체된 결정에 예전 REVIEW_DUE state가 남은 경우
            conn.execute("UPDATE decisions SET status = 'superseded' WHERE id = 'DEC-ADR-002-API'")

        # 1) 승인 시점부터 이미 due: baseline REVIEW_DUE, 이벤트 없음
        _check("DEC-TEST-VERIFIED", "REVIEW_DUE", OBS(1400))
        # 2) ACTIVE 유지
        _check("DEC-2026-0321-CACHE", "ACTIVE", None)
        # 3) due → 해소: resolved 이벤트만 남고 현재 ACTIVE
        _check("DEC-ADR-001-STORAGE", "ACTIVE", None)
        _check("DEC-ADR-001-STORAGE", "REVIEW_DUE", OBS(5), prev="ACTIVE")
        _check("DEC-ADR-001-STORAGE", "ACTIVE", OBS(1), prev="REVIEW_DUE")
        # 4) due → 해소 → 다시 due (REVIEW_DUE 이벤트 2개, 첫 번째는 resolved) + 최신 이벤트 acknowledged
        _check("DEC-2026-0314-DB", "ACTIVE", OBS(900))
        cls.first_event = _check("DEC-2026-0314-DB", "REVIEW_DUE", OBS(1400), prev="ACTIVE")
        _check("DEC-2026-0314-DB", "ACTIVE", OBS(1000), prev="REVIEW_DUE")
        cls.latest_event = _check("DEC-2026-0314-DB", "REVIEW_DUE", OBS(1350), prev="ACTIVE")
        db.acknowledge_review_event(cls.latest_event)
        # 5) superseded 결정의 stale REVIEW_DUE
        _check("DEC-ADR-002-API", "REVIEW_DUE", OBS(1400))

        cls.before = _dump()
        db.READ_ONLY = True  # 이후 모든 조회는 MCP와 같은 read-only 연결

    @classmethod
    def tearDownClass(cls):
        db.READ_ONLY = False
        db.DB_PATH = cls._orig_path
        cls._tmp.cleanup()

    def _due(self, **kw) -> dict[str, dict]:
        result = decision_read.list_review_due(**kw)
        self.assertNotIn("error", result)
        self.assertEqual(result["count"], len(result["items"]))
        return {i["decision_id"]: i for i in result["items"]}

    # ---------------------------------------------------------------- list_review_due

    def test_includes_review_due_and_excludes_active(self):
        due = self._due()
        self.assertEqual(set(due), {"DEC-TEST-VERIFIED", "DEC-2026-0314-DB"})
        self.assertNotIn("DEC-2026-0321-CACHE", due)  # ACTIVE

    def test_excludes_resolved_event(self):
        self.assertNotIn("DEC-ADR-001-STORAGE", self._due())  # REVIEW_DUE 이벤트는 있지만 resolved

    def test_excludes_superseded_decision(self):
        self.assertNotIn("DEC-ADR-002-API", self._due())

    def test_duplicate_events_return_current_one(self):
        items = decision_read.list_review_due()["items"]
        self.assertEqual([i["decision_id"] for i in items].count("DEC-2026-0314-DB"), 1)
        item = self._due()["DEC-2026-0314-DB"]
        self.assertEqual(item["event"]["event_id"], self.latest_event)  # resolved된 first_event가 아님
        self.assertNotEqual(item["event"]["event_id"], self.first_event)
        self.assertTrue(item["event"]["acknowledged"])  # acknowledge해도 현재 due면 목록에 남는다
        self.assertEqual(item["observed"]["value"], 1350)
        self.assertEqual(item["detected_by"], "review_event")

    def test_baseline_due_without_event(self):
        item = self._due()["DEC-TEST-VERIFIED"]
        self.assertIsNone(item["event"])
        self.assertEqual(item["detected_by"], "review_state_baseline")
        self.assertEqual(item["review_status"], "REVIEW_DUE")
        self.assertEqual(item["trigger"]["metric_key"], "user_count")
        self.assertEqual(item["trigger_text"], "user_count > 1000")
        self.assertEqual(item["observed"], {"metric": "user_count", "value": 1400, "unit": "users",
                                            "source": "sqlite:users", "observed_at": "2026-11-06T00:00:00+00:00"})
        self.assertEqual(item["project_id"], PID)

    def test_project_filter(self):
        self.assertEqual(set(self._due(project_id=PID)), {"DEC-TEST-VERIFIED", "DEC-2026-0314-DB"})
        self.assertEqual(decision_read.list_review_due(project_id=999)["error"], "project_not_found")

    # ---------------------------------------------------------------- get_decision_detail

    def test_verified_decision_detail(self):
        d = decision_read.get_decision_detail("DEC-TEST-VERIFIED")
        self.assertTrue(d["human_verified"])
        self.assertEqual(d["project"], "Decision Debt MVP")
        self.assertEqual(d["rationale"], "운영 부담보다 기능 개발이 우선")
        self.assertEqual(d["assumptions"], ["초기 사용자는 적다"])
        self.assertEqual(d["review_trigger"]["threshold"], 1000)
        self.assertEqual(d["review_state"]["last_review_status"], "REVIEW_DUE")
        self.assertEqual(d["review_state"]["last_observed"]["value"], 1400)
        self.assertIsNone(d["open_review_event"])

    def test_detail_sources_and_evidence_without_full_content(self):
        d = decision_read.get_decision_detail("DEC-TEST-VERIFIED")
        self.assertEqual(len(d["sources"]), 1)  # 같은 Source의 근거 2개 → Source는 1개
        src = d["sources"][0]
        self.assertEqual((src["source_id"], src["type"]), ("meeting_01_database", "meeting"))
        self.assertTrue(src["title"])
        self.assertNotIn("content", src)  # 원문 전체를 돌려주지 않는다
        self.assertEqual(d["evidence"][0]["excerpt"], "SQLite를 유지한다")
        self.assertEqual(d["evidence"][0]["source_title"], src["title"])
        self.assertEqual(len(d["evidence"][1]["excerpt"]), decision_read.EVIDENCE_EXCERPT_CHARS)
        self.assertTrue(d["evidence"][1]["truncated"])

    def test_unmonitored_decision_has_null_review_state(self):
        d = decision_read.get_decision_detail("DEC-2026-0328-AUTH")  # manual 조건 → monitor 대상 아님
        self.assertIsNone(d["review_state"])
        self.assertFalse(d["human_verified"])

    def test_missing_decision(self):
        self.assertEqual(decision_read.get_decision_detail("DEC-NOPE"), {"error": "decision_not_found", "decision_id": "DEC-NOPE"})

    # ---------------------------------------------------------------- read-only

    def test_reads_do_not_modify_db(self):
        decision_read.list_review_due()
        for decision_id in ("DEC-TEST-VERIFIED", "DEC-2026-0314-DB", "DEC-NOPE"):
            decision_read.get_decision_detail(decision_id)
        self.assertEqual(_dump(), self.before)

    def test_read_only_connection_rejects_writes(self):
        with self.assertRaises(sqlite3.OperationalError):
            with db.connect() as conn:
                conn.execute("UPDATE decisions SET title = 'x' WHERE id = 'DEC-TEST-VERIFIED'")
        self.assertEqual(_dump(), self.before)

    def test_mcp_exposes_only_two_read_only_tools(self):
        import mcp_server

        tools = asyncio.run(mcp_server.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in tools), ["get_decision_detail", "list_review_due"])
        self.assertTrue(all(t.annotations.read_only_hint for t in tools))
        self.assertTrue(db.READ_ONLY)

    def test_mcp_db_error_is_summarized(self):
        import mcp_server

        path, db.DB_PATH = db.DB_PATH, Path(self._tmp.name) / "missing.db"
        try:
            result = mcp_server.get_decision_detail("DEC-TEST-VERIFIED")
        finally:
            db.DB_PATH = path
        self.assertEqual(result["error"], "database_unavailable")
        self.assertNotIn("Traceback", result["message"])


if __name__ == "__main__":
    unittest.main()
