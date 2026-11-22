"""Candidate 승인 시 title/decision 전달 (db.approve_candidate). 임시 DB만 쓴다 (LLM/Dify/gbrain 호출 없음).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_approve_title -v
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

PID = 1


def _draft(title: str, decision: str) -> dict:
    return {"topic": "database", "title": title, "decision": decision, "rationale": "나중에 옮길 수 있다",
            "assumptions": [], "review_trigger": None, "evidence": "SQLite를 유지한다", "evidence_in_source": True}


class ApproveTitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = db.DB_PATH
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.READ_ONLY = False
        db.init_db()
        cls.source = db.get_source("meeting_01_database")

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls._orig
        cls._tmp.cleanup()

    def _approve(self, title, decision):
        cand = db.insert_candidates(PID, self.source, [_draft(title, decision)], extractor="test")[0]
        return db.approve_candidate(cand.id)[1]

    def test_short_candidate_title_is_used(self):
        decision_text = "MVP에서는 SQLite 그대로 가죠. 지금 굳이 프로그레스 SQL로 옮기지는 않는 걸로요."
        d = self._approve("MVP SQLite 유지", decision_text)
        self.assertEqual(d.title, "MVP SQLite 유지")
        self.assertEqual(d.decision, decision_text)  # 본문은 그대로, title과 별도
        stored = db.get_decision(d.id)
        self.assertEqual((stored.title, stored.decision), ("MVP SQLite 유지", decision_text))

    def test_extraction_fallback_title_is_kept_as_is(self):
        # extraction title 검증 실패 시 fallback(= decision 문장)은 승인에서 바꾸지 않는다 (새 제목을 만들지 않음)
        decision_text = "레디스는 당장 도입하지 않는다."
        d = self._approve(decision_text.rstrip("."), decision_text)
        self.assertEqual(d.title, "레디스는 당장 도입하지 않는다")
        self.assertEqual(d.decision, decision_text)

    def test_seed_decisions_untouched_by_approval(self):
        def seed_rows():
            with sqlite3.connect(db.DB_PATH) as conn:
                return conn.execute("SELECT * FROM decisions WHERE origin = 'seed_document' ORDER BY id").fetchall()

        before = seed_rows()
        self._approve("캐시 유지", "내부 캐시를 유지한다.")
        self.assertEqual(seed_rows(), before)


if __name__ == "__main__":
    unittest.main()
