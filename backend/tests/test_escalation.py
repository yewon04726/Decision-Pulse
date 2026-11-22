"""Local-first selective escalation (LLM 호출 없음: 가짜 모델 응답으로 실제 pipeline 코드를 실행).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_escalation -v
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import KNOWN_METRICS  # noqa: E402
from services import extraction  # noqa: E402

LOG_OR = "하루 로그 용량이 10GB를 넘거나 보안 감사에서 30일 보관을 요구하면 중앙 로그 저장소 도입을 재검토한다."
LOG_DOC = f"MVP에서는 stdout 로그를 유지한다.\n{LOG_OR}"
LOG_COMPOUND = {"type": "compound", "logic": "OR", "conditions": [
    {"type": "metric", "raw_metric": "하루 로그 용량", "operator": ">", "threshold": 10, "unit": "GB"},
    {"type": "event", "description": "보안 감사에서 30일 보관을 요구함"},
]}


def stage1_item(decision: str, condition: str, title: str = "결정") -> dict:
    return {
        "topic": "ops", "decision": decision, "rationale": "", "review_condition": condition,
        "evidence": [decision, condition] if condition else [decision], "decision_status": "confirmed",
        "title": title, "assumptions": [],
    }


class FakeModels:
    """provider별 가짜 응답. stage2는 호출마다 순서대로 꺼낸다. 예외 객체를 넣으면 그 예외를 낸다."""

    def __init__(self, local_stage1, local_stage2, or_stage1=None, or_stage2=None):
        self.answers = {
            ("local", "decision_sources"): [local_stage1],
            ("local", "review_trigger"): list(local_stage2),
            ("openrouter", "decision_sources"): [or_stage1] if or_stage1 is not None else [],
            ("openrouter", "review_trigger"): list(or_stage2 or []),
        }
        self.calls = {"local": 0, "openrouter": 0}

    async def __call__(self, llm, messages, schema, name, temperature):
        self.calls[llm.provider] += 1
        llm.stats["calls"][name] = llm.stats["calls"].get(name, 0) + 1
        queue = self.answers.get((llm.provider, name)) or []
        if not queue:
            raise AssertionError(f"unexpected call {llm.provider}/{name}")
        answer = queue.pop(0) if len(queue) > 1 or name == "review_trigger" else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def run_auto(content: str, fake: FakeModels, configured: bool = True):
    saved = (extraction._call_llm, extraction.OPENROUTER_API_KEY, extraction.OPENROUTER_MODEL)
    extraction._call_llm = fake
    extraction.OPENROUTER_API_KEY = "test-key" if configured else ""
    extraction.OPENROUTER_MODEL = "z-ai/glm-5.3" if configured else ""
    info: dict = {}
    try:
        drafts, warnings, extractor = asyncio.run(extraction.extract_auto(content, KNOWN_METRICS, info))
    finally:
        extraction._call_llm, extraction.OPENROUTER_API_KEY, extraction.OPENROUTER_MODEL = saved
    return drafts, warnings, extractor, info


class EscalationTest(unittest.TestCase):
    def test_semantic_manual_does_not_escalate(self):
        """D: 모델이 manual을 고른 '사람 판단' 조건 → escalation 안 함"""
        cond = "팀이 운영 복잡도가 충분히 커졌다고 판단하면 재검토한다."
        doc = f"MVP에서는 모놀리식 구조를 유지한다.\n{cond}"
        fake = FakeModels(
            {"items": [stage1_item("MVP에서는 모놀리식 구조를 유지한다.", cond, "모놀리식 유지")]},
            [{"trigger": {"type": "manual", "description": "팀이 운영 복잡도가 충분히 커졌다고 판단함"}}],
        )
        drafts, _, extractor, info = run_auto(doc, fake)
        self.assertFalse(info["escalated"])
        self.assertEqual(fake.calls["openrouter"], 0)
        self.assertEqual(drafts[0]["review_trigger"]["type"], "manual")
        self.assertTrue(extractor.startswith("local:"))

    def test_title_fallback_does_not_escalate(self):
        """E: 부정 결정 + 약한 title → title fallback만, escalation 안 함"""
        decision = "MVP 단계에서는 SAML이나 사내 IdP 연동 기능을 추가하지 않는다."
        cond = "첫 번째 유료 기업 고객이 SSO를 계약 조건으로 요구하면 SAML 또는 OIDC 기반 기업 로그인을 재검토한다."
        fake = FakeModels(
            {"items": [stage1_item(decision, cond, "기업 로그인 검토")]},
            [{"trigger": {"type": "event", "description": "첫 번째 유료 기업 고객이 SSO를 계약 조건으로 요구함"}}],
        )
        drafts, _, _, info = run_auto(f"{decision}\n{cond}", fake)
        self.assertFalse(info["escalated"])
        self.assertEqual(info["runs"]["local"]["title_fallbacks"], 1)
        self.assertEqual(drafts[0]["title"], decision.rstrip("."))
        self.assertEqual(fake.calls["openrouter"], 0)

    def test_unknown_metric_does_not_escalate(self):
        """F: 등록되지 않은 지표(needs_mapping) → escalation 안 함"""
        cond = "일주일에 프로덕션 배포 횟수가 5회를 넘으면 자동 배포를 재검토한다."
        fake = FakeModels(
            {"items": [stage1_item("MVP에서는 수동 배포를 유지한다.", cond, "수동 배포 유지")]},
            [{"trigger": {"type": "metric", "raw_metric": "일주일 프로덕션 배포 횟수", "operator": ">", "threshold": 5, "unit": "회"}}],
        )
        drafts, _, _, info = run_auto(f"MVP에서는 수동 배포를 유지한다.\n{cond}", fake)
        trigger = drafts[0]["review_trigger"]
        self.assertEqual((trigger["metric_key"], trigger["needs_mapping"]), (None, True))
        self.assertFalse(info["escalated"])
        self.assertEqual(fake.calls["openrouter"], 0)

    def test_retry_then_success_does_not_escalate(self):
        """재시도가 있었지만 최종 구조화 성공 → escalation 안 함"""
        fake = FakeModels(
            {"items": [stage1_item("MVP에서는 stdout 로그를 유지한다.", LOG_OR)]},
            [{"trigger": {"type": "event", "description": "보안 감사 요구"}}, {"trigger": LOG_COMPOUND}],
        )
        drafts, _, _, info = run_auto(LOG_DOC, fake)
        self.assertEqual(info["runs"]["local"]["retries"]["stage2"], 1)
        self.assertFalse(info["escalated"])
        self.assertEqual(drafts[0]["review_trigger"]["logic"], "OR")

    def test_structuring_failure_escalates_and_uses_openrouter(self):
        """C: local이 OR 조건을 두 번 다 단일 조건으로 냄 → manual fallback → OpenRouter compound OR 채택"""
        bad = {"trigger": {"type": "event", "description": "로그 관련 조건"}}
        fake = FakeModels(
            {"items": [stage1_item("MVP에서는 stdout 로그를 유지한다.", LOG_OR)]}, [bad, bad],
            {"items": [stage1_item("MVP에서는 stdout 로그를 유지한다.", LOG_OR, "stdout 로그 유지")]},
            [{"trigger": LOG_COMPOUND}],
        )
        drafts, warnings, extractor, info = run_auto(LOG_DOC, fake)
        self.assertTrue(info["escalated"])
        self.assertEqual(info["escalation_reasons"], ["stage2_manual_fallback_after_validation_failure"])
        self.assertEqual(info["final_provider"], "openrouter")
        self.assertEqual(extractor, "openrouter:z-ai/glm-5.3")
        self.assertEqual((drafts[0]["review_trigger"]["type"], drafts[0]["review_trigger"]["logic"]), ("compound", "OR"))
        self.assertEqual(fake.calls, {"local": 3, "openrouter": 2})

    def test_openrouter_unavailable_keeps_local_result(self):
        """OpenRouter 장애 → 예외 없이 local 결과(manual) 유지 + warning"""
        bad = {"trigger": {"type": "event", "description": "로그 관련 조건"}}
        fake = FakeModels(
            {"items": [stage1_item("MVP에서는 stdout 로그를 유지한다.", LOG_OR)]}, [bad, bad],
            extraction.ExtractionUnavailable("OpenRouter(https://openrouter.ai/api/v1)에 연결할 수 없습니다: ConnectError"),
        )
        drafts, warnings, extractor, info = run_auto(LOG_DOC, fake)
        self.assertTrue(info["escalated"])
        self.assertEqual(info["final_provider"], "local")
        self.assertTrue(extractor.startswith("local:"))
        self.assertEqual(drafts[0]["review_trigger"]["type"], "manual")
        self.assertTrue(any(w.startswith("escalation failed, kept local result") for w in warnings))

    def test_openrouter_not_configured_keeps_local_result(self):
        """OpenRouter 설정 없음 → 호출 없이 local 결과 유지 (optional dependency)"""
        bad = {"trigger": {"type": "event", "description": "로그 관련 조건"}}
        fake = FakeModels({"items": [stage1_item("MVP에서는 stdout 로그를 유지한다.", LOG_OR)]}, [bad, bad])
        drafts, warnings, _, info = run_auto(LOG_DOC, fake, configured=False)
        self.assertEqual(fake.calls["openrouter"], 0)
        self.assertEqual(info["final_provider"], "local")
        self.assertIn("OpenRouter 설정이 없습니다", info["escalation_error"])

    def test_local_server_down_is_not_escalated(self):
        """llama-swap 꺼짐은 구조화 실패가 아니다 → 외부로 보내지 않고 기존처럼 오류"""
        fake = FakeModels(extraction.ExtractionUnavailable("llama-swap(http://localhost:8080/v1)에 연결할 수 없습니다"), [])
        with self.assertRaises(extraction.ExtractionUnavailable):
            run_auto(LOG_DOC, fake)
        self.assertEqual(fake.calls["openrouter"], 0)

    def test_should_escalate_rules(self):
        ok = lambda **o: {"llm": {"stage2_outcomes": [{"status": "ok", **o}]}}  # noqa: E731
        self.assertFalse(should(ok(type="event", compound_signal=False)))
        self.assertFalse(should(ok(type="metric", compound_signal=False)))
        self.assertFalse(should(ok(type="manual", compound_signal=True)))  # 모델이 고른 manual
        self.assertTrue(should(ok(type="metric", compound_signal=True)))  # 복합 조건을 단일로 축소
        self.assertTrue(should({"llm": {"stage2_outcomes": [{"status": "manual_fallback", "cause": "llm_error"}]}}))
        self.assertTrue(should({}, extraction.ExtractionError("LLM 응답을 해석할 수 없습니다")))
        self.assertFalse(should({"llm": {"title_fallbacks": 3, "retries": {"stage1": 0, "stage2": 2}}}))


def should(stats, error=None) -> bool:
    return extraction.should_escalate(stats, error)["escalate"]


if __name__ == "__main__":
    unittest.main()
