"""Data Source Adapter — canonical metric 이름으로 현재값(Reading)을 읽는다.

    resolve(project, names) -> (readings, errors)

- adapter는 metric_registry.MetricDefinition.resolver 이름으로 찾는다.
- 지금 연결된 adapter는 check_metric 하나다: 기존 check_metric 서버(~/decision-debt-tool)의 resolver
  (users 테이블 COUNT, metrics 테이블)를 그대로 재사용한다. 새로 구현한 resolver는 없다.
- 새 data source를 붙이는 곳: MetricAdapter를 구현해 ADAPTERS에 등록하고 registry 지표의 resolver를 그 이름으로 바꾼다.
  예) "github_actions" → deployment_count, "prometheus" → error_rate, "stripe" → monthly_revenue (이번에는 구현하지 않음)
- 등록되지 않은 resolver 이름이면 조회하지 않고 not_found 오류로 돌려준다 (다른 지표 값으로 대체하지 않는다).
"""

from __future__ import annotations

from typing import Protocol

import metric_registry
from schemas import Metric, Project
from services import metrics as metric_service
from services.metrics import MetricError

Reading = Metric  # {name, value, unit, source, observed_at}


class MetricAdapter(Protocol):
    name: str

    async def resolve(self, project: Project, names: list[str]) -> tuple[dict[str, Reading], dict[str, MetricError]]:
        ...


class CheckMetricAdapter:
    """기존 check_metric HTTP 서버. 프로젝트의 metric_source가 없으면 not_connected (다른 프로젝트 값을 빌려오지 않음)"""

    name = "check_metric"

    async def resolve(self, project: Project, names: list[str]):
        # 모듈 속성으로 호출한다 (테스트의 patch와 기존 동작을 그대로 따른다)
        return await metric_service.check_metrics(project, names)


ADAPTERS: dict[str, MetricAdapter] = {"check_metric": CheckMetricAdapter()}


async def resolve_metrics(project: Project, names: list[str]) -> tuple[dict[str, Reading], dict[str, MetricError]]:
    """지표들을 resolver별로 묶어 조회. adapter 하나의 실패가 다른 adapter 조회를 막지 않는다."""
    by_adapter: dict[str, list[str]] = {}
    readings: dict[str, Reading] = {}
    errors: dict[str, MetricError] = {}
    for name in dict.fromkeys(names):
        definition = metric_registry.get(name)
        resolver = definition.resolver if definition else None
        if resolver and resolver in ADAPTERS:
            by_adapter.setdefault(resolver, []).append(name)
        else:
            errors[name] = MetricError("not_found", f"{name}: 연결된 data source adapter가 없습니다")
    for resolver, group in by_adapter.items():
        try:
            found, failed = await ADAPTERS[resolver].resolve(project, group)
        except Exception as e:  # noqa: BLE001
            found, failed = {}, {n: MetricError("unavailable", f"{resolver}: {type(e).__name__}") for n in group}
        readings.update(found)
        errors.update(failed)
    return readings, errors
