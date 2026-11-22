"""Decision Debt read-only MCP server (stdio). Hermes가 로컬 subprocess로 띄운다.

실행: cd backend && .venv/bin/python mcp_server.py

- tool은 list_review_due, get_decision_detail 두 개뿐이다 (승인/수정/acknowledge/metric/추출/gbrain/Dify 없음)
- SQLite를 read-only(mode=ro)로만 연다. init_db(migration/seed)도, review monitor도, check_metric도 실행하지 않는다
- 관측값은 review monitor가 decision_review_state에 저장한 마지막 값이다
- stdout은 MCP protocol 전용. 로그는 stderr로만 낸다
- gbrain DB는 열지 않는다 (gbrain은 Hermes의 별도 MCP)
"""

import logging
import sqlite3
import sys

import db
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from services import decision_read

db.READ_ONLY = True

log = logging.getLogger("decision_debt_mcp")
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [decision-debt-mcp] %(levelname)s %(message)s", "%H:%M:%S"))
log.addHandler(_handler)
log.setLevel(logging.INFO)
log.propagate = False

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

mcp = MCPServer(
    "decision-debt",
    instructions=(
        "Decision Debt (read-only). 사람이 승인한 기술 결정과 재검토(REVIEW_DUE) 상태를 조회한다. "
        "관측값은 review monitor가 마지막으로 저장한 값이다. 이 서버로는 어떤 데이터도 바꿀 수 없다."
    ),
    log_level="WARNING",
)


def _safe(fn, *args) -> dict:
    """DB 오류 등은 traceback 없이 요약해서 돌려준다 (상세는 stderr 로그)"""
    try:
        return fn(*args)
    except sqlite3.Error as e:
        log.exception("%s failed", fn.__name__)
        return {"error": "database_unavailable", "message": f"{type(e).__name__}: {str(e)[:200]}"}
    except Exception as e:  # noqa: BLE001
        log.exception("%s failed", fn.__name__)
        return {"error": "internal_error", "message": type(e).__name__}


@mcp.tool(annotations=READ_ONLY)
def list_review_due(project_id: int | None = None) -> dict:
    """List Decision Debt decisions that are currently REVIEW_DUE (review trigger met) from the Decision Debt database (read-only).

    Returns one record per decision: decision_id, project, title, topic, decision, review trigger condition,
    observed metric value (from the review monitor's last check), detected_at and the open review event.

    현재 재검토가 필요한(REVIEW_DUE) 결정 목록. 결정당 1개.

    review monitor가 저장한 현재 판정 기준이다 (해소된 이벤트·ACTIVE 결정은 제외).
    각 항목: decision_id, project_id, title, topic, decision, trigger(재검토 조건), trigger_text,
    observed(마지막 관측값: metric, value, unit, source, observed_at), detected_at, event(열린 REVIEW_DUE 이벤트 또는 null).

    Args:
        project_id: 특정 프로젝트만 조회할 때. 생략하면 모든 프로젝트.
    """
    return _safe(decision_read.list_review_due, project_id)


@mcp.tool(annotations=READ_ONLY)
def get_decision_detail(decision_id: str) -> dict:
    """Get one Decision Debt decision record by decision_id from the Decision Debt database (read-only).

    Returns the original decision, rationale, assumptions, review trigger condition, status, human_verified,
    source documents and evidence excerpts, and the current review state with the last observed metric value.

    결정 하나의 상세: 당시 결정, 이유(rationale), 가정, 재검토 조건, 상태, 사람 승인 여부,
    근거 Source(제목/유형)와 근거 인용(excerpt), 현재 재검토 상태(review_state)와 열린 REVIEW_DUE 이벤트.
    원문 전체는 돌려주지 않는다. 없는 decision_id면 {"error": "decision_not_found"}.

    Args:
        decision_id: 예) DEC-2026-1106-DATABASE
    """
    return _safe(decision_read.get_decision_detail, decision_id.strip())


if __name__ == "__main__":
    mcp.run()  # stdio
