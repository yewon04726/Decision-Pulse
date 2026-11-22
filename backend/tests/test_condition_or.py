"""validator의 OR 판단 범위: 조건절 안의 '또는/거나'만 compound OR 근거로 쓴다 (LLM 호출 없음).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_condition_or -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import KNOWN_METRICS  # noqa: E402
from services.extraction import _condition_clause, validate_trigger  # noqa: E402

OR_PROBLEM = "compound OR"


def problems(trigger: dict, condition: str) -> list[str]:
    return validate_trigger(trigger, condition, KNOWN_METRICS)[1]


class ConditionOrTest(unittest.TestCase):
    def test_action_choice_is_not_compound(self):
        """Case 1: 'SAML 또는 OIDC'는 재검토 action의 선택지 → event 하나로 통과"""
        cond = "첫 번째 유료 기업 고객이 SSO를 계약 조건으로 요구하면 SAML 또는 OIDC 기반 기업 로그인을 재검토한다."
        self.assertEqual(_condition_clause(cond), "첫 번째 유료 기업 고객이 SSO를 계약 조건으로 요구하면")
        trigger, probs, _ = validate_trigger(
            {"type": "event", "description": "첫 번째 유료 기업 고객이 SSO를 계약 조건으로 요구"}, cond, KNOWN_METRICS
        )
        self.assertEqual(probs, [])
        self.assertEqual(trigger["type"], "event")

    def test_log_volume_or_audit_requires_compound(self):
        """Case 2: 조건절 안의 '~거나' → compound OR이어야 한다"""
        cond = "하루 로그 용량이 10GB를 넘거나 보안 감사에서 30일 보관을 요구하면 재검토한다."
        single = {"type": "metric", "raw_metric": "하루 로그 용량", "operator": ">", "threshold": 10, "unit": "GB"}
        self.assertTrue(any(OR_PROBLEM in p for p in problems(single, cond)))
        compound = {"type": "compound", "logic": "OR", "conditions": [
            single, {"type": "event", "description": "보안 감사에서 30일 보관을 요구함"}]}
        trigger, probs, _ = validate_trigger(compound, cond, KNOWN_METRICS)
        self.assertEqual(probs, [])
        self.assertEqual((trigger["type"], trigger["logic"]), ("compound", "OR"))

    def test_incident_counts_or_requires_compound(self):
        """Case 3: 두 개수 조건의 '~이거나' → compound OR (>= 2, >= 3)"""
        cond = "한 달 안에 원인 파악이 어려운 장애가 2건 이상이거나 장애 분석이 30분을 넘긴 사례가 3회 이상이면 확대한다."
        first = {"type": "metric", "raw_metric": "한 달 안 원인 파악이 어려운 장애 횟수", "operator": ">=", "threshold": 2, "unit": "건"}
        second = {"type": "metric", "raw_metric": "장애 분석이 30분을 넘긴 사례 횟수", "operator": ">=", "threshold": 3, "unit": "회"}
        self.assertTrue(any(OR_PROBLEM in p for p in problems(first, cond)))
        trigger, probs, _ = validate_trigger({"type": "compound", "logic": "OR", "conditions": [first, second]}, cond, KNOWN_METRICS)
        self.assertEqual(probs, [])
        self.assertEqual([c["operator"] for c in trigger["conditions"]], [">=", ">="])

    def test_existing_multiline_or_still_compound(self):
        """기존 04 원문 형태 ('~발생하거나, ~발생하면 ~확대한다')도 OR 요구 유지"""
        cond = "한 달 안에 원인 파악이 어려운 장애가 2건 이상 발생하거나, 장애 분석에 30분을 넘긴 사례가 3회 이상 발생하면 모니터링 범위를 확대한다."
        self.assertIn("거나", _condition_clause(cond))

    def test_dasi_is_not_condition_end(self):
        """'다시 검토'의 '시'에서 조건절을 끊지 않는다"""
        cond = "고객이 SSO를 요구하면 SAML 또는 OIDC를 다시 검토한다."
        self.assertEqual(_condition_clause(cond), "고객이 SSO를 요구하면")


if __name__ == "__main__":
    unittest.main()
