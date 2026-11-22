"""승인된 Decision → Markdown → gbrain sync (source working tree).

SQLite가 Decision의 원본이고, gbrain은 승인된 Decision을 검색·종합하기 위한 파생 knowledge layer다.
- human_verified=true인 Decision만 내보낸다 (seed/pending/ignored/추출 중간 결과는 대상 아님)
- 파일명은 Decision ID 기준 → 다시 동기화하면 같은 파일을 덮어쓴다
- 임시 파일에 쓴 뒤 rename (반쪽 파일이 남지 않게)
- gbrain CLI를 subprocess로 실행 (shell 없음, timeout): `gbrain sync --no-pull --source <id> --working-tree --json`
  Hermes gateway의 `gbrain serve`가 PGLite owner이면 sync가 그 owner에게 위임된다 (`gbrain import`는 pglite_busy로 실패).
  --working-tree라 commit하지 않은 새 Markdown 파일도 반영된다. embedding/index는 gbrain sync가 담당 (backend는 호출하지 않음)
- 내용 hash가 마지막 성공과 같으면 sync를 생략한다
- 어떤 경우에도 예외를 내지 않고 결과만 돌려준다 → 승인(Approve)에는 영향이 없다
LLM은 호출하지 않는다.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import db
from config import (
    BACKEND_ENV_KEYS,
    GBRAIN_BIN,
    GBRAIN_DECISIONS_DIR,
    GBRAIN_REPO_DIR,
    GBRAIN_SOURCE_ID,
    GBRAIN_SYNC_ENABLED,
    GBRAIN_SYNC_TIMEOUT_SECONDS,
)
from schemas import Decision, Project, Source
from services.review_trigger import format_review_trigger_v2, normalize_review_trigger

TARGET = "gbrain"
log = logging.getLogger("gbrain_sync")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [gbrain] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

# backend 안에서는 sync를 순서대로 실행한다 (같은 source를 동시에 sync하지 않게)
_LOCK = asyncio.Lock()


@dataclass
class GbrainSyncResult:
    status: str  # synced | unchanged | failed | skipped | disabled
    message: str
    path: str | None = None


def is_exportable(decision: Decision) -> bool:
    """사람이 승인한 Decision만 (Candidate Approve로 만들어진 것)"""
    return decision.human_verified is True


def markdown_filename(decision_id: str) -> str:
    """Decision ID 기준의 안정적인 파일명. 파일명에 위험한 문자는 '_'로 바꾼다."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", decision_id).strip("._") or "decision"
    return f"{safe}.md"


