"""Metric 조회. 기존 check_metric 서버(~/decision-debt-tool, GET /metrics/{metric_name}) 클라이언트.

Dify Agent가 호출하는 것과 같은 서버를 호출하므로, 웹과 Agent가 항상 같은 현재값을 본다.
check_metric은 project_id를 받지 않으므로 '기본 프로젝트의 실제 metric'으로만 취급한다.
metric source가 연결되지 않은 프로젝트는 서버를 호출하지 않고 not_connected로 처리한다
(다른 프로젝트의 값을 그 프로젝트의 값처럼 보여주지 않기 위해).
"""

import asyncio
from dataclasses import dataclass

import httpx

from config import METRIC_SOURCES, METRIC_TOOL_TIMEOUT
from schemas import Metric, Project


class MetricToolUnavailable(Exception):
    """check_metric 서버에 연결할 수 없음 (서버가 꺼져 있거나 응답 없음)"""


class MetricNotFound(Exception):
    """check_metric 서버가 404를 반환 (등록되지 않은 지표)"""


class MetricNotConnected(Exception):
    """프로젝트에 metric source가 연결되지 않음"""


@dataclass
class MetricError:
    reason: str  # unavailable | not_found | not_connected
    message: str


def metric_base_url(project: Project) -> str | None:
    return METRIC_SOURCES.get(project.metric_source) if project.metric_source else None


async def _fetch(client: httpx.AsyncClient, base_url: str, metric_name: str) -> Metric:
    try:
        res = await client.get(f"{base_url}/metrics/{metric_name}")
    except httpx.TransportError as e:
        raise MetricToolUnavailable(f"check_metric 서버({base_url})에 연결할 수 없습니다: {type(e).__name__}") from e
    if res.status_code == 404:
        raise MetricNotFound(res.json().get("detail", f"Unknown metric: {metric_name}"))
    if res.status_code == 503:  # 지표는 등록됐지만 source(DB, monitoring)를 읽을 수 없음 → 판정 불가
        body = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        raise MetricToolUnavailable(f"{metric_name} source를 읽을 수 없습니다: {body.get('reason', 'HTTP 503')}")
    res.raise_for_status()
    return Metric(**res.json())


async def check_metric(project: Project, metric_name: str) -> Metric:
    base_url = metric_base_url(project)
    if base_url is None:
        raise MetricNotConnected(f"'{project.name}' 프로젝트에는 metric source가 연결되지 않았습니다.")
    async with httpx.AsyncClient(timeout=METRIC_TOOL_TIMEOUT) as client:
        return await _fetch(client, base_url, metric_name)


async def check_metrics(project: Project, names: list[str]) -> tuple[dict[str, Metric], dict[str, MetricError]]:
    """여러 지표를 동시에 조회. (성공한 지표, 실패한 지표의 에러)"""
    base_url = metric_base_url(project)
    if base_url is None:
        msg = "Metric not connected — 이 프로젝트에는 metric source가 연결되지 않았습니다."
        return {}, {n: MetricError("not_connected", msg) for n in names}
    return await check_metrics_at(base_url, names)


async def check_metrics_at(base_url: str, names: list[str]) -> tuple[dict[str, Metric], dict[str, MetricError]]:
    """metric source URL을 직접 지정해 조회 (Demo Control Center가 source 단위로 현재값을 읽을 때)"""
    async with httpx.AsyncClient(timeout=METRIC_TOOL_TIMEOUT) as client:
        results = await asyncio.gather(*(_fetch(client, base_url, n) for n in names), return_exceptions=True)
    metrics: dict[str, Metric] = {}
    errors: dict[str, MetricError] = {}
    for name, result in zip(names, results):
        if isinstance(result, Metric):
            metrics[name] = result
        elif isinstance(result, MetricNotFound):
            errors[name] = MetricError("not_found", str(result))
        else:
            errors[name] = MetricError("unavailable", str(result) or type(result).__name__)
    return metrics, errors


async def ping(base_url: str) -> tuple[bool, str | None]:
    async with httpx.AsyncClient(timeout=METRIC_TOOL_TIMEOUT) as client:
        try:
            res = await client.get(f"{base_url}/openapi.json")
            return res.is_success, None if res.is_success else f"HTTP {res.status_code}"
        except httpx.TransportError as e:
            return False, type(e).__name__
