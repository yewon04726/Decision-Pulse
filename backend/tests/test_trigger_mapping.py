"""Review Trigger mapping 상태 (CONNECTED / NEEDS_MAPPING / MANUAL)와 UNKNOWN 구분. 임시 DB + 가짜 metric adapter.

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_trigger_mapping -v
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import metric_registry  # noqa: E402
from config import KNOWN_METRICS  # noqa: E402
from schemas import Decision, ManualTrigger, Metric, MetricTrigger  # noqa: E402
from services import metric_adapters, review_monitor  # noqa: E402
from services import metrics as metric_service  # noqa: E402
from services.extraction import validate_trigger  # noqa: E402
from services.review import to_view  # noqa: E402
from services.review_trigger import format_review_trigger_v2, normalize_review_trigger  # noqa: E402
from services.trigger_status import classify  # noqa: E402

PID = 1
CONNECTED = "DEC-T-USERS"
DEPLOY = "DEC-T-DEPLOY"
MANUAL = "DEC-T-MANUAL"
LEGACY_DEPLOY = {  # 현재 DB의 '배포 자동화 미확정'과 같은 저장 형태
    "type": "metric", "raw_metric": "프로덕션 배포 횟수", "metric_key": None, "operator": ">", "threshold": 5, "unit": "회",
    "needs_mapping": True, "raw_condition": "프로덕션 배포가 일주일에 5회를 넘으면 기터브 액션 기반 자동 배포를 다시 검토하는 걸로요",
    "metric": None, "description": "프로덕션 배포가 일주일에 5회를 넘으면 기터브 액션 기반 자동 배포를 다시 검토하는 걸로요", "note": None,
}


def _decision(did, trigger):
    return Decision(id=did, project_id=PID, topic="t", title=did, decision="d", rationale="r", assumptions=[],
                    review_trigger=trigger, status="active", origin="review_approved", human_verified=True, evidence=[],
                    created_at="2026-11-25", updated_at="2026-11-25", history=[])


def _reading(name, value, unit="users", source="sqlite:users"):
    return Metric(metric_name=name, value=value, unit=unit, source=source, observed_at="2026-11-25T00:00:00+00:00")


class RegistryTest(unittest.TestCase):
    def test_existing_metrics_stay_connected(self):
        self.assertEqual(KNOWN_METRICS, ["user_count", "search_api_p95_ms", "server_count"])
        for name in KNOWN_METRICS:
            self.assertEqual(metric_registry.get(name).resolver, "check_metric")
        self.assertIsNone(metric_registry.get("deployment_count").resolver)  # 정의는 있지만 연결 안 됨

    def test_alias_normalization(self):
        for raw in ("실제 사용자 수", "사용자 수", "가입자 수"):
            self.assertEqual(metric_registry.canonicalize(raw), "user_count", raw)
        for raw in ("프로덕션 배포 횟수", "배포 횟수", "릴리즈 빈도"):
            self.assertEqual(metric_registry.canonicalize(raw), "deployment_count", raw)
        self.assertIsNone(metric_registry.canonicalize("동시 사용자 수"))  # 부분 일치는 하지 않는다
        self.assertIsNone(metric_registry.canonicalize("서버", "GB"))  # 단위가 맞지 않으면 매핑하지 않는다


class ClassifyTest(unittest.TestCase):
    def test_connected(self):
        t = normalize_review_trigger({"type": "metric", "metric": "user_count", "operator": ">", "threshold": 1000})
        s = classify(t)
        self.assertEqual((s.mapping_status, s.canonical_metric, s.resolver), ("CONNECTED", "user_count", "check_metric"))

    def test_needs_mapping_registered_and_unregistered(self):
        t = normalize_review_trigger(LEGACY_DEPLOY)
        self.assertEqual((t.metric_key, t.canonical_metric, t.window), (None, "deployment_count", "7d"))
        self.assertEqual(classify(t).mapping_status, "NEEDS_MAPPING")
        self.assertEqual(format_review_trigger_v2(t), "프로덕션 배포 횟수 > 5 회 / 7일")
        unknown = normalize_review_trigger({"type": "metric", "raw_metric": "결제 실패 건수", "operator": ">", "threshold": 3})
        s = classify(unknown)
        self.assertEqual((s.mapping_status, s.canonical_metric), ("NEEDS_MAPPING", None))

    def test_manual(self):
        for t in (
            ManualTrigger(description="고객 불만이 많아지면 다시 검토"),
            normalize_review_trigger({"type": "event", "description": "첫 유료 기업 고객이 SSO를 요구"}),
            normalize_review_trigger({"type": "metric", "raw_metric": "고객 불만", "operator": ">", "threshold": 10}),
        ):
            self.assertEqual(classify(t).mapping_status, "MANUAL", t)
        compound = normalize_review_trigger({"type": "compound", "logic": "AND", "conditions": [
            {"type": "metric", "metric": "search_api_p95_ms", "operator": ">", "threshold": 800},
            {"type": "duration", "value": 3, "unit": "day"}]})
        self.assertEqual(classify(compound).mapping_status, "MANUAL")  # 복합 조건은 자동 평가하지 않음 (기존과 같음)

    def test_resolver_added_later_connects_stored_trigger(self):
        """registry에 resolver를 붙이면 저장된 Decision을 고치지 않아도 CONNECTED가 된다"""
        import config
        import schemas

        orig = (schemas.KNOWN_METRICS, config.KNOWN_METRICS)
        schemas.KNOWN_METRICS = [*orig[0], "deployment_count"]
        try:
            t = normalize_review_trigger(LEGACY_DEPLOY)
            self.assertEqual(t.metric_key, "deployment_count")
        finally:
            schemas.KNOWN_METRICS = orig[0]


class ViewTest(unittest.TestCase):
    def test_connected_evaluates_active_and_review_due(self):
        d = _decision(CONNECTED, {"type": "metric", "metric": "user_count", "operator": ">", "threshold": 1000})
        for value, expected in ((900, "ACTIVE"), (1400, "REVIEW_DUE")):
            v = to_view(d, {"user_count": _reading("user_count", value)}, {})
            self.assertEqual((v.display_status, v.trigger_status.mapping_status, v.trigger_status.evaluation_status),
                             (expected, "CONNECTED", expected))
            self.assertEqual(v.trigger_status.data_source, "sqlite:users")

    def test_unknown_is_connected_but_unreadable(self):
        d = _decision(CONNECTED, {"type": "metric", "metric": "user_count", "operator": ">", "threshold": 1000})
        v = to_view(d, {}, {"user_count": metric_service.MetricError("unavailable", "check_metric 서버 꺼짐")})
        self.assertEqual((v.display_status, v.trigger_status.mapping_status, v.trigger_status.evaluation_status),
                         ("UNKNOWN", "CONNECTED", "UNKNOWN"))

    def test_needs_mapping_is_not_unknown(self):
        v = to_view(_decision(DEPLOY, LEGACY_DEPLOY), {}, {})
        self.assertEqual(v.display_status, "ACTIVE")  # 예전에는 UNKNOWN
        self.assertIsNone(v.evaluation)
        self.assertEqual((v.trigger_status.mapping_status, v.trigger_status.evaluation_status), ("NEEDS_MAPPING", None))
        self.assertEqual(v.trigger_status.reason, "현재 Decision Pulse에 연결된 데이터 소스가 없습니다.")

    def test_project_without_source_is_needs_mapping(self):
        d = _decision(CONNECTED, {"type": "metric", "metric": "user_count", "operator": ">", "threshold": 1000})
        v = to_view(d, {}, {"user_count": metric_service.MetricError("not_connected", "Metric not connected")})
        self.assertEqual((v.display_status, v.trigger_status.mapping_status), ("ACTIVE", "NEEDS_MAPPING"))

    def test_manual_view(self):
        v = to_view(_decision(MANUAL, {"type": "manual", "description": "고객 불만이 많아지면 다시 검토"}), {}, {})
        self.assertEqual((v.display_status, v.trigger_status.mapping_status), ("ACTIVE", "MANUAL"))


class AdapterTest(unittest.TestCase):
    def test_resolve_skips_metrics_without_adapter(self):
        calls = []
        orig = metric_service.check_metrics

        async def fake(project, names):
            calls.append(list(names))
            return {n: _reading(n, 1) for n in names}, {}

        metric_service.check_metrics = fake
        try:
            found, errors = asyncio.run(metric_adapters.resolve_metrics(object(), ["user_count", "deployment_count", "nope"]))
        finally:
            metric_service.check_metrics = orig
        self.assertEqual(calls, [["user_count"]])  # 연결 안 된 지표는 data source를 호출하지 않는다
        self.assertEqual(set(found), {"user_count"})
        self.assertEqual({k: e.reason for k, e in errors.items()}, {"deployment_count": "not_found", "nope": "not_found"})


class ExtractionTest(unittest.TestCase):
    def test_unmapped_metric_kept_as_needs_mapping(self):
        t, problems, _ = validate_trigger({"type": "metric", "raw_metric": "프로덕션 배포 횟수", "operator": ">", "threshold": 5,
                                           "unit": "회"}, "프로덕션 배포가 일주일에 5회를 넘으면 자동 배포를 다시 검토", KNOWN_METRICS)
        self.assertEqual(problems, [])  # 실패로 처리하지 않는다
        self.assertEqual((t["type"], t["metric_key"], t["canonical_metric"], t["window"]), ("metric", None, "deployment_count", "7d"))

    def test_subjective_metric_demoted_to_manual(self):
        t, problems, notes = validate_trigger({"type": "metric", "raw_metric": "고객 불만 건수", "operator": ">", "threshold": 10,
                                               "unit": "건"}, "고객 불만이 10건을 넘으면 다시 검토", KNOWN_METRICS)
        self.assertEqual((t["type"], problems), ("manual", []))
        self.assertIn("manual", notes[0])


class MonitorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = (db.DB_PATH, metric_service.check_metrics)
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.READ_ONLY = False
        db.init_db()
        with db.connect() as conn:
            for did, trig in ((CONNECTED, {"type": "metric", "metric": "user_count", "operator": ">", "threshold": 1000}),
                              (DEPLOY, LEGACY_DEPLOY),
                              (MANUAL, {"type": "manual", "description": "고객 불만이 많아지면 다시 검토"})):
                db.insert_decision(conn, _decision(did, trig))
        cls.sent = []

        async def no_telegram(decision, view):
            cls.sent.append(decision.id)

        cls._orig_notify = review_monitor._notify_review_due
        review_monitor._notify_review_due = no_telegram

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH, metric_service.check_metrics = cls._orig
        review_monitor._notify_review_due = cls._orig_notify
        cls._tmp.cleanup()

    def _cycle(self, users):
        self.asked = []

        async def check(_project, names):
            self.asked.append(sorted(names))
            values = {"user_count": users, "search_api_p95_ms": 500, "server_count": 1}
            return {n: _reading(n, values[n]) for n in names if n in values}, {}

        metric_service.check_metrics = check
        return asyncio.run(review_monitor.check_project(db.get_project(PID)))

    def _events(self, did):
        return [e for e in db.list_review_events(PID, limit=500) if e.decision_id == did]

    def test_only_connected_is_monitored(self):
        self._cycle(900)
        self._cycle(1400)  # ACTIVE → REVIEW_DUE
        self._cycle(900)  # 복구
        self.assertTrue(all("deployment_count" not in a for a in self.asked))
        self.assertEqual([e.new_status for e in reversed(self._events(CONNECTED))], ["REVIEW_DUE", "ACTIVE"])  # 최신순 → 시간순
        self.assertEqual(self._events(DEPLOY), [])  # NEEDS_MAPPING: 평가·이벤트 없음
        self.assertEqual(self._events(MANUAL), [])  # MANUAL: 평가·이벤트 없음
        self.assertNotIn(DEPLOY, self.sent)
        states = db.get_review_states(PID)
        self.assertNotIn(DEPLOY, states)
        self.assertNotIn(MANUAL, states)
        with sqlite3.connect(db.DB_PATH) as conn:  # Decision 자체는 바뀌지 않는다
            raw = conn.execute("SELECT review_trigger_json FROM decisions WHERE id = ?", (DEPLOY,)).fetchone()[0]
        self.assertIn('"metric_key": null', raw.replace('"metric_key":null', '"metric_key": null'))


if __name__ == "__main__":
    unittest.main()