def _yaml(value: str) -> str:
    """frontmatter 문자열 값 (따옴표로 감싸고 escape)"""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def decision_to_gbrain_markdown(
    decision: Decision, project: Project | None = None, sources: dict[str, Source] | None = None
) -> str:
    """사람이 읽을 수 있고 검색에 유리한 Decision page. 실제로 있는 값만 쓴다 (없는 섹션은 만들지 않음).
    원문 Source 전체는 넣지 않고 근거 문장과 출처만 넣는다."""
    sources = sources or {}
    trigger = normalize_review_trigger(decision.review_trigger) if decision.review_trigger else None
    tags = ["decision-debt", "decision"] + ([decision.topic] if decision.topic else [])

    front = [
        "---",
        "type: decision",
        f"title: {_yaml(decision.title)}",
        f"decision_id: {_yaml(decision.id)}",
        f"project: {_yaml(project.name if project else decision.project_id)}",
        f"topic: {_yaml(decision.topic)}",
        f"status: {_yaml(decision.status)}",
        f"human_verified: {'true' if decision.human_verified else 'false'}",
        f"origin: {_yaml(decision.origin)}",
    ]
    if decision.created_at:
        front.append(f"created_at: {_yaml(decision.created_at)}")
    if decision.updated_at:
        front.append(f"updated_at: {_yaml(decision.updated_at)}")
    front.append("tags: [" + ", ".join(_yaml(t) for t in tags) + "]")
    front.append("---")

    # 본문은 결정 내용부터 쓴다. 모든 page에 같은 메타데이터 줄이 앞에 오면 embedding이 서로 비슷해져 검색 구분력이 떨어지므로
    # 메타데이터는 frontmatter와 맨 아래 Metadata 섹션에 둔다
    lines = [*front, "", f"# {decision.title}", "", "## Decision", "", decision.decision]
    if decision.rationale:
        lines += ["", "## Rationale", "", decision.rationale]
    if decision.assumptions:
        lines += ["", "## Assumptions", "", *[f"- {a}" for a in decision.assumptions]]
    if trigger is not None:
        lines += ["", "## Review Trigger", "", format_review_trigger_v2(trigger)]  # 기존 V2 formatter
        raw = trigger.raw_condition or getattr(trigger, "description", None)
        if raw:
            lines += ["", "## Review Trigger Raw", "", raw]
    if decision.evidence:
        lines += ["", "## Evidence", ""]
        for e in decision.evidence:
            src = sources.get(e.source_id)
            label = src.title if src else e.source_id
            for quote in [q for q in e.quote.split("\n") if q.strip()]:
                lines.append(f"- {quote.strip()} ({label})")
    used = list(dict.fromkeys(e.source_id for e in decision.evidence))
    if used:
        lines += ["", "## Sources", ""]
        for sid in used:
            src = sources.get(sid)
            if src:
                meta = ", ".join(x for x in (src.type, src.date) if x)
                lines.append(f"- {src.title} ({meta}) [source_id: {sid}]")
            else:
                lines.append(f"- {sid}")
    if decision.notes:
        lines += ["", "## Notes", "", *[f"- {n.label}: {n.text}" for n in decision.notes]]
    lines += [
        "", "## Metadata", "",
        f"- Decision ID: {decision.id}",
        f"- Project: {project.name if project else decision.project_id}",
        f"- Topic: {decision.topic}",
        f"- Status: {decision.status}",
        f"- Human Verified: {'true' if decision.human_verified else 'false'}",
    ]
    if decision.superseded_by:
        lines.append(f"- Superseded By: {decision.superseded_by}")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)  # mkstemp 기본(0600) 대신 일반 문서 권한
        os.replace(tmp, path)  # 같은 디렉터리 안 rename → 원자적
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def export_decision_to_gbrain(
    decision: Decision, project: Project | None = None, sources: dict[str, Source] | None = None
) -> tuple[Path, str]:
    """Markdown 파일로 저장. (경로, 내용 sha256). 내용이 같으면 파일을 다시 쓰지 않는다."""
    text = decision_to_gbrain_markdown(decision, project, sources)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = GBRAIN_DECISIONS_DIR / markdown_filename(decision.id)
    if not (path.exists() and path.read_text(encoding="utf-8") == text):
        _atomic_write(path, text)
    return path, digest


async def _gbrain(args: list[str], cwd: Path) -> tuple[str, str]:
    """gbrain CLI 실행 (shell 없음, timeout). 실패하면 RuntimeError (stderr 요약 포함). (stdout, stderr)를 돌려준다."""
    exe = shutil.which(GBRAIN_BIN)
    if not exe:
        raise RuntimeError(f"gbrain 실행 파일({GBRAIN_BIN})을 찾을 수 없습니다 (GBRAIN_BIN 확인)")
    # gbrain은 현재 디렉터리의 .env를 읽는다 → backend 폴더가 아닌 brain 폴더에서 실행하고,
    # backend/.env에서 온 값(OpenRouter/Dify 키 등)은 환경변수로 넘기지 않는다. gbrain 설정은 ~/.gbrain에 있다
    env = {k: v for k, v in os.environ.items() if k not in BACKEND_ENV_KEYS}
    proc = await asyncio.create_subprocess_exec(
        exe, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
        cwd=str(cwd), env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=GBRAIN_SYNC_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"gbrain {args[0]} 시간 초과 ({GBRAIN_SYNC_TIMEOUT_SECONDS:.0f}초)") from None
    stdout, stderr = out.decode(errors="replace").strip(), err.decode(errors="replace").strip()
    if proc.returncode != 0:
        tail = " | ".join((stderr or stdout).splitlines()[-3:])
        raise RuntimeError(f"gbrain {args[0]} 실패 (exit {proc.returncode}): {tail[:500]}")
    return stdout, stderr


def _json_tail(stdout: str) -> dict:
    """--json 출력 (앞에 진행 로그가 섞일 수 있어 마지막 JSON 객체만)"""
    start = stdout.find("{")
    try:
        return json.loads(stdout[start:]) if start >= 0 else {}
    except ValueError:
        return {}


