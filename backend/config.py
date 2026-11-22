import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


# backend/.env에서 읽은 변수 이름. 외부 CLI(gbrain 등)를 실행할 때 이 값(API Key 등)을 넘기지 않기 위해 기록한다
BACKEND_ENV_KEYS: set[str] = set()


def _load_env_file(path: Path):
    """backend/.env 로드 (이미 설정된 환경변수가 우선)"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            BACKEND_ENV_KEYS.add(key.strip())
            os.environ.setdefault(key.strip(), value.strip())


_load_env_file(BASE_DIR / ".env")

# Dify Chatflow 앱 (API Key는 backend에만 둔다. 프론트에 노출 금지)
DIFY_BASE_URL = os.environ.get("DIFY_BASE_URL", "http://localhost/v1").rstrip("/")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")
DIFY_TIMEOUT = float(os.environ.get("DIFY_TIMEOUT", "180"))

# Dify Knowledge(Dataset) API — 승인된 Decision을 Knowledge 문서로 동기화할 때 사용.
# Chatflow 앱 키(DIFY_API_KEY)와는 다른 Knowledge API 키(dataset-...)를 쓴다. 값은 .env에만 둔다.
DIFY_API_BASE = os.environ.get("DIFY_API_BASE", "").rstrip("/")
DIFY_KNOWLEDGE_API_KEY = os.environ.get("DIFY_KNOWLEDGE_API_KEY", "")
DIFY_DATASET_ID = os.environ.get("DIFY_DATASET_ID", "")
DIFY_KNOWLEDGE_TIMEOUT = float(os.environ.get("DIFY_KNOWLEDGE_TIMEOUT", "30"))

# 기존 ~/decision-debt-tool (Dify Agent의 check_metric 도구와 같은 서버). 값을 복사하지 않고 호출만 한다.
METRIC_TOOL_URL = os.environ.get("METRIC_TOOL_URL", "http://localhost:8000").rstrip("/")
METRIC_TOOL_TIMEOUT = float(os.environ.get("METRIC_TOOL_TIMEOUT", "3"))

# check_metric 서버에 목록 API가 없으므로 조회 대상 지표를 여기서 관리한다.
KNOWN_METRICS = ["user_count", "search_api_p95_ms", "server_count"]

# Decision Extraction: 원문의 지표 표현(raw_metric) → 등록된 지표 키. LLM이 아니라 backend 코드가 매핑한다.
# 공백/대소문자/괄호를 무시한 '완전 일치'만 매핑한다 (부분 일치 X: "동시 사용자 수"는 user_count가 아니다).
# 여기에 없는 표현은 metric_key=null, needs_mapping=true로 남는다. 새 표현은 이 목록에 추가한다.
METRIC_ALIASES = {
    "user_count": [
        "사용자 수", "가입자 수", "회원 수", "유저 수", "현재 사용자 수", "현재 가입자 수", "현재 회원 수", "전체 사용자 수",
        "전체 가입자 수", "누적 가입자 수", "실제 사용자 수", "사용자", "가입자", "회원", "user count", "users",
    ],
    "search_api_p95_ms": [
        "검색 API p95", "검색 API p95 응답시간", "검색 API p95 응답 시간", "검색 API p95 latency",
        "검색 API의 p95", "검색 API의 p95 응답시간", "검색 API의 p95 응답 시간", "검색 API의 p95 latency",
        "검색 API 응답시간 p95", "검색 API 응답 시간 p95", "검색 API의 응답시간 p95", "검색 p95", "검색 p95 응답시간",
    ],
    "server_count": [
        "서버 수", "서버 대수", "운영 서버 수", "운영 서버 대수", "프로덕션 서버 수", "프로덕션 서버 대수",
        "운영 서버", "서버", "server count", "servers",
    ],
}
# 지표별로 허용하는 단위. raw_metric이 일치해도 단위가 다르면 매핑하지 않는다 (예: "서버" + GB)
METRIC_UNITS = {
    "user_count": ["명", "users", "user", "people"],
    "search_api_p95_ms": ["ms", "밀리초", "millisecond", "milliseconds"],
    "server_count": ["대", "개", "servers", "server", "units"],
}

# Decision Extraction: llama-swap의 OpenAI 호환 API (설정은 변경하지 않고 호출만)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8080/v1").rstrip("/")
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "qwen2.5-7b-instruct")
EXTRACTION_TIMEOUT = float(os.environ.get("EXTRACTION_TIMEOUT", "180"))
# 웹 Extract 기본 mode. local: Qwen만 / auto: Qwen 먼저, 구조화 실패일 때만 OpenRouter(optimized)로 다시 추출
EXTRACTION_MODE = os.environ.get("EXTRACTION_MODE", "local").strip().lower() or "local"

# 비교 실험용 extraction provider (OpenRouter, 유료). 웹 API는 항상 local을 쓰고,
# scripts/run_extraction_comparison.py에서 명시적으로 고를 때만 사용한다. 값이 없어도 local MVP는 동작한다.
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "")

# OpenRouter 'optimized' profile 옵션 (비교 script에서 --profile optimized일 때만 적용. local 요청에는 절대 넣지 않는다)
# 비우면 해당 옵션을 요청에 넣지 않는다 (= OpenRouter/모델 기본 동작)
OPENROUTER_REASONING_EFFORT = os.environ.get("OPENROUTER_REASONING_EFFORT", "").strip()  # 예: low
OPENROUTER_PROVIDER_SORT = os.environ.get("OPENROUTER_PROVIDER_SORT", "").strip()  # 예: throughput | latency | price
OPENROUTER_REQUIRE_PARAMETERS = os.environ.get("OPENROUTER_REQUIRE_PARAMETERS", "true").strip().lower() in ("1", "true", "yes", "on")
# Stage별 출력 token 상한 (reasoning token 포함). 비우면 상한 없음. 너무 작으면 JSON이 잘린다 → 결과에 truncated로 기록
OPENROUTER_STAGE1_MAX_TOKENS = int(os.environ.get("OPENROUTER_STAGE1_MAX_TOKENS") or 0) or None
OPENROUTER_STAGE2_MAX_TOKENS = int(os.environ.get("OPENROUTER_STAGE2_MAX_TOKENS") or 0) or None

# 기존 데이터(seed, Dify Knowledge v4, check_metric)가 속하는 기본 프로젝트
DEFAULT_PROJECT = {
    "name": "Decision Debt MVP",
    "slug": "decision-debt",
    "description": "Decision Debt Agent MVP",
}

# 프로젝트별 연결 설정 값. DB에는 비밀값 대신 이 식별자만 저장한다.
# 추후 프로젝트별 metric API / Dify 앱을 붙일 때 여기에 항목을 추가한다.
METRIC_SOURCES = {"check_metric_default": METRIC_TOOL_URL}
AGENT_SOURCES = {"dify_default": {"base_url": DIFY_BASE_URL, "api_key": DIFY_API_KEY}}
# 승인된 Decision을 보낼 Knowledge. projects.agent_source 키로 찾는다 → 그 프로젝트의 Agent가 읽는 Knowledge에만 들어간다.
# (dify_default = Decision Debt MVP의 Agent가 쓰는 "Decision Debt Knowledge v4")
KNOWLEDGE_TARGETS = {
    "dify_default": {"api_base": DIFY_API_BASE, "api_key": DIFY_KNOWLEDGE_API_KEY, "dataset_id": DIFY_DATASET_ID},
}

# Audio Source: 로컬 STT(whisper.cpp)만 쓴다. 오디오/전사는 외부로 보내지 않는다
AUDIO_DIR = Path(os.environ.get("AUDIO_DIR", BASE_DIR / "data" / "audio")).expanduser()
AUDIO_MAX_UPLOAD_MB = float(os.environ.get("AUDIO_MAX_UPLOAD_MB", "200"))
AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a")
WHISPER_CLI = os.environ.get("WHISPER_CLI", "whisper-cli")
WHISPER_MODEL_PATH = Path(
    os.environ.get("WHISPER_MODEL_PATH", Path.home() / "models" / "whisper" / "ggml-large-v3-turbo-q5_0.bin")
).expanduser()
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "ko")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
STT_TIMEOUT_SECONDS = float(os.environ.get("STT_TIMEOUT_SECONDS", "1800"))

# gbrain: 승인된(human_verified) Decision을 Markdown으로 내보내 gbrain에 import (검색·종합용 파생 knowledge layer).
# SQLite가 원본이다. gbrain 실패는 승인에 영향을 주지 않는다. embedding은 gbrain이 담당 (backend는 호출하지 않음)
GBRAIN_SYNC_ENABLED = os.environ.get("GBRAIN_SYNC_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
GBRAIN_DECISIONS_DIR = Path(
    os.environ.get("GBRAIN_DECISIONS_DIR", Path.home() / "decision-debt-brain" / "decisions")
).expanduser()
GBRAIN_BIN = os.environ.get("GBRAIN_BIN", "gbrain")
# gbrain source(git working tree) — export한 Markdown을 `gbrain sync --working-tree`로 반영한다.
# gbrain serve(Hermes gateway의 MCP)가 PGLite를 잡고 있으면 sync가 그 owner에게 위임된다 (import는 lock 충돌)
GBRAIN_SOURCE_ID = os.environ.get("GBRAIN_SOURCE_ID", "decision-debt").strip() or "decision-debt"
GBRAIN_REPO_DIR = Path(os.environ.get("GBRAIN_REPO_DIR", Path.home() / "decision-debt-brain")).expanduser()
GBRAIN_SYNC_TIMEOUT_SECONDS = float(os.environ.get("GBRAIN_SYNC_TIMEOUT_SECONDS", "60"))

# Review Memo: REVIEW_DUE 결정에 대해 Hermes(로컬 CLI, -z oneshot)가 Decision Debt read-only MCP + gbrain MCP만으로
# 검토 메모를 만든다 (advisory. Decision/review 상태는 바꾸지 않는다). Hermes의 model/provider는 Hermes 설정을 따른다.
HERMES_REVIEW_MEMO_ENABLED = os.environ.get("HERMES_REVIEW_MEMO_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")
# Hermes의 state.db(session/tool 기록) 위치. tool 사용 경로 검증에 읽기 전용으로만 연다
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_REVIEW_MEMO_TIMEOUT_SECONDS = float(os.environ.get("HERMES_REVIEW_MEMO_TIMEOUT_SECONDS", "180"))
# 비우면 Hermes 설정(config.yaml)의 값을 쓴다
HERMES_REVIEW_MEMO_MODEL = os.environ.get("HERMES_REVIEW_MEMO_MODEL", "").strip()
HERMES_REVIEW_MEMO_PROVIDER = os.environ.get("HERMES_REVIEW_MEMO_PROVIDER", "").strip()
HERMES_REVIEW_MEMO_REASONING = os.environ.get("HERMES_REVIEW_MEMO_REASONING", "").strip()

# REVIEW_DUE 전환 알림 (Telegram). Hermes CLI `hermes send --to telegram`으로 보낸다 (HERMES_BIN 공용).
# Telegram token/chat id는 backend에 두지 않는다 — Hermes 설정의 자격 증명을 쓴다
TELEGRAM_REVIEW_NOTIFICATIONS_ENABLED = os.environ.get("TELEGRAM_REVIEW_NOTIFICATIONS_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS = float(os.environ.get("TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS", "15"))

# Review monitor: backend가 주기적으로 결정의 재검토 조건을 확인한다 (LLM 호출 없음: DB + check_metric + 코드 비교)
REVIEW_MONITOR_ENABLED = os.environ.get("REVIEW_MONITOR_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
REVIEW_MONITOR_INTERVAL_SECONDS = max(5.0, float(os.environ.get("REVIEW_MONITOR_INTERVAL_SECONDS", "30")))

DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "decision_debt.db"))
SEED_PATH = BASE_DIR / "seed_data.json"


# ---------------------------------------------------------------- Demo Control Center (발표/개발 전용)
# false(기본)이면 /api/demo/* 전체가 404이고 웹의 Demo 메뉴/페이지도 숨겨진다. production에서는 켜지 않는다.
DEMO_MODE_ENABLED = os.environ.get("DEMO_MODE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
# 시뮬레이터가 조작하는 metric source (= check_metric 서버). 이 source에 연결된 프로젝트의 결정만 영향을 받는다
DEMO_METRIC_SOURCE = os.environ.get("DEMO_METRIC_SOURCE", "check_metric_default")
# check_metric 서버 폴더. 값 변경은 이 폴더의 CLI script(set_user_count.py / set_metric.py)를 그대로 실행한다
DEMO_TOOL_DIR = Path(os.environ.get("DEMO_TOOL_DIR", Path.home() / "decision-debt-tool")).expanduser()
DEMO_TOOL_PYTHON = Path(os.environ.get("DEMO_TOOL_PYTHON", DEMO_TOOL_DIR / ".venv" / "bin" / "python")).expanduser()
DEMO_SCRIPT_TIMEOUT_SECONDS = float(os.environ.get("DEMO_SCRIPT_TIMEOUT_SECONDS", "30"))
DEMO_MAX_USER_COUNT = int(os.environ.get("DEMO_MAX_USER_COUNT", "100000"))  # users row insert 상한
DEMO_MAX_METRIC_VALUE = float(os.environ.get("DEMO_MAX_METRIC_VALUE", "1000000000"))

# Reset Demo State 기준값: 현재 metric trigger 전부가 미충족(ACTIVE)인 값 (2026-11-12 기준 trigger 조사)
#   user_count  > 1000, > 1300, > 2000, > 5000        → 900
#   search_api_p95_ms > 800 (+ compound > 800 AND 3일) → 500
#   server_count > 2, >= 2                            → 1
DEMO_BASELINE: dict[str, float] = {"user_count": 900, "search_api_p95_ms": 500, "server_count": 1}

# 발표 시나리오: 외부 상태값 하나만 바꾼다 (REVIEW_DUE는 Review Monitor가 판정)
DEMO_SCENARIOS: list[dict] = [
    {"id": "database-scale", "title": "Database scale trigger", "metric": "user_count", "value": 1400},
    {"id": "search-latency", "title": "Search latency trigger", "metric": "search_api_p95_ms", "value": 850},
    {"id": "infra-growth", "title": "Infrastructure growth trigger", "metric": "server_count", "value": 3},
]
