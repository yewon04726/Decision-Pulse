"""REVIEW_DUE 전환 알림: Hermes CLI(`hermes send --to telegram`)로 Telegram에 보낸다.

- Telegram Bot API를 직접 부르지 않는다. Hermes에 이미 설정된 자격 증명을 쓴다 (backend에는 token/chat id를 두지 않음)
- 메시지는 Decision 데이터로 deterministic하게 만든다 (LLM 없음). 값이 없는 필드는 쓰지 않는다
- 언제 보낼지는 호출하는 쪽(review_monitor)이 정한다: 새 ACTIVE → REVIEW_DUE 이벤트가 DB에 저장된 직후 1회
- 실패해도 예외를 내지 않는다 (결과만 돌려줌) → review monitor는 영향받지 않는다. 재시도 큐는 없다
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass

from config import BACKEND_ENV_KEYS, HERMES_BIN, TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS, TELEGRAM_REVIEW_NOTIFICATIONS_ENABLED

log = logging.getLogger("telegram_notifier")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [telegram] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

ENABLED = TELEGRAM_REVIEW_NOTIFICATIONS_ENABLED
TARGET = "telegram"  # Hermes의 Telegram home channel


@dataclass
class NotificationResult:
    status: str  # sent | failed | disabled
    message: str


def _number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def format_review_due_message(
    decision_id: str, title: str | None, trigger_text: str | None, observed_value=None, unit: str | None = None
) -> str:
    lines = ["🚨 Decision Review Due", ""]
    if title:
        lines.append(title)
    lines += [decision_id, ""]
    if trigger_text:
        lines.append(f"재검토 조건: {trigger_text}")
    if observed_value is not None:
        lines.append(f"현재 관측값: {_number(observed_value)}" + (f" {unit}" if unit else ""))
    lines += ["", "결정을 다시 검토할 시점입니다."]
    return "\n".join(lines)


def build_argv(message: str) -> list[str]:
    return [HERMES_BIN, "send", "--to", TARGET, "--json", message]


async def send_review_due_notification(
    decision_id: str, title: str | None, trigger_text: str | None, observed_value=None, unit: str | None = None
) -> NotificationResult:
    """Telegram으로 1회 발송. 예외를 내지 않는다."""
    if not ENABLED:
        return NotificationResult("disabled", "TELEGRAM_REVIEW_NOTIFICATIONS_ENABLED=false")
    message = format_review_due_message(decision_id, title, trigger_text, observed_value, unit)
    # backend/.env 값(API Key 등)은 Hermes에 넘기지 않는다. Hermes는 자기 설정(~/.hermes)을 쓴다
    env = {k: v for k, v in os.environ.items() if k not in BACKEND_ENV_KEYS}
    try:
        proc = await asyncio.create_subprocess_exec(
            *build_argv(message), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            result = NotificationResult("failed", f"timeout ({TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS:g}s)")
            log.warning("%s 알림 실패: %s", decision_id, result.message)
            return result
    except Exception as e:  # noqa: BLE001 — 실행 파일 없음 등
        log.warning("%s 알림 실패: %s: %s", decision_id, type(e).__name__, e)
        return NotificationResult("failed", f"{type(e).__name__}: {e}")
    stdout = out.decode(errors="replace").strip()
    stderr = err.decode(errors="replace").strip()
    if proc.returncode != 0:
        tail = " | ".join((stderr or stdout).splitlines()[-3:])[:300]
        log.warning("%s 알림 실패 (exit %s): %s", decision_id, proc.returncode, tail)
        return NotificationResult("failed", f"exit {proc.returncode}: {tail}")
    # hermes --json 결과에는 chat_id가 있다 → 로그/결과에는 message_id만 남긴다 (backend에 chat id를 두지 않음)
    try:
        message_id = json.loads(stdout).get("message_id")
    except (ValueError, AttributeError):
        message_id = None
    log.info("%s REVIEW_DUE 알림 발송 (telegram, message_id=%s)", decision_id, message_id)
    return NotificationResult("sent", f"message_id={message_id}")
