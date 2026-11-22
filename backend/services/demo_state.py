"""Demo Control Center (발표/개발 전용): Review Monitor가 관측하는 '외부 상태값'을 웹에서 바꾼다.

원칙
- 바꾸는 것은 check_metric(~/decision-debt-tool)이 읽는 sample DB뿐이다. Decision / review state / review event는 건드리지 않는다.
  REVIEW_DUE 판정, Review Event, Web alert, Telegram 알림은 기존 Review Monitor가 다음 주기에 그대로 처리한다.
- DB 쓰기는 기존 CLI script를 그대로 실행한다 (scripts/set_user_count.py, scripts/set_metric.py).
  → 터미널과 웹이 같은 코드로 DB를 바꾼다. 어떤 script를 쓸지는 resolver가 알려 주는 source로 정한다:
      sqlite:users             → set_user_count.py (users row 수를 N으로 맞춤)
      metrics_store:<name>     → set_metric.py <name> <value>
      그 밖(Prometheus 등)      → 조작 불가 (read-only)
- 현재값은 Review Monitor와 같은 경로(GET check_metric /metrics/{name})로 읽는다.
- 미리보기(preview)는 backend 판정 함수(services.review.evaluate_trigger / display_status)를 그대로 쓴다.
- DEMO_MODE_ENABLED=false면 route에서 모두 404 (이 모듈을 호출하지 않는다).
"""

import asyncio
import logging
import math
import os

import db
from config import (
    BACKEND_ENV_KEYS,
    DEMO_BASELINE,
    DEMO_MAX_METRIC_VALUE,
    DEMO_MAX_USER_COUNT,
    DEMO_METRIC_SOURCE,
    DEMO_SCENARIOS,
    DEMO_SCRIPT_TIMEOUT_SECONDS,
    DEMO_TOOL_DIR,
    DEMO_TOOL_PYTHON,
    KNOWN_METRICS,
    METRIC_SOURCES,
)
from schemas import (
    CompoundTrigger,
    Decision,
    DemoChange,
    DemoCondition,
    DemoConditionPart,
    DemoDecisionImpact,
    DemoMetricState,
    DemoPreset,
    DemoPreview,
    DemoPreviewItem,
    DemoProjectRef,
    DemoScenario,
    DemoState,
    Metric,
    MetricTrigger,
    Project,
)
from services import metrics as metric_service
from services import review_monitor
from services.review import _OPS, display_status, evaluate_trigger, to_view
from services.review_trigger import format_review_trigger_v2

log = logging.getLogger("demo")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [demo] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

_WRITE_LOCK = asyncio.Lock()  # 동시에 두 값을 바꾸지 않게 (script는 한 트랜잭션씩)


class UnknownMetric(Exception):
    """등록되지 않은 metric (KNOWN_METRICS에 없음)"""


class NotSimulatorBacked(Exception):
    """resolver source가 시뮬레이터가 아님 → 조작 불가"""


class InvalidValue(Exception):
    pass


class SourceUnavailable(Exception):
    """check_metric 서버 / sample DB를 읽거나 쓸 수 없음"""


# ---------------------------------------------------------------- source → writer


def simulator_for(name: str, source: str | None) -> str | None:
    if source == "sqlite:users" and name == "user_count":
        return "users_table"
    if source == f"metrics_store:{name}":
        return "metrics_store"
    return None


def is_integer_metric(name: str, simulator: str | None) -> bool:
    return simulator == "users_table" or name.endswith("_count")


