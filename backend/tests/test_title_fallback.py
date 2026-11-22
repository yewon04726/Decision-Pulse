"""Stage 1 title 정책: title 검증 실패는 재시도하지 않고 decision 문장으로 대체한다 (LLM 호출 없음, 가짜 응답 사용).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_title_fallback -v
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import extraction  # noqa: E402

SAML = "MVP 단계에서는 SAML이나 사내 IdP 연동 기능을 추가하지 않는다."
REDIS = "MVP에서는 Redis를 도입하지 않는다."


def run_stage1(content: str, items: list[dict]) -> tuple[list[dict], list[str], dict, int]:
    """_call_llm을 가짜로 바꿔 Stage 1만 실행. (항목, warnings, stats, Stage 1 호출 수)"""
    calls = {"n": 0}

    async def fake_call(llm, messages, schema, name, temperature):
        calls["n"] += 1
        return {"items": items}

    original = extraction._call_llm
    extraction._call_llm = fake_call
    try:
        llm = extraction.LLMRoute("local", "http://unused", "fake", client=None)
        kept, _dropped, warnings = asyncio.run(extraction._stage1(llm, content))
    finally:
        extraction._call_llm = original
    return kept, warnings, llm.stats, calls["n"]


def item(decision: str, title: str) -> dict:
    return {
        "topic": "auth", "decision": decision, "rationale": "", "review_condition": "",
        "evidence": [decision], "decision_status": "confirmed", "title": title, "assumptions": [],
    }


class TitleFallbackTest(unittest.TestCase):
    def test_weak_title_on_negative_decision_falls_back_without_retry(self):
        kept, warnings, stats, n = run_stage1(SAML, [item(SAML, "기업 로그인 검토")])
        self.assertEqual(n, 1)
        self.assertEqual(stats["retries"]["stage1"], 0)
        self.assertEqual(kept[0]["title"], SAML.rstrip("."))
        self.assertEqual(stats.get("title_fallbacks"), 1)
        self.assertTrue(any(w.startswith(extraction.TITLE_FALLBACK_WARNING) for w in warnings))

    def test_valid_title_is_kept(self):
        kept, warnings, stats, n = run_stage1(REDIS, [item(REDIS, "Redis 미도입")])
        self.assertEqual((n, stats["retries"]["stage1"]), (1, 0))
        self.assertEqual(kept[0]["title"], "Redis 미도입")
        self.assertNotIn("title_fallbacks", stats)
        self.assertFalse(any(w.startswith(extraction.TITLE_FALLBACK_WARNING) for w in warnings))

    def test_empty_title_falls_back_without_retry(self):
        kept, warnings, stats, n = run_stage1(REDIS, [item(REDIS, "")])
        self.assertEqual((n, stats["retries"]["stage1"]), (1, 0))
        self.assertEqual(kept[0]["title"], REDIS.rstrip("."))
        self.assertTrue(any("(empty)" in w for w in warnings))

    def test_core_field_han_leak_still_retries(self):
        """핵심 필드(decision)의 중국어 혼입은 기존처럼 재시도한다"""
        leaked = item(REDIS.replace("도입", "導入"), "Redis 미도입")
        with self.assertRaises(extraction.ExtractionError):
            run_stage1(REDIS, [leaked])  # 매번 같은 응답 → MAX_ATTEMPTS 후 오류


if __name__ == "__main__":
    unittest.main()
