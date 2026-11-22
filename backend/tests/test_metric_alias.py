"""metric alias 완전 일치 매핑 (LLM 호출 없음).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_metric_alias -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import KNOWN_METRICS  # noqa: E402
from services.extraction import map_metric_key, validate_trigger  # noqa: E402


def structure(raw_metric: str, threshold: float, unit: str | None, condition: str) -> dict:
    trigger, problems, _ = validate_trigger(
        {"type": "metric", "raw_metric": raw_metric, "operator": ">", "threshold": threshold, "unit": unit or ""},
        condition, KNOWN_METRICS,
    )
    assert problems == [], problems
    return trigger


class MetricAliasTest(unittest.TestCase):
    def test_actual_user_count_maps_to_user_count(self):
        self.assertEqual(map_metric_key("실제 사용자 수", None, KNOWN_METRICS), "user_count")
        t = structure("실제 사용자 수", 1000, None, "네, 그러면 실제 사용자 수가 1,000명을 넘으면 프로그레스 SQL로 전환을 다시 보는 걸로 하죠.")
        self.assertEqual((t["metric_key"], t["needs_mapping"], t["operator"], t["threshold"]), ("user_count", False, ">", 1000))

    def test_production_deploy_count_stays_unmapped(self):
        self.assertIsNone(map_metric_key("프로덕션 배포 횟수", None, KNOWN_METRICS))
        t = structure("프로덕션 배포 횟수", 5, "회", "일주일에 프로덕션 배포가 다섯 번을 넘으면 자동 배포를 다시 검토한다.")
        self.assertEqual((t["metric_key"], t["needs_mapping"]), (None, True))

    def test_exact_match_only(self):
        """부분 일치로 넓히지 않는다"""
        for raw in ("실제 동시 사용자 수", "실제 사용자 수 증가율", "동시 사용자 수"):
            self.assertIsNone(map_metric_key(raw, None, KNOWN_METRICS), raw)


if __name__ == "__main__":
    unittest.main()