def validate_value(name: str, simulator: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidValue("value는 유한한 숫자여야 합니다.")
    if value < 0:
        raise InvalidValue("value는 0 이상이어야 합니다.")
    if is_integer_metric(name, simulator) and float(value) != int(value):
        raise InvalidValue(f"{name}은 정수만 가능합니다.")
    if simulator == "users_table" and value > DEMO_MAX_USER_COUNT:
        raise InvalidValue(f"user_count는 최대 {DEMO_MAX_USER_COUNT}까지 바꿀 수 있습니다 (users row를 실제로 insert).")
    if value > DEMO_MAX_METRIC_VALUE:
        raise InvalidValue(f"value가 너무 큽니다 (최대 {DEMO_MAX_METRIC_VALUE:g}).")
    return float(value)


def tool_argv(simulator: str, name: str, value: float) -> list[str]:
    """기존 CLI와 같은 명령 (decision-debt-tool 폴더에서 실행)"""
    number = str(int(value)) if float(value).is_integer() else repr(float(value))
    if simulator == "users_table":
        return [str(DEMO_TOOL_PYTHON), "scripts/set_user_count.py", number]
    return [str(DEMO_TOOL_PYTHON), "scripts/set_metric.py", name, number]


async def _run_tool(argv: list[str]) -> str:
    # backend/.env 값(API key 등)은 넘기지 않는다. tool은 자기 settings/.env를 쓴다
    env = {k: v for k, v in os.environ.items() if k not in BACKEND_ENV_KEYS}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(DEMO_TOOL_DIR), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
    except OSError as e:
        raise SourceUnavailable(f"sample DB script를 실행할 수 없습니다 ({DEMO_TOOL_DIR.name}): {type(e).__name__}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=DEMO_SCRIPT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise SourceUnavailable(f"sample DB script timeout ({DEMO_SCRIPT_TIMEOUT_SECONDS:g}s)") from e
    if proc.returncode != 0:
        tail = " | ".join((err or out).decode(errors="replace").strip().splitlines()[-2:])[:300]
        raise SourceUnavailable(f"sample DB script 실패 (exit {proc.returncode}): {tail}")
    return out.decode(errors="replace").strip()


# ---------------------------------------------------------------- read


def metric_tool_url() -> str | None:
    return METRIC_SOURCES.get(DEMO_METRIC_SOURCE)


async def read_metrics() -> tuple[dict[str, Metric], dict[str, metric_service.MetricError]]:
    url = metric_tool_url()
    if url is None:
        msg = f"metric source '{DEMO_METRIC_SOURCE}'가 설정되지 않았습니다."
        return {}, {n: metric_service.MetricError("not_connected", msg) for n in KNOWN_METRICS}
    return await metric_service.check_metrics_at(url, KNOWN_METRICS)


async def read_metric(name: str) -> Metric:
    found, errors = await read_metrics()
    if name in found:
        return found[name]
    raise SourceUnavailable(errors[name].message if name in errors else f"{name} 값을 읽을 수 없습니다.")


def simulated_projects() -> list[Project]:
    """시뮬레이터 metric source에 연결된 프로젝트 (값 변경의 영향을 받는 프로젝트)"""
    return [p for p in db.list_projects() if p.metric_source == DEMO_METRIC_SOURCE and p.metrics_connected]


def _single_metric(d: Decision) -> MetricTrigger | None:
    t = d.review_trigger
    return t if isinstance(t, MetricTrigger) and t.metric_key else None


def _metric_parts(t) -> list[MetricTrigger]:
    if isinstance(t, MetricTrigger):
        return [t]
    if isinstance(t, CompoundTrigger):
        return [m for c in t.conditions for m in _metric_parts(c)]
    return []


def _hypothetical(name: str, value: float, unit: str | None) -> Metric:
    v = int(value) if float(value).is_integer() else value
    return Metric(metric_name=name, value=v, unit=unit or "", source="demo-preview")


def _status_at(d: Decision, name: str, value: float, unit: str | None) -> str:
    """value일 때의 판정 (Review Monitor와 같은 evaluate_trigger / display_status)"""
    ev = evaluate_trigger(_single_metric(d), {name: _hypothetical(name, value, unit)}, {})
    return display_status(d, ev)


def _presets(name: str, decisions: list[Decision], unit: str | None, step: float) -> list[DemoPreset]:
    """trigger 경계값 (판정이 바뀌는 바로 아래/위). 값마다 REVIEW_DUE 결정 수는 실제 판정 함수로 센다"""
    triggers = sorted({(t.operator, t.threshold) for d in decisions if (t := _single_metric(d)) and t.metric_key == name},
                      key=lambda x: x[1])
    out: list[DemoPreset] = []
    seen: set[float] = set()
    for op, th in triggers:
        candidates = [v for v in (th - step, th, th + step) if v >= 0]
        met = [_OPS[op](v, th) for v in candidates]
        for i in range(len(candidates) - 1):
            if met[i] != met[i + 1]:
                for v, m in ((candidates[i], met[i]), (candidates[i + 1], met[i + 1])):
                    if v in seen:
                        continue
                    seen.add(v)
                    due = sum(1 for d in decisions if _status_at(d, name, v, unit) == "REVIEW_DUE")
                    out.append(DemoPreset(value=v, label=f"{op} {th:g} · {'meets' if m else 'below'}", review_due=due))
                break
    return out


def _due_count(decisions: list[Decision], name: str, value: float | None, unit: str | None) -> int:
    if value is None:
        return 0
    return sum(1 for d in decisions if (t := _single_metric(d)) and t.metric_key == name
               and _status_at(d, name, value, unit) == "REVIEW_DUE")


async def get_state() -> DemoState:
    found, errors = await read_metrics()
    sim_projects = simulated_projects()
    sim_ids = {p.id for p in sim_projects}
    all_projects = db.list_projects()

    # 시뮬레이터 프로젝트의 active 결정 (Review Monitor 대상과 같은 기준)
    active: list[tuple[Project, Decision]] = [
        (p, d) for p in sim_projects for d in db.list_decisions(p.id) if d.status == "active"
    ]
    states = {pid: db.get_review_states(pid) for pid in sim_ids}

    metrics: list[DemoMetricState] = []
    for name in KNOWN_METRICS:
        m = found.get(name)
        sim = simulator_for(name, m.source if m else None)
        unit = m.unit if m else None
        single = [(p, d) for p, d in active if (t := _single_metric(d)) and t.metric_key == name]
        impacts = []
        for p, d in single:
            view = to_view(d, found, errors)
            st = states[p.id].get(d.id)
            t = d.review_trigger
            impacts.append(DemoDecisionImpact(
                decision_id=d.id, title=d.title, project_id=p.id, project_name=p.name,
                trigger=format_review_trigger_v2(t), operator=t.operator, threshold=t.threshold,
                status=view.display_status,
                monitor_status=st["last_check_status"] if st else None,
                monitor_checked_at=st["last_checked_at"] if st else None,
            ))
        compound = [d.id for _, d in active if isinstance(d.review_trigger, CompoundTrigger)
                    and any(x.metric_key == name for x in _metric_parts(d.review_trigger))]
        step = 1.0
        metrics.append(DemoMetricState(
            name=name,
            value=m.value if m else None, unit=unit,
            source=m.source if m else None, observed_at=m.observed_at if m else None,
            error=None if m else (errors[name].message if name in errors else "값을 읽을 수 없습니다."),
            simulator=sim,
            integer=is_integer_metric(name, sim),
            baseline=DEMO_BASELINE.get(name),
            presets=_presets(name, [d for _, d in single], unit, step) if sim else [],
            decisions=impacts,
            compound_decisions=compound,
        ))

    # 시뮬레이터로 조작할 수 없는 조건 (모든 프로젝트, read-only)
    conditions: list[DemoCondition] = []
    for p in all_projects:
        for d in db.list_decisions(p.id):
            t = d.review_trigger
            if d.status != "active" or t is None:
                continue
            simulated = p.id in sim_ids
            if simulated and _single_metric(d):
                continue  # 위 metric 섹션에서 조작 가능
            view = to_view(d, found, errors) if simulated else None
            parts: list[DemoConditionPart] = []
            if isinstance(t, CompoundTrigger):
                for c in t.conditions:
                    key = c.metric_key if isinstance(c, MetricTrigger) else None
                    parts.append(DemoConditionPart(
                        type=c.type, expression=format_review_trigger_v2(c), metric=key,
                        simulator_backed=bool(simulated and key and simulator_for(key, found[key].source if key in found else None)),
                    ))
                note = ("compound 조건은 Review Monitor가 자동 판정하지 않습니다. "
                        "metric 부분은 위에서 바꿀 수 있지만 duration 등 나머지는 시뮬레이터가 없습니다.")
            elif isinstance(t, MetricTrigger) and not t.metric_key:
                note = "등록되지 않은 지표 (지표 매핑 필요) — 자동 판정하지 않습니다."
            elif isinstance(t, MetricTrigger):
                note = "이 프로젝트는 시뮬레이터 metric source에 연결되어 있지 않습니다."
            else:
                note = "상태 source가 없는 조건 — 사람이 확인합니다 (Not simulator-backed)."
            conditions.append(DemoCondition(
                decision_id=d.id, title=d.title, project_id=p.id, project_name=p.name,
                trigger_type=t.type, expression=format_review_trigger_v2(t),
                status=view.display_status if view else "ACTIVE",
                logic=t.logic if isinstance(t, CompoundTrigger) else None,
                parts=parts, note=note,
            ))

    by_name = {m.name: m for m in metrics}
    scenarios = []
    for s in DEMO_SCENARIOS:
        ms = by_name.get(s["metric"])
        if not ms or not ms.simulator:
            continue
        decs = [d for _, d in active]
        scenarios.append(DemoScenario(
            id=s["id"], title=s["title"], metric=s["metric"], baseline=DEMO_BASELINE.get(s["metric"]),
            value=float(s["value"]), unit=ms.unit, review_due=_due_count(decs, s["metric"], s["value"], ms.unit),
        ))

    return DemoState(
        metric_source=DEMO_METRIC_SOURCE,
        metric_tool_url=metric_tool_url(),
        monitor=dict(review_monitor.STATUS),
        projects=[DemoProjectRef(id=p.id, name=p.name, simulated=p.id in sim_ids) for p in all_projects],
        metrics=metrics,
        conditions=conditions,
        scenarios=scenarios,
    )


async def preview(name: str, value: float) -> DemoPreview:
    if name not in KNOWN_METRICS:
        raise UnknownMetric(name)
    found, errors = await read_metrics()
    unit = found[name].unit if name in found else None
    sim = simulator_for(name, found[name].source if name in found else None)
    if sim:
        value = validate_value(name, sim, value)
    items = []
    for p in simulated_projects():
        for d in db.list_decisions(p.id):
            t = _single_metric(d)
            if d.status != "active" or not t or t.metric_key != name:
                continue
            items.append(DemoPreviewItem(
                decision_id=d.id, title=d.title, trigger=format_review_trigger_v2(t),
                current=to_view(d, found, errors).display_status,
                predicted=_status_at(d, name, value, unit),
            ))
    return DemoPreview(
        metric=name, value=value, unit=unit,
        affected=sum(1 for i in items if i.current != i.predicted),
        review_due=sum(1 for i in items if i.predicted == "REVIEW_DUE"),
        decisions=items,
    )


# ---------------------------------------------------------------- write


async def set_value(name: str, value: float) -> DemoChange:
    """외부 상태값 1개 변경. Decision / review state는 바꾸지 않는다."""
    if name not in KNOWN_METRICS:
        raise UnknownMetric(name)
    async with _WRITE_LOCK:
        before = await read_metric(name)
        sim = simulator_for(name, before.source)
        if sim is None:
            raise NotSimulatorBacked(f"{name}의 source({before.source})는 시뮬레이터가 아닙니다.")
        value = validate_value(name, sim, value)
        await _run_tool(tool_argv(sim, name, value))
        after = await read_metric(name)
    log.info("demo_state_changed metric=%s before=%s after=%s unit=%s source=%s",
             name, before.value, after.value, after.unit, after.source)
    return DemoChange(metric=name, before=before.value, after=after.value, unit=after.unit,
                      source=after.source, observed_at=after.observed_at)


async def reset() -> list[DemoChange]:
    """발표 baseline으로 복원 (외부 상태만. 복구 판정은 Review Monitor가 한다). 이미 baseline이면 건너뛴다"""
    changes = []
    for name, value in DEMO_BASELINE.items():
        if name not in KNOWN_METRICS:
            continue
        current = await read_metric(name)
        if float(current.value) == float(value):
            continue
        changes.append(await set_value(name, value))
    return changes
