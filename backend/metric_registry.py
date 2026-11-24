"""Canonical Metric Registry — Review Trigger와 실제 데이터 소스를 분리한다.

    Decision → Review Trigger → Canonical Metric → (resolver = Data Source Adapter) → Observed Value

- 이 파일은 순수 데이터 + 함수만 둔다 (config / schemas / services를 import하지 않는다 → 어디서나 import 가능).
- MetricDefinition.resolver가 있으면 자동 평가 가능한 지표(CONNECTED 후보), 없으면 '측정 가능하지만 아직 연결 안 된' 지표
  (NEEDS_MAPPING). 새 지표가 회의록에 나오면 resolver 없이 여기에 정의만 추가해도 Decision은 정상 저장·관리된다.
- resolver 이름은 services/metric_adapters.py의 ADAPTERS 키다. 새 data source(GitHub Actions, Prometheus …)를 붙일 때는
  adapter를 구현해 ADAPTERS에 등록하고, 지표의 resolver를 그 이름으로 바꾸면 된다 (Decision 데이터는 바꾸지 않는다).
- alias 매핑은 LLM이 아니라 이 목록의 '완전 일치'(공백/대소문자/괄호/밑줄 무시)로만 한다. 부분 일치는 하지 않는다
  ("동시 사용자 수"는 user_count가 아니다). 단위가 주어지면 units와도 맞아야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDefinition:
    name: str  # canonical metric 이름 (Review Trigger의 metric_key / canonical_metric)
    label: str  # 화면 표시용
    aliases: tuple[str, ...]  # 원문 표현 (완전 일치)
    units: tuple[str, ...] = ()  # 허용 단위 (원문 단위가 있으면 이 중 하나여야 매핑)
    unit: str | None = None  # 표시 단위
    resolver: str | None = None  # data source adapter 이름. None = 아직 연결된 source 없음 (NEEDS_MAPPING)
    source: str | None = None  # resolver가 읽는 위치 (표시용. 예: sqlite:users)
    examples: tuple[str, ...] = field(default_factory=tuple)


REGISTRY: tuple[MetricDefinition, ...] = (
    # ---- 연결됨: check_metric 서버(~/decision-debt-tool)의 기존 resolver를 그대로 쓴다
    MetricDefinition(
        name="user_count", label="사용자 수", unit="users",
        aliases=(
            "사용자 수", "가입자 수", "회원 수", "유저 수", "현재 사용자 수", "현재 가입자 수", "현재 회원 수", "전체 사용자 수",
            "전체 가입자 수", "누적 가입자 수", "실제 사용자 수", "사용자", "가입자", "회원", "user count", "users",
        ),
        units=("명", "users", "user", "people"),
        resolver="check_metric", source="sqlite:users",
    ),
    MetricDefinition(
        name="search_api_p95_ms", label="검색 API p95 응답 시간", unit="ms",
        aliases=(
            "검색 API p95", "검색 API p95 응답시간", "검색 API p95 응답 시간", "검색 API p95 latency",
            "검색 API의 p95", "검색 API의 p95 응답시간", "검색 API의 p95 응답 시간", "검색 API의 p95 latency",
            "검색 API 응답시간 p95", "검색 API 응답 시간 p95", "검색 API의 응답시간 p95", "검색 p95", "검색 p95 응답시간",
        ),
        units=("ms", "밀리초", "millisecond", "milliseconds"),
        resolver="check_metric", source="metrics_store:search_api_p95_ms",
    ),
    MetricDefinition(
        name="server_count", label="운영 서버 수", unit="servers",
        aliases=(
            "서버 수", "서버 대수", "운영 서버 수", "운영 서버 대수", "프로덕션 서버 수", "프로덕션 서버 대수",
            "운영 서버", "서버", "server count", "servers",
        ),
        units=("대", "개", "servers", "server", "units"),
        resolver="check_metric", source="metrics_store:server_count",
    ),
    # ---- 측정 가능하지만 아직 연결된 data source 없음 → NEEDS_MAPPING (resolver를 붙이면 자동으로 CONNECTED)
    MetricDefinition(
        name="deployment_count", label="프로덕션 배포 횟수", unit="회",
        aliases=(
            "배포 횟수", "프로덕션 배포 횟수", "운영 배포 횟수", "주간 배포 횟수", "주간 운영 배포 횟수", "주간 프로덕션 배포 횟수",
            "일주일 프로덕션 배포 횟수", "릴리즈 횟수", "릴리스 횟수", "릴리즈 빈도", "릴리스 빈도", "배포 빈도", "프로덕션 배포",
            "deployment count", "deployments",
        ),
        units=("회", "번", "건", "times", "deployments"),
        examples=("GitHub Actions workflow runs", "deploy 이력 테이블"),
    ),
    MetricDefinition(
        name="error_rate", label="API 오류율", unit="%",
        aliases=("오류율", "에러율", "API 오류율", "API 에러율", "error rate", "5xx 비율"),
        units=("%", "퍼센트", "percent"),
        examples=("Prometheus", "APM"),
    ),
    MetricDefinition(
        name="monthly_revenue", label="월 매출", unit="원",
        aliases=("월 매출", "월매출", "월간 매출", "MRR", "monthly revenue"),
        units=("원", "만원", "억", "달러", "USD", "KRW"),
        examples=("Stripe", "결제 DB"),
    ),
    MetricDefinition(
        name="failed_job_count", label="실패한 작업 수", unit="건",
        aliases=("실패한 작업 수", "실패 작업 수", "실패한 job 수", "job 실패 횟수", "배치 실패 횟수", "실패한 배치 수", "failed jobs"),
        units=("건", "회", "번", "개"),
        examples=("job queue", "batch 로그"),
    ),
    MetricDefinition(
        name="daily_log_volume_gb", label="일일 로그 발생량", unit="GB",
        aliases=("일일 로그 발생량", "로그 발생량", "하루 로그 양", "일일 로그량", "daily log volume"),
        units=("GB", "기가바이트", "gigabytes"),
        examples=("로그 수집기", "디스크 사용량"),
    ),
)

_BY_NAME = {d.name: d for d in REGISTRY}


def norm(text: str) -> str:
    """alias 비교용: 소문자 + 공백/밑줄/괄호/하이픈 제거"""
    return re.sub(r"[\s_()\-]", "", (text or "").lower())


_ALIASES = {d.name: {norm(a) for a in (*d.aliases, d.name)} for d in REGISTRY}
_UNITS = {d.name: {norm(u) for u in d.units} for d in REGISTRY}


def get(name: str | None) -> MetricDefinition | None:
    return _BY_NAME.get(name) if name else None


def all_names() -> list[str]:
    return [d.name for d in REGISTRY]


def connected_names() -> list[str]:
    """resolver가 있는 지표 = 자동 평가 가능 (기존 KNOWN_METRICS)"""
    return [d.name for d in REGISTRY if d.resolver]


def canonicalize(raw_metric: str | None, unit: str | None = None, names: list[str] | None = None) -> str | None:
    """원문 지표 표현 → canonical metric. alias 완전 일치 + 단위 호환일 때만. 후보가 하나가 아니면 None (추측하지 않음)."""
    if not raw_metric:
        return None
    name, unit_key = norm(raw_metric), norm(unit or "")
    keys = [
        k for k in (names if names is not None else all_names())
        if name in _ALIASES.get(k, set()) and (not unit_key or unit_key in _UNITS.get(k, set()))
    ]
    return keys[0] if len(keys) == 1 else None


def aliases_by_metric(names: list[str] | None = None) -> dict[str, list[str]]:
    return {d.name: list(d.aliases) for d in REGISTRY if names is None or d.name in names}


def units_by_metric(names: list[str] | None = None) -> dict[str, list[str]]:
    return {d.name: list(d.units) for d in REGISTRY if names is None or d.name in names}


# ---- MANUAL 판정: 객관적인 자동 측정값으로 표현하기 어려운 조건 (사람의 인식/판단/전략에 기대는 표현)
_SUBJECTIVE = re.compile(
    r"불만|불편|부담|만족|체감|느끼|느껴|판단|생각|분위기|의견|평가|우려|전략|방향|요구사항|요구 사항|중요해지|애매|어렵다고|힘들"
)


def is_subjective(*texts: str | None) -> bool:
    return any(_SUBJECTIVE.search(t) for t in texts if t)


# ---- 집계 기간 (window): 원문에서만 읽는다. 예) "일주일에 5회" → 7d
_WINDOWS = (
    (re.compile(r"일주일|한\s*주|주간|매주|주당|1\s*주(?:일)?\s*(?:에|동안|간)"), "7d"),
    (re.compile(r"하루에|하루\s*동안|일일|매일|1\s*일\s*(?:에|동안)"), "1d"),
    (re.compile(r"한\s*달|월간|매월|월\s*\d|1\s*개월"), "30d"),
)
WINDOW_LABELS = {"1d": "1일", "7d": "7일", "30d": "30일"}


def infer_window(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        for pattern, window in _WINDOWS:
            if pattern.search(text):
                return window
    return None