def sync_command() -> list[str]:
    """source working tree 전체를 반영하는 delegated sync (PGLite owner가 있으면 위임됨)"""
    return [GBRAIN_BIN, "sync", "--no-pull", "--source", GBRAIN_SOURCE_ID, "--working-tree", "--json"]


# sync 결과 판정: exit 0 + outcome=synced. 변경이 없으면 sync_status=up_to_date (added/modified/embedded=0) → 정상 no-op
_OK_SYNC_STATUS = {"synced", "up_to_date"}


async def _gbrain_source_sync() -> dict:
    """export한 Markdown을 gbrain source에 반영한다. embedding도 gbrain sync가 처리한다 (backend는 embedding API를 부르지 않음)."""
    stdout, stderr = await _gbrain(sync_command()[1:], GBRAIN_REPO_DIR)
    for line in stderr.splitlines():  # 예: "[sync] Delegating to the registered PGLite owner." (gateway의 gbrain serve에 위임)
        if line.startswith("[sync]") or line.startswith("Managed sync"):
            log.info("gbrain: %s", line.strip()[:200])
    result = _json_tail(stdout)
    outcome, status = result.get("outcome"), result.get("sync_status")
    if outcome != "synced" or (status is not None and status not in _OK_SYNC_STATUS):
        detail = json.dumps({k: result.get(k) for k in ("sync_status", "outcome", "error", "message") if result.get(k)},
                            ensure_ascii=False)
        raise RuntimeError(f"gbrain sync 결과가 synced가 아님: {detail[:300] or '(JSON 없음)'}")
    summary = {k: result.get(k) for k in ("sync_status", "added", "modified", "deleted", "chunks_created", "embedded")}
    waived = (result.get("drain") or {}).get("waived")
    if waived:
        summary["waived"] = waived
    log.info("sync source=%s → %s", GBRAIN_SOURCE_ID, summary)
    return summary


async def sync_decision_to_gbrain(
    decision: Decision, project: Project | None = None, sources: dict[str, Source] | None = None, force: bool = False
) -> GbrainSyncResult:
    """Decision 1건 export + gbrain sync. 예외를 내지 않는다. 상태는 decision_knowledge_sync(target=gbrain)에 기록.
    force=True: 내용 hash가 같아도 sync를 다시 실행한다 (같은 delegated sync. import는 쓰지 않는다)."""
    if not GBRAIN_SYNC_ENABLED:
        return GbrainSyncResult("disabled", "GBRAIN_SYNC_ENABLED=false")
    if not is_exportable(decision):
        return GbrainSyncResult("skipped", "human_verified=false인 Decision은 gbrain으로 보내지 않습니다")
    try:
        if project is None:
            project = db.get_project(decision.project_id)
        if sources is None:
            sources = {s.id: s for s in db.list_sources(decision.project_id)}
        path, digest = export_decision_to_gbrain(decision, project, sources)
    except Exception as e:  # noqa: BLE001
        db.record_knowledge_sync(decision.id, TARGET, "failed", error=f"Markdown export 실패: {e}")
        log.warning("%s export 실패: %s", decision.id, e)
        return GbrainSyncResult("failed", f"Markdown export 실패: {e}")

    prev = db.get_knowledge_sync(decision.id, TARGET)
    if not force and prev and prev["status"] == "synced" and prev["content_hash"] == digest:
        return GbrainSyncResult("unchanged", "내용이 바뀌지 않아 sync를 생략했습니다", str(path))
    try:
        async with _LOCK:
            summary = await _gbrain_source_sync()
    except Exception as e:  # noqa: BLE001 — gbrain/embedding 실패는 기록만 (Markdown 파일과 승인은 유지)
        db.record_knowledge_sync(decision.id, TARGET, "failed", document_ref=str(path), error=str(e))
        log.warning("%s gbrain sync 실패: %s", decision.id, e)
        return GbrainSyncResult("failed", str(e), str(path))
    db.record_knowledge_sync(decision.id, TARGET, "synced", content_hash=digest, document_ref=str(path))
    log.info("%s gbrain sync 완료 (%s)", decision.id, path.name)
    return GbrainSyncResult("synced", f"gbrain sync 완료 ({summary.get('sync_status')})", str(path))


async def sync_after_approve(decision_id: str) -> None:
    """Approve 응답 이후 background로 실행. 승인(SQLite)은 이미 끝난 상태다."""
    decision = db.get_decision(decision_id)
    if decision is not None:
        await sync_decision_to_gbrain(decision)
