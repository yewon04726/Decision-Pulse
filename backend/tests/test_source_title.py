"""오디오 Source 제목 (services/source_title + /api/extract hook). 임시 DB만 쓴다 (LLM 호출 없음 — extraction은 stub).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_source_title -v
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from services import source_title  # noqa: E402
from services.source_title import title_from_topics  # noqa: E402

PID = 1


def _draft(topic: str) -> dict:
    return {"topic": topic, "title": f"{topic} 결정", "decision": f"{topic} 결정", "rationale": "", "assumptions": [],
            "review_trigger": None, "evidence": "인용", "evidence_in_source": True}


class TitleRuleTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(title_from_topics(["database"]), "데이터베이스 회의")
        self.assertEqual(title_from_topics(["database", "search"]), "데이터베이스 · 검색 회의")
        self.assertEqual(title_from_topics(["deployment", "monitoring", "authentication"]), "배포 · 모니터링 · 인증 회의")
        self.assertEqual(title_from_topics(["database", "search", "cache", "deployment", "monitoring"]),
                         "데이터베이스 · 검색 · 캐시 외 2개 회의")

    def test_duplicates_and_unknown(self):
        self.assertEqual(title_from_topics(["database", "search", "database", "search"]), "데이터베이스 · 검색 회의")
        self.assertEqual(title_from_topics(["database", "billing-v2"]), "데이터베이스 · billing-v2 회의")
        self.assertEqual(title_from_topics(["deploy", "deployment"]), "배포 회의")  # 같은 표시명은 하나로
        self.assertIsNone(title_from_topics([]))
        self.assertIsNone(title_from_topics(["", "  "]))


class AudioTitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = db.DB_PATH
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.READ_ONLY = False
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls._orig
        cls._tmp.cleanup()

    def _audio(self, title="신수동", filename="신수동.m4a"):
        src = db.create_audio_source(PID, title, filename, "/tmp/x.m4a", "audio/mp4", 10)
        db.complete_transcription(src.id, "[00:00:01] 회의 내용", "[]", 1, 1.0, 1.0, 1.0)
        return db.get_source(src.id)

    def _extract(self, source, topics):
        db.insert_candidates(PID, source, [_draft(t) for t in topics], extractor="test")
        return source_title.update_audio_source_title(source.id)

    def test_upload_origin(self):
        self.assertEqual(self._audio("신수동", "신수동.m4a").audio.title_origin, "filename")
        self.assertEqual(self._audio("신수동.m4a", "신수동.m4a").audio.title_origin, "filename")
        self.assertEqual(self._audio("DB 결정 회의", "db_meeting.m4a").audio.title_origin, "user")
        self.assertEqual(db._audio_title_origin("신수동 2", "신수동 2.m4a"), "filename")  # migration 분류도 같은 규칙

    def test_extraction_updates_filename_title(self):
        src = self._audio()
        self.assertEqual(self._extract(src, ["database", "search"]), "데이터베이스 · 검색 회의")
        updated = db.get_source(src.id)
        self.assertEqual((updated.title, updated.audio.title_origin), ("데이터베이스 · 검색 회의", "auto_topics"))
        self.assertEqual(updated.content, src.content)  # transcript는 그대로

    def test_reextract_is_stable_and_can_grow(self):
        src = self._audio()
        self._extract(src, ["database", "search"])
        self.assertIsNone(self._extract(src, ["database", "search"]))  # 중복 후보 → 같은 제목, 변경 없음
        self.assertEqual(db.get_source(src.id).title, "데이터베이스 · 검색 회의")
        self.assertEqual(self._extract(src, ["cache"]), "데이터베이스 · 검색 · 캐시 회의")  # auto 제목은 새 topic 반영

    def test_no_candidates_keeps_title(self):
        src = self._audio()
        self.assertIsNone(source_title.update_audio_source_title(src.id))
        self.assertEqual(db.get_source(src.id).title, "신수동")

    def test_user_title_never_overwritten(self):
        src = self._audio("DB 결정 회의 (오디오 테스트)", "db_meeting.m4a")  # 업로드 때 사람이 입력
        self.assertIsNone(self._extract(src, ["database"]))
        self.assertEqual(db.get_source(src.id).title, "DB 결정 회의 (오디오 테스트)")
        auto = self._audio()
        self._extract(auto, ["database"])
        self.assertTrue(db.set_source_title(auto.id, "내가 정한 제목", "user"))  # 나중에 사람이 수정
        self.assertIsNone(self._extract(auto, ["search"]))
        self.assertFalse(db.set_source_title(auto.id, "자동", "auto_topics"))  # DB 수준에서도 거부
        self.assertEqual(db.get_source(auto.id).title, "내가 정한 제목")

    def test_text_source_untouched(self):
        text = db.create_source(PID, "회의록 원문.md", "meeting", "본문", None)
        db.insert_candidates(PID, text, [_draft("database")], extractor="test")
        self.assertIsNone(source_title.update_audio_source_title(text.id))
        self.assertFalse(db.set_source_title(text.id, "자동", "auto_topics"))
        self.assertEqual(db.get_source(text.id).title, "회의록 원문.md")

    def test_extract_api_and_patch(self):
        from fastapi.testclient import TestClient

        import app

        async def fake_extract(content, metrics, provider="local", stats=None, profile=None):
            return [_draft("deployment"), _draft("monitoring"), _draft("authentication")], []

        orig = app.extraction.extract
        app.extraction.extract = fake_extract
        try:
            client = TestClient(app.app)
            src = self._audio("신수동 2", "신수동 2.m4a")
            r = client.post("/api/extract", json={"project_id": PID, "source_id": src.id, "mode": "local"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["source"]["title"], "배포 · 모니터링 · 인증 회의")
            self.assertEqual(r.json()["source"]["audio"]["title_origin"], "auto_topics")
            r = client.patch(f"/api/sources/{src.id}", json={"title": "11월 인프라 회의"})
            self.assertEqual((r.status_code, r.json()["audio"]["title_origin"]), (200, "user"))
            client.post("/api/extract", json={"project_id": PID, "source_id": src.id, "mode": "local"})
            self.assertEqual(db.get_source(src.id).title, "11월 인프라 회의")
            self.assertEqual(client.patch("/api/sources/nope", json={"title": "x"}).status_code, 404)
        finally:
            app.extraction.extract = orig


if __name__ == "__main__":
    unittest.main()
