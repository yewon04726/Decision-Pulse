"""Review monitor: 사용자 요청 없이 주기적으로 결정의 재검토 조건을 확인하고 상태 변화를 기록한다.

판정은 /api/decisions와 같은 코드를 쓴다 (check_metrics → to_view). LLM은 호출하지 않는다.
결정의 업무 상태(decisions.status)는 바꾸지 않는다. 판정 결과는 decision_review_state, 변화 이력은 review_events에 둔다.

상태 변화 규칙 (기준 = 마지막으로 확정된 판정 last_review_status)
- 처음 확인          : 기준만 저장 (이벤트 없음. 모니터 시작 전부터의 상태이므로 '변화'가 아니다)
- ACTIVE → REVIEW_DUE : 이벤트 1개 (알림 대상)
- REVIEW_DUE → ACTIVE : 기준 갱신 + 복구 이벤트 (이력용, 알림 아님). 이후 다시 넘으면 새 이벤트
- 같은 상태 유지      : 확인 시각만 갱신 (이벤트 없음 → 반복 실행해도 중복 없음)
- UNKNOWN            : 이벤트 없음, 기준 유지 (확인 결과만 UNKNOWN으로 기록). 조회가 회복되면 기준과 비교한다

Telegram 알림: 새 ACTIVE → REVIEW_DUE 이벤트가 DB에 저장된 직후에만 1회 (telegram_notifier).
이벤트가 생기지 않는 경우(처음 확인/같은 상태/복구)에는 보내지 않는다. 알림 실패는 판정·기록에 영향을 주지 않는다.
"""

import asyncio
import logging
from datetime import datetime

import db
from config import REVIEW_MONITOR_ENABLED, REVIEW_MONITOR_INTERVAL_SECONDS
from schemas import Decision, MetricTrigger
from services import metrics as metric_service
from services import telegram_notifier
from services.review import to_view, trigger_metric_names
from services.review_trigger import format_review_trigger_v2

log = logging.getLogger("review_monitor")
if not log.handlers:  # uvicorn 기본 설정에서도 INFO 로그가 보이도록
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [review_monitor] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

# /api/health에서 보여 주는 실행 상태
STATUS = {
    "enabled": REVIEW_MONITOR_ENABLED,
    "interval_seconds": REVIEW_MONITOR_INTERVAL_SECONDS,
    "runs": 0,
    "last_run_at": None,
    "last_error": None,
}

_DEFINITIVE = ("ACTIVE", "REVIEW_DUE")


def _monitored(decisions: list[Decision]) -> list[Decision]:
    """자동 판정 대상: 업무 상태가 active이고, 등록된 지표의 metric 조건이 있는 결정"""
    return [
        d for d in decisions
        if d.status == "active" and isinstance(d.review_trigger, MetricTrigger) and d.review_trigger.metric_key
    ]


async def check_project(project) -> list[int]:
    """프로젝트 하나 확인. 생성된 이벤트 id 목록. 결정 하나의 오류가 다른 결정 확인을 막지 않는다."""
    decisions = _monitored(db.list_decisions(project.id))
    if not decisions:
        return []
    found, errors = await metric_service.check_metrics(project, trigger_metric_names(decisions))
    states = db.get_review_states(project.id)
    created = []
    for decision in decisions:
        try:
            view = to_view(decision, found, errors)
            current = view.display_status  # ACTIVE | REVIEW_DUE | UNKNOWN
            ev = view.evaluation
            key = decision.review_trigger.metric_key
            observed = (
                {key: {"value": ev.metric.value, "unit": ev.metric.unit, "source": ev.metric.source,
                       "observed_at": ev.metric.observed_at}}
                if ev and ev.metric else None
            )
            prev = states.get(decision.id)
            baseline = prev["last_review_status"] if prev else None
            new_baseline = current if current in _DEFINITIVE else (baseline or "UNKNOWN")
            event = None
            if baseline in _DEFINITIVE and current in _DEFINITIVE and current != baseline:
                event = {
                    "previous_status": baseline,
                    "new_status": current,
                    "trigger": decision.review_trigger.model_dump(mode="json", exclude_none=True),
                }
            event_id = db.record_review_check(
                project.id, decision.id, current, new_baseline, observed, ev.error if ev else None, event
            )
            value = f"{ev.metric.value} {ev.metric.unit}" if ev and ev.metric else "-"
            t = decision.review_trigger
            if event:
                created.append(event_id)
                if event_id and current == "REVIEW_DUE":
                    await _notify_review_due(decision, view)
                log.info(
                    "%s %s → %s (event #%s) | %s %s %s, observed %s=%s",
                    decision.id, baseline, current, event_id, key, t.operator, t.threshold, key, value,
                )
            elif prev is None:
                log.info("%s baseline %s (%s=%s)", decision.id, current, key, value)
            elif current == "UNKNOWN" and prev["last_check_status"] != "UNKNOWN":
                log.warning("%s UNKNOWN (판정 불가, 이벤트 없음): %s", decision.id, ev.error if ev else "-")
        except Exception as e:  # noqa: BLE001 — 한 결정의 오류로 전체 확인이 멈추지 않게
            log.exception("%s 확인 실패: %s", decision.id, e)
    return created


async def _notify_review_due(decision: Decision, view) -> None:
    """새 REVIEW_DUE 이벤트 1건 → Telegram 1회. 실패해도 예외를 밖으로 내지 않는다 (이벤트/상태는 이미 저장됨)."""
    try:
        metric = view.evaluation.metric if view.evaluation else None
        await telegram_notifier.send_review_due_notification(
            decision.id, decision.title, format_review_trigger_v2(decision.review_trigger),
            metric.value if metric else None, metric.unit if metric else None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("%s Telegram 알림 오류 (무시): %s", decision.id, e)


async def run_once() -> list[int]:
    created = []
    for project in db.list_projects():
        if not project.metrics_connected:  # metric source가 없는 프로젝트는 확인할 값이 없다
            continue
        try:
            created += await check_project(project)
        except Exception as e:  # noqa: BLE001
            log.exception("project %s 확인 실패: %s", project.id, e)
    STATUS["runs"] += 1
    STATUS["last_run_at"] = datetime.now().isoformat(timespec="seconds")
    return created


async def run_forever():
    log.info("started (interval %ss)", REVIEW_MONITOR_INTERVAL_SECONDS)
    while True:
        try:
            await run_once()
            STATUS["last_error"] = None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 다음 주기에 다시 시도
            STATUS["last_error"] = f"{type(e).__name__}: {e}"
            log.exception("run failed: %s", e)
        await asyncio.sleep(REVIEW_MONITOR_INTERVAL_SECONDS)
