"""말로 쓴 한국어 숫자 ("천 명", "다섯 번") 인식 — 회의 전사(STT) 원문에서 threshold 검증용 (LLM 호출 없음).

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_korean_numbers -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import KNOWN_METRICS  # noqa: E402
from services.extraction import _number_spans, validate_trigger  # noqa: E402


def values(text: str) -> set[float]:
    return {v for v, _ in _number_spans(text)}


class KoreanNumberTest(unittest.TestCase):
    def test_magnitude_words(self):
        self.assertIn(1000, values("사용자가 천 명을 넘으면"))
        self.assertIn(2000, values("가입자가 2천 명을 넘으면"))
        self.assertIn(3000, values("사용자가 삼천 명 이상이면"))
        self.assertIn(50000, values("월 요청이 5만 건을 넘으면"))
        self.assertIn(5, values("배포가 다섯 번을 넘으면"))  # 기존 고유어 숫자

    def test_words_that_are_not_numbers(self):
        for text in ("백업 작업이 실패하면", "만약 장애가 나면", "천천히 진행한다", "CPU, 메모리, 에러율만 수집한다"):
            self.assertEqual(values(text), set(), text)

    def test_spoken_threshold_passes_validation(self):
        """STT 전사 문장: '천 명을 넘으면' → user_count > 1000이 원문 숫자로 확인되어 통과"""
        cond = "대신 사용자가 천 명을 넘으면 포스트그레스클 전환을 다시 검토하죠."
        trigger, problems, _ = validate_trigger(
            {"type": "metric", "raw_metric": "사용자 수", "operator": ">", "threshold": 1000, "unit": "명"}, cond, KNOWN_METRICS
        )
        self.assertEqual(problems, [])
        self.assertEqual((trigger["metric_key"], trigger["operator"], trigger["threshold"]), ("user_count", ">", 1000))


if __name__ == "__main__":
    unittest.main()
