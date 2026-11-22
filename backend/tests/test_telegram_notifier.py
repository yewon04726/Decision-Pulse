"""REVIEW_DUE Telegram 알림 (review_monitor → telegram_notifier). 실제 Telegram은 보내지 않는다.

- monitor 흐름: metric 조회는 stub, notifier는 기록용 mock
- notifier 자체: 가짜 hermes 실행 파일로 argv / 실패 / timeout 확인
실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_telegram_notifier -v
"""

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from schemas import Metric  # noqa: E402
from services import metrics as metric_service  # noqa: E402
from services import review_monitor, telegram_notifier  # noqa: E402

PID = 1
DEC = "DEC-2026-0314-DB"  # seed: user_count > 1000, title "SQLite"


class MonitorNotificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = (db.DB_PATH, metric_service.check_metrics, telegram_notifier.send_review_due_notification)
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.READ_ONLY = False
        db.init_db()
        cls.calls = []
        cls.fail = False

        async def fake_send(decision_id, title, trigger_text, observed_value=None, unit=None):
            cls.calls.append((decision_id, title, trigger_text, observed_value, unit))
            if cls.fail:
                raise RuntimeError("hermes send failed")
            return telegram_notifier.NotificationResult("sent", "ok")

        telegram_notifier.send_review_due_notification = fake_send

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH, metric_service.check_metrics, telegram_notifier.send_review_due_notification = cls._orig
        cls._tmp.cleanup()

    def setUp(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM review_events")
            conn.execute("DELETE FROM decision_review_state")
        self.calls.clear()
        type(self).fail = False

    def _cycle(self, user_count: int) -> list[int]:
        """review monitor 1회 (user_count만 바꾸고 나머지 지표는 고정)"""
        async def check(_project, names):
            values = {"user_count": (user_count, "users"), "search_api_p95_ms": (500, "ms"), "server_count": (1, "servers")}
            return {n: Metric(metric_name=n, value=values[n][0], unit=values[n][1], source="test", observed_at="now")
                    for n in names if n in values}, {}

        metric_service.check_metrics = check
        return asyncio.run(review_monitor.check_project(db.get_project(PID)))

    def _events(self, status=None):
        return [e for e in db.list_review_events(PID, limit=200) if e.decision_id == DEC and (status is None or e.new_status == status)]

    def _my_calls(self):
        return [c for c in self.calls if c[0] == DEC]

    def test_active_to_review_due_notifies_once(self):
        self._cycle(900)  # 처음: baseline ACTIVE (이벤트 없음)
        self.assertEqual(self._my_calls(), [])
        self._cycle(1400)  # ACTIVE → REVIEW_DUE
        self.assertEqual(len(self._events("REVIEW_DUE")), 1)
        self.assertEqual(self._my_calls(), [(DEC, "SQLite", "user_count > 1000", 1400, "users")])

    def test_staying_review_due_does_not_notify_again(self):
        self._cycle(900)
        self._cycle(1400)
        for _ in range(3):
            self._cycle(1400)  # 같은 상태 polling
        self.assertEqual(len(self._events("REVIEW_DUE")), 1)
        self.assertEqual(len(self._my_calls()), 1)

    def test_baseline_review_due_does_not_notify(self):
        self._cycle(1400)  # 처음 확인부터 REVIEW_DUE (서버 시작 시 기존 due 포함)
        self._cycle(1400)
        self.assertEqual(self._events(), [])
        self.assertEqual(self._my_calls(), [])
        self.assertEqual(db.get_review_states(PID)[DEC]["last_review_status"], "REVIEW_DUE")

    def test_recovery_does_not_notify_then_retrigger_notifies(self):
        self._cycle(900)
        self._cycle(1400)
        self._cycle(900)  # REVIEW_DUE → ACTIVE (복구 이벤트, 알림 아님)
        self.assertEqual(len(self._events("ACTIVE")), 1)
        self.assertEqual(len(self._my_calls()), 1)
        self._cycle(1400)  # 다시 ACTIVE → REVIEW_DUE
        self.assertEqual(len(self._events("REVIEW_DUE")), 2)
        self.assertEqual(len(self._my_calls()), 2)

    def test_notifier_failure_keeps_event_and_state(self):
        self._cycle(900)
        type(self).fail = True
        created = self._cycle(1400)  # notifier가 예외를 내도 monitor는 끝까지 진행
        self.assertEqual(len(self._events("REVIEW_DUE")), 1)
        self.assertIn(self._events("REVIEW_DUE")[0].id, created)
        self.assertEqual(db.get_review_states(PID)[DEC]["last_review_status"], "REVIEW_DUE")
        self._cycle(1400)  # 다음 polling에서 같은 이벤트를 다시 보내지 않는다 (retry queue 없음)
        self.assertEqual(len(self._my_calls()), 1)


class NotifierTest(unittest.TestCase):
    def test_message_format(self):
        msg = telegram_notifier.format_review_due_message(
            "DEC-2026-1106-DATABASE", "MVP SQLite 유지", "user_count > 1000", 1400, "users")
        self.assertEqual(msg, "🚨 Decision Review Due\n\nMVP SQLite 유지\nDEC-2026-1106-DATABASE\n\n"
                              "재검토 조건: user_count > 1000\n현재 관측값: 1400 users\n\n결정을 다시 검토할 시점입니다.")

    def test_message_without_unit_or_values(self):
        msg = telegram_notifier.format_review_due_message("DEC-X", "제목", "server_count >= 2", 2.0, None)
        self.assertIn("현재 관측값: 2\n", msg)  # 2.0 → 2, unit 없으면 뒤에 아무것도 붙이지 않음
        self.assertNotIn("None", msg)
        bare = telegram_notifier.format_review_due_message("DEC-X", None, None, None, None)
        self.assertNotIn("None", bare)
        self.assertNotIn("재검토 조건", bare)
        self.assertNotIn("현재 관측값", bare)

    # ---- 가짜 hermes로 subprocess 경로 확인 (실제 발송 없음)

    def _fake_hermes(self, body: str) -> str:
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp)
        self.log = Path(tmp) / "argv.json"
        path = Path(tmp) / "hermes"
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > \"{self.log}\"\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def _send(self, bin_path, timeout=5):
        orig = (telegram_notifier.HERMES_BIN, telegram_notifier.TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS, telegram_notifier.ENABLED)
        telegram_notifier.HERMES_BIN, telegram_notifier.TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS = bin_path, timeout
        telegram_notifier.ENABLED = True
        try:
            return asyncio.run(telegram_notifier.send_review_due_notification("DEC-X", "제목", "user_count > 1000", 1400, "users"))
        finally:
            telegram_notifier.HERMES_BIN, telegram_notifier.TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS, telegram_notifier.ENABLED = orig

    def test_argv_and_success(self):
        r = self._send(self._fake_hermes('echo \'{"success": true, "chat_id": "123456", "message_id": "10"}\''))
        self.assertEqual((r.status, r.message), ("sent", "message_id=10"))
        self.assertNotIn("123456", r.message)  # chat id는 결과/로그에 남기지 않는다
        argv = self.log.read_text().split("\n")
        self.assertEqual(argv[:4], ["send", "--to", "telegram", "--json"])
        self.assertIn("🚨 Decision Review Due", self.log.read_text())

    def test_nonzero_exit_is_failed_not_raised(self):
        r = self._send(self._fake_hermes("echo 'telegram: Unauthorized' >&2; exit 3"))
        self.assertEqual(r.status, "failed")
        self.assertIn("exit 3", r.message)

    def test_timeout_is_failed(self):
        r = self._send(self._fake_hermes("sleep 5"), timeout=0.5)
        self.assertEqual((r.status, r.message), ("failed", "timeout (0.5s)"))

    def test_missing_binary_is_failed(self):
        self.assertEqual(self._send("/nonexistent/hermes").status, "failed")

    def test_disabled(self):
        orig = telegram_notifier.ENABLED
        telegram_notifier.ENABLED = False
        try:
            r = asyncio.run(telegram_notifier.send_review_due_notification("DEC-X", "t", None))
        finally:
            telegram_notifier.ENABLED = orig
        self.assertEqual(r.status, "disabled")


if __name__ == "__main__":
    unittest.main()
