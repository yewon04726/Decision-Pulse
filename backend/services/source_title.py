"""오디오 Source 제목: 파일명(예: "신수동 2") 대신 추출된 Candidate topic으로 의미 있는 제목을 만든다.

LLM을 새로 호출하지 않는다 — Decision Extraction이 이미 만든 Candidate topic만 쓴다. 제목 생성은 deterministic.
- 대상: 오디오 Source만 (텍스트 Source 제목은 건드리지 않는다)
- 사람이 정한 제목(title_origin=user)은 덮어쓰지 않는다
- Candidate가 0개면 기존 제목을 그대로 둔다
- 같은 Source를 다시 추출해 Candidate가 중복돼도 unique topic 기준이라 제목이 같다
"""

import logging

import db

log = logging.getLogger("source_title")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [source_title] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

# topic → 화면 표시명. 없는 topic은 원래 문자열 그대로 쓴다
TOPIC_LABELS = {
    "database": "데이터베이스",
    "db": "데이터베이스",
    "search": "검색",
    "deployment": "배포",
    "deploy": "배포",
    "monitoring": "모니터링",
    "authentication": "인증",
    "auth": "인증",
    "cache": "캐시",
    "caching": "캐시",
    "storage": "스토리지",
    "logging": "로깅",
    "log": "로깅",
    "email": "이메일",
    "notification": "알림",
    "api": "API",
    "security": "보안",
    "infrastructure": "인프라",
    "architecture": "아키텍처",
    "performance": "성능",
    "queue": "큐",
    "payment": "결제",
    "frontend": "프론트엔드",
    "backend": "백엔드",
    "testing": "테스트",
}
MAX_TOPICS = 3
SEPARATOR = " · "
SUFFIX = "회의"


def topic_label(topic: str) -> str:
    key = (topic or "").strip()
    return TOPIC_LABELS.get(key.lower(), key)


def title_from_topics(topics: list[str]) -> str | None:
    """unique topic(순서 유지) → "데이터베이스 · 검색 회의". 4개 이상이면 앞 3개 + "외 N개". topic이 없으면 None."""
    labels = list(dict.fromkeys(topic_label(t) for t in topics if t and t.strip()))
    if not labels:
        return None
    head = SEPARATOR.join(labels[:MAX_TOPICS])
    rest = len(labels) - MAX_TOPICS
    return f"{head} 외 {rest}개 {SUFFIX}" if rest > 0 else f"{head} {SUFFIX}"


def update_audio_source_title(source_id: str) -> str | None:
    """추출 후 호출. 바뀐 제목(또는 None: 오디오가 아님 / user 제목 / Candidate 없음 / 이미 같음)."""
    source = db.get_source(source_id)
    if source is None or source.audio is None:
        return None
    if source.audio.title_origin == "user":
        return None
    title = title_from_topics(db.list_source_candidate_topics(source_id))
    if title is None or title == source.title:
        return None
    if db.set_source_title(source_id, title, "auto_topics"):
        log.info("%s 제목: %r → %r (topics)", source_id, source.title, title)
        return title
    return None
