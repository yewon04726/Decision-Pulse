"""Qwen 출력의 중국어 혼입 처리 (Extraction / Ask Agent 공통).

Qwen2.5는 한국어 문서를 다룰 때 중국어 문장부호(。，：)나 한자 단어, 때로는 답변 전체를 중국어로 낸다.
- 문장부호: 의미가 같은 일반 기호로 바꾼다 (글자는 바꾸지 않음).
- 한자: 지우면 문장이 깨지므로 지우지 않고 '원문/근거에 없는 한자가 섞였는지' 감지만 한다.
  호출하는 쪽에서 재생성하거나 저장/표시하지 않는다.
"""

import re

# 전각 ASCII 영역(！ ～ ～)은 코드 차이로 일괄 변환, 나머지 CJK 문장부호는 개별 매핑
_CJK_PUNCT = {
    "　": " ",  # 전각 공백
    "。": ".",
    "、": ",",
    "「": '"',
    "」": '"',
    "『": '"',
    "』": '"',
    "【": "[",
    "】": "]",
    "〈": "<",
    "〉": ">",
    "《": "<",
    "》": ">",
    "〔": "(",
    "〕": ")",
}
_FULLWIDTH = {chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}
_PUNCT_TABLE = str.maketrans({**_FULLWIDTH, **_CJK_PUNCT})

# CJK 통합 한자 + 확장 A + 호환 한자 (한글은 포함하지 않음)
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def normalize_cjk_punct(text: str) -> str:
    """중국어/전각 문장부호 → 일반 기호. 한글·한자 등 글자는 그대로 둔다."""
    return text.translate(_PUNCT_TABLE)


def foreign_han(text: str, allowed: str = "") -> set[str]:
    """text에 있는 한자 중 allowed(원문, 근거 문서 등)에 없는 것. 비어 있으면 중국어 혼입 없음."""
    return set(_HAN.findall(text)) - set(_HAN.findall(allowed))
