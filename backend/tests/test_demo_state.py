"""Demo Control Center (/api/demo/*). 실제 sample DB / Telegram은 건드리지 않는다.

- 값 쓰기: ~/decision-debt-tool의 실제 CLI script(set_user_count.py, set_metric.py)를 실행하되
  SERVICE_DB_PATH / METRICS_STORE_PATH 환경변수로 임시 sample DB를 가리킨다.
- 값 읽기: check_metric HTTP 대신, tool의 resolver와 같은 SQL로 임시 DB를 읽는 stub.
- Review Monitor는 실행하지 않는다 → Demo API가 Decision / review state / event를 직접 바꾸지 않는지 확인.
실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_demo_state -v
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
from schemas import Metric  # noqa: E402
from services import demo_state  # noqa: E402
from services import metrics as metric_service  # noqa: E402
from services import telegram_notifier  # noqa: E402

TOOL_OK = config.DEMO_TOOL_PYTHON.exists() and (config.DEMO_TOOL_DIR / "scripts" / "set_user_count.py").exists()


@unittest.skipUnless(TOOL_OK, "~/decision-debt-tool (venv + scripts) 없음")
class DemoApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import app

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.sample = tmp / "sample_service.db"
        cls._orig = (db.DB_PATH, config.DEMO_MODE_ENABLED, metric_service.check_metrics_at,
                     telegram_notifier.send_review_due_notification,
                     os.environ.get("SERVICE_DB_PATH"), os.environ.get("METRICS_STORE_PATH"))
        db.DB_PATH = tmp / "test.db"
        db.READ_ONLY = False
        db.init_db()
        # tool script가 임시 sample DB에 쓰도록 (tool settings는 환경변수가 우선)
        os.environ["SERVICE_DB_PATH"] = str(cls.sample)
        os.environ["METRICS_STORE_PATH"] = str(cls.sample)
        with sqlite3.connect(cls.sample) as conn:
            conn.executescript(
                "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL);"
                "CREATE TABLE metrics (name TEXT PRIMARY KEY, value REAL NOT NULL, unit TEXT, updated_at TEXT NOT NULL);"
            )

        sample = cls.sample

        async def read_sample(_url, names):
            """tool resolver와 같은 SQL로 읽기 (check_metric HTTP 대체)"""
            found, errors = {}, {}
            with sqlite3.connect(sample) as conn:
                for n in names:
                    if n == "user_count":
                        v = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                        found[n] = Metric(metric_name=n, value=v, unit="users", source="sqlite:users", observed_at="t")
                        continue
                    row = conn.execute("SELECT value, unit, updated_at FROM metrics WHERE name = ?", (n,)).fetchone()
                    if row is None:
                        errors[n] = metric_service.MetricError("unavailable", f"metrics store에 '{n}' 값이 없습니다")
                    else:
                        v = int(row[0]) if float(row[0]).is_integer() else row[0]
                        found[n] = Metric(metric_name=n, value=v, unit=row[1], source=f"metrics_store:{n}", observed_at=row[2])
            return found, errors

        metric_service.check_metrics_at = read_sample
        cls.telegram_calls = []

        async def no_telegram(*a, **k):
            cls.telegram_calls.append(a)
            return telegram_notifier.NotificationResult("sent", "fake")

        telegram_notifier.send_review_due_notification = no_telegram
        cls.client = TestClient(app.app)  # with 없이 → lifespan(review monitor) 실행 안 함

    @classmethod
    def tearDownClass(cls):
        (db.DB_PATH, config.DEMO_MODE_ENABLED, metric_service.check_metrics_at,
         telegram_notifier.send_review_due_notification, svc, mst) = cls._orig
        for k, v in (("SERVICE_DB_PATH", svc), ("METRICS_STORE_PATH", mst)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._tmp.cleanup()

    def setUp(self):
        config.DEMO_MODE_ENABLED = True
        asyncio.run(demo_state._run_tool(demo_state.tool_argv("users_table", "user_count", 900)))
        for name, value, unit in (("search_api_p95_ms", 500, "ms"), ("server_count", 1, "servers")):
            asyncio.run(demo_state._run_tool(demo_state.tool_argv("metrics_store", name, value) + ["--unit", unit]))

    def _users(self):
        with sqlite3.connect(self.sample) as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def _metric(self, name):
        with sqlite3.connect(self.sample) as conn:
            return conn.execute("SELECT value FROM metrics WHERE name = ?", (name,)).fetchone()[0]

    def _decision_snapshot(self):
        with db.connect() as conn:
            return (
                [tuple(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()],
                conn.execute("SELECT COUNT(*) FROM decision_review_state").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM review_events").fetchone()[0],
            )

    # ---- Demo Mode 보호

    def test_disabled_blocks_everything(self):
        config.DEMO_MODE_ENABLED = False
        for method, path, body in (
            ("get", "/api/demo/status", None),
            ("get", "/api/demo/state", None),
            ("get", "/api/demo/state/user_count/preview?value=1400", None),
            ("patch", "/api/demo/state/user_count", {"value": 1400}),
            ("post", "/api/demo/reset", None),
        ):
            r = getattr(self.client, method)(path, **({"json": body} if body else {}))
            self.assertEqual(r.status_code, 404, path)
        self.assertEqual(self._users(), 900)  # 값도 바뀌지 않음

    # ---- 입력 검증

    def test_unknown_metric(self):
        self.assertEqual(self.client.patch("/api/demo/state/cpu_usage", json={"value": 1}).status_code, 404)
        self.assertEqual(self.client.get("/api/demo/state/cpu_usage/preview?value=1").status_code, 404)

    def test_invalid_values(self):
        for body in ({"value": "1400"}, {"value": None}, {}, {"value": True}):
            self.assertEqual(self.client.patch("/api/demo/state/user_count", json=body).status_code, 422, body)
        self.assertEqual(self.client.patch("/api/demo/state/user_count", json={"value": -1}).status_code, 422)
        self.assertEqual(self.client.patch("/api/demo/state/user_count", json={"value": 1000.5}).status_code, 422)
        self.assertEqual(self.client.patch("/api/demo/state/server_count", json={"value": 2.5}).status_code, 422)
        too_many = config.DEMO_MAX_USER_COUNT + 1
        self.assertEqual(self.client.patch("/api/demo/state/user_count", json={"value": too_many}).status_code, 422)
        self.assertEqual(self._users(), 900)

    # ---- 값 변경

    def test_user_count_changes_users_rows(self):
        r = self.client.patch("/api/demo/state/user_count", json={"value": 1400})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["before"], r.json()["after"], r.json()["unit"]), (900, 1400, "users"))
        self.assertEqual(self._users(), 1400)
        r = self.client.patch("/api/demo/state/user_count", json={"value": 950})
        self.assertEqual((r.json()["before"], r.json()["after"]), (1400, 950))
        self.assertEqual(self._users(), 950)

    def test_metrics_store_value(self):
        r = self.client.patch("/api/demo/state/search_api_p95_ms", json={"value": 850})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["before"], r.json()["after"], r.json()["source"]), (500, 850, "metrics_store:search_api_p95_ms"))
        self.assertEqual(self._metric("search_api_p95_ms"), 850)
        self.assertEqual(self.client.patch("/api/demo/state/search_api_p95_ms", json={"value": 799.5}).json()["after"], 799.5)

    def test_not_simulator_backed_source(self):
        orig = metric_service.check_metrics_at

        async def prometheus(_url, names):
            found, errors = await orig(_url, names)
            found["server_count"] = found["server_count"].model_copy(update={"source": "prometheus:servers"})
            return found, errors

        metric_service.check_metrics_at = prometheus
        try:
            self.assertEqual(self.client.patch("/api/demo/state/server_count", json={"value": 3}).status_code, 409)
            state = self.client.get("/api/demo/state").json()
            self.assertIsNone(next(m for m in state["metrics"] if m["name"] == "server_count")["simulator"])
        finally:
            metric_service.check_metrics_at = orig
        self.assertEqual(self._metric("server_count"), 1)

    def test_does_not_touch_decisions_or_review_state(self):
        before = self._decision_snapshot()
        self.client.patch("/api/demo/state/user_count", json={"value": 1400})
        self.client.patch("/api/demo/state/server_count", json={"value": 3})
        self.client.post("/api/demo/reset")
        self.assertEqual(self._decision_snapshot(), before)
        self.assertEqual(self.telegram_calls, [])  # Demo API는 Telegram을 부르지 않는다

    def test_reset_restores_baseline(self):
        self.client.patch("/api/demo/state/user_count", json={"value": 1400})
        self.client.patch("/api/demo/state/search_api_p95_ms", json={"value": 850})
        r = self.client.post("/api/demo/reset")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({c["metric"] for c in r.json()}, {"user_count", "search_api_p95_ms"})  # 이미 baseline인 server_count는 건너뜀
        self.assertEqual((self._users(), self._metric("search_api_p95_ms"), self._metric("server_count")), (900, 500, 1))

    # ---- 조회 / 미리보기 (backend 판정 함수 사용)

    def test_state_and_preview_use_real_evaluation(self):
        state = self.client.get("/api/demo/state").json()
        users = next(m for m in state["metrics"] if m["name"] == "user_count")
        self.assertEqual((users["value"], users["simulator"], users["integer"], users["baseline"]), (900, "users_table", True, 900))
        self.assertTrue(users["decisions"])
        for d in users["decisions"]:  # 900이면 모든 user_count 조건 미충족
            self.assertEqual(d["status"], "ACTIVE")
        # preset: 경계 바로 아래/위, 판정이 실제로 바뀐다
        for p in users["presets"]:
            self.assertEqual(p["review_due"], sum(1 for d in users["decisions"] if _met(d, p["value"])))
        pv = self.client.get("/api/demo/state/user_count/preview?value=1400").json()
        expected = [d for d in users["decisions"] if _met(d, 1400)]
        self.assertEqual(pv["review_due"], len(expected))
        self.assertEqual(pv["affected"], len(expected))
        self.assertEqual({i["decision_id"] for i in pv["decisions"] if i["predicted"] == "REVIEW_DUE"},
                         {d["decision_id"] for d in expected})
        self.assertEqual(self._users(), 900)  # preview는 값을 바꾸지 않는다


def _met(d, value):
    return {">": value > d["threshold"], ">=": value >= d["threshold"], "<": value < d["threshold"],
            "<=": value <= d["threshold"], "==": value == d["threshold"], "!=": value != d["threshold"]}[d["operator"]]


if __name__ == "__main__":
    unittest.main()
