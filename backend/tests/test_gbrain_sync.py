"""gbrain knowledge sync (delegated `gbrain sync --working-tree`). 임시 DB + 임시 brain 폴더 + 가짜 gbrain만 쓴다.

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_gbrain_sync -v
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
from config import BACKEND_ENV_KEYS  # noqa: E402
from schemas import Decision, Evidence, KnowledgeSyncStatus, MetricTrigger  # noqa: E402
from services import gbrain_sync  # noqa: E402

PID = 1
DEC = "DEC-TEST-GBRAIN"
SYNC_ARGS = ["sync", "--no-pull", "--source", "decision-debt", "--working-tree", "--json"]


def _decision(**kw) -> Decision:
    base = dict(
        id=DEC, project_id=PID, topic="database", title="SQLite 유지", decision="MVP에서는 SQLite를 유지한다.",
        rationale="기능 개발 우선", assumptions=[], review_trigger=MetricTrigger(
            raw_metric="사용자 수", metric_key="user_count", operator=">", threshold=1000),
        status="active", origin="review_approved", human_verified=True,
        evidence=[Evidence(source_id="meeting_01_database", quote="SQLite를 유지한다")],
        created_at="2026-11-06", updated_at="2026-11-06", history=[],
    )
    base.update(kw)
    return Decision(**base)


class GbrainSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls._orig = (db.DB_PATH, gbrain_sync.GBRAIN_BIN, gbrain_sync.GBRAIN_REPO_DIR, gbrain_sync.GBRAIN_DECISIONS_DIR,
                     gbrain_sync.GBRAIN_SYNC_ENABLED)
        db.DB_PATH = tmp / "test.db"
        db.READ_ONLY = False
        db.init_db()
        with db.connect() as conn:
            db.insert_decision(conn, _decision())

        cls.repo = tmp / "brain"  # git commit하지 않은 working tree (새 파일 그대로)
        cls.log = tmp / "gbrain_calls.jsonl"
        fake = tmp / "gbrain"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).parent / "fake_gbrain.py"}" "$@"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        gbrain_sync.GBRAIN_BIN = str(fake)
        gbrain_sync.GBRAIN_REPO_DIR = cls.repo
        gbrain_sync.GBRAIN_DECISIONS_DIR = cls.repo / "decisions"
        gbrain_sync.GBRAIN_SYNC_ENABLED = True
        os.environ["FAKE_GBRAIN_LOG"] = str(cls.log)

    @classmethod
    def tearDownClass(cls):
        (db.DB_PATH, gbrain_sync.GBRAIN_BIN, gbrain_sync.GBRAIN_REPO_DIR, gbrain_sync.GBRAIN_DECISIONS_DIR,
         gbrain_sync.GBRAIN_SYNC_ENABLED) = cls._orig
        os.environ.pop("FAKE_GBRAIN_LOG", None)
        cls._tmp.cleanup()

    def setUp(self):
        self.log.unlink(missing_ok=True)
        with db.connect() as conn:
            conn.execute("DELETE FROM decision_knowledge_sync")
        os.environ["FAKE_GBRAIN_MODE"] = "noop"

    def _calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def _sync(self, decision=None, force=False):
        return asyncio.run(gbrain_sync.sync_decision_to_gbrain(decision or db.get_decision(DEC), force=force))

    def _state(self):
        return db.get_knowledge_sync(DEC, gbrain_sync.TARGET)

    # ---------------------------------------------------------------- command

    def test_sync_command_args_and_cwd(self):
        self.assertEqual(gbrain_sync.sync_command()[1:], SYNC_ARGS)
        r = self._sync()
        self.assertEqual(r.status, "synced", r.message)
        calls = self._calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"], SYNC_ARGS)
        self.assertEqual(Path(calls[0]["cwd"]).resolve(), self.repo.resolve())

    def test_import_and_embed_never_called(self):
        self._sync()
        self._sync(force=True)
        argvs = [c["argv"] for c in self._calls()]
        self.assertEqual(len(argvs), 2)
        for argv in argvs:
            self.assertNotIn("import", argv)
            self.assertNotIn("embed", argv)
            self.assertNotIn("--stale", argv)
            self.assertEqual(argv[0], "sync")

    def test_new_uncommitted_file_is_exported_into_working_tree(self):
        self._sync()
        path = self.repo / "decisions" / gbrain_sync.markdown_filename(DEC)
        self.assertTrue(path.exists())  # git commit 없이 working tree에 있는 새 파일 → --working-tree sync 대상
        self.assertIn("--working-tree", self._calls()[0]["argv"])

    # ---------------------------------------------------------------- 결과 판정

    def test_noop_up_to_date_is_success(self):
        os.environ["FAKE_GBRAIN_MODE"] = "noop"  # added=0 modified=0 embedded=0
        r = self._sync()
        self.assertEqual(r.status, "synced")
        self.assertIn("up_to_date", r.message)
        state = self._state()
        self.assertEqual(state["status"], "synced")
        self.assertTrue(state["content_hash"])

    def test_synced_with_changes(self):
        os.environ["FAKE_GBRAIN_MODE"] = "synced"
        self.assertEqual(self._sync().status, "synced")

    def test_pglite_busy_records_failed(self):
        os.environ["FAKE_GBRAIN_MODE"] = "busy"
        r = self._sync()
        self.assertEqual(r.status, "failed")
        self.assertIn("pglite_busy", r.message)
        self.assertEqual(self._state()["status"], "failed")
        self.assertIsNotNone(db.get_decision(DEC))

    def test_not_synced_outcome_is_failure(self):
        os.environ["FAKE_GBRAIN_MODE"] = "not_synced"
        r = self._sync()
        self.assertEqual(r.status, "failed")
        self.assertIn("synced가 아님", r.message)

    # ---------------------------------------------------------------- hash skip / force

    def test_unchanged_hash_skips_and_force_resyncs(self):
        self.assertEqual(self._sync().status, "synced")
        self.assertEqual(self._sync().status, "unchanged")  # 같은 내용 → gbrain 호출 없음
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sync(force=True).status, "synced")  # --force → 같은 delegated sync
        self.assertEqual([c["argv"] for c in self._calls()], [SYNC_ARGS, SYNC_ARGS])

    def test_failed_then_resync_recovers(self):
        os.environ["FAKE_GBRAIN_MODE"] = "busy"
        self.assertEqual(self._sync().status, "failed")
        os.environ["FAKE_GBRAIN_MODE"] = "noop"
        self.assertEqual(self._sync().status, "synced")  # failed 상태면 hash가 같아도 skip하지 않는다
        self.assertEqual(self._state()["status"], "synced")

    # ---------------------------------------------------------------- 환경변수

    def test_backend_secrets_not_passed(self):
        prev = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "secret-test-value"
        BACKEND_ENV_KEYS.add("OPENROUTER_API_KEY")
        try:
            self._sync()
        finally:
            if prev is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = prev
        self.assertNotIn("OPENROUTER_API_KEY", self._calls()[0]["env"])

    # ---------------------------------------------------------------- manual script / approval

    def test_manual_script_uses_same_service(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import sync_gbrain as script

        argv = sys.argv
        sys.argv = ["sync_gbrain.py", "--decision", DEC, "--force"]
        try:
            code = script.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 0)
        self.assertEqual([c["argv"] for c in self._calls()], [SYNC_ARGS])
        self.assertEqual(self._state()["status"], "synced")

    def test_approval_kept_when_gbrain_sync_fails(self):
        from fastapi.testclient import TestClient

        import app

        source = db.get_source("meeting_01_database")
        cand = db.insert_candidates(PID, source, [{
            "topic": "cache", "title": "캐시 유지", "decision": "내부 캐시를 유지한다.", "rationale": "단순함",
            "assumptions": [], "review_trigger": None, "evidence": "SQLite를 유지한다", "evidence_in_source": True,
        }], extractor="test")[0]

        async def no_dify(*_args, **_kw):  # 실제 Dify를 부르지 않는다
            return KnowledgeSyncStatus(status="not_connected", message="test")

        orig_dify = app.sync_after_approve
        app.sync_after_approve = no_dify
        os.environ["FAKE_GBRAIN_MODE"] = "busy"
        try:
            r = TestClient(app.app).post(f"/api/candidates/{cand.id}/approve")
        finally:
            app.sync_after_approve = orig_dify
        self.assertEqual(r.status_code, 200)  # gbrain 실패여도 승인 응답은 정상
        body = r.json()
        self.assertEqual(body["gbrain_sync"]["status"], "pending")
        decision_id = body["decision"]["id"]
        self.assertIsNotNone(db.get_decision(decision_id))  # Decision rollback 없음
        self.assertEqual(db.get_candidate(cand.id).status, "approved")  # approval rollback 없음
        state = db.get_knowledge_sync(decision_id, gbrain_sync.TARGET)
        self.assertEqual(state["status"], "failed")  # background sync 실패는 기록만
        self.assertIn("pglite_busy", state["error"])
        self.assertEqual([c["argv"] for c in self._calls()], [SYNC_ARGS])


if __name__ == "__main__":
    unittest.main()
