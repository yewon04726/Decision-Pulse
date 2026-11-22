"""SQLite persistence.

스키마 버전은 PRAGMA user_version으로 관리한다.
- v0: 단일 프로젝트 (sources, decisions, decision_sources)
- v1: projects + 모든 데이터에 project_id, Review Queue candidates, decisions.origin, sources.human_authored
- v2: review monitor (decision_review_state: 결정별 마지막 판정 상태, review_events: 상태 변화 이력). 기존 테이블은 그대로
- v3: source_audio (오디오 Source의 원본 파일 경로/전사 상태). 기존 테이블은 그대로
- v4: decision_knowledge_sync (승인된 Decision의 외부 knowledge layer 동기화 상태. 지금은 target=gbrain)
- v5: decision_review_memos (REVIEW_DUE 결정에 대한 AI Review Memo. advisory 산출물이며 Decision/review 상태는 바꾸지 않는다)
- v6: source_audio.title_origin (오디오 Source 제목의 출처: filename | auto_topics | user. user 제목은 자동으로 바꾸지 않는다)

init_db()는 빈 DB면 최신 스키마 + seed를, v0 DB면 백업 후 v1으로, v1 DB면 백업 후 v2 테이블을 추가한다.
"""

import json
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config import AGENT_SOURCES, DB_PATH, DEFAULT_PROJECT, METRIC_SOURCES, SEED_PATH
from services.review_trigger import normalize_review_trigger
from schemas import (
    Candidate,
    CandidateUpdate,
    Decision,
    Evidence,
    Project,
    ReviewEvent,
    ReviewMemo,
    Revision,
    Source,
    SourceAudio,
)

SCHEMA_VERSION = 6

SCHEMA_V1 = """
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    metric_source TEXT,
    agent_source TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    date TEXT,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    human_authored INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE decisions (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    notes_json TEXT NOT NULL,
    review_trigger_json TEXT,
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    human_verified INTEGER NOT NULL,
    superseded_by TEXT,
    history_json TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE decision_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    evidence_text TEXT NOT NULL
);
CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    review_trigger_json TEXT,
    evidence TEXT NOT NULL,
    evidence_in_source INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    edited INTEGER NOT NULL DEFAULT 0,
    decision_id TEXT REFERENCES decisions(id),
    extractor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_sources_project ON sources(project_id);
CREATE INDEX idx_decisions_project ON decisions(project_id);
CREATE INDEX idx_candidates_project ON candidates(project_id, status);
"""

# review monitor. decisions.status(업무 lifecycle)는 건드리지 않고 판정 결과는 별도 테이블에 둔다.
# decision_id에는 FK를 걸지 않는다 (이력 데이터라 결정을 정리해도 남아 있어야 함).
SCHEMA_V2 = """
CREATE TABLE decision_review_state (
    decision_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    last_review_status TEXT NOT NULL,     -- 마지막으로 확정된 판정: ACTIVE | REVIEW_DUE (변화 감지 기준)
    last_check_status TEXT NOT NULL,      -- 마지막 확인 결과: ACTIVE | REVIEW_DUE | UNKNOWN
    last_observed_json TEXT,
    last_error TEXT,
    last_checked_at TEXT NOT NULL,
    last_changed_at TEXT NOT NULL
);
CREATE TABLE review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    decision_id TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    trigger_json TEXT,
    observed_json TEXT,
    detected_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    acknowledged_at TEXT,
    resolved_at TEXT                      -- REVIEW_DUE 이벤트 이후 조건이 다시 풀린 시각 (복구 감지 시)
);
CREATE INDEX idx_review_events_project ON review_events(project_id, acknowledged, new_status);
"""

# 오디오 Source. 원본 오디오는 로컬 파일(audio_path)로 두고 BLOB으로 넣지 않는다. 전사 결과는 sources.content에 들어간다.
SCHEMA_V3 = """
CREATE TABLE source_audio (
    source_id TEXT PRIMARY KEY REFERENCES sources(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    original_filename TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    transcription_status TEXT NOT NULL,   -- uploaded | transcribing | ready | failed
    transcription_engine TEXT,
    transcription_model TEXT,
    language TEXT,
    duration_seconds REAL,
    transcription_seconds REAL,
    rtf REAL,
    segments INTEGER,
    segments_json TEXT,                   -- whisper 원본 segment (시작/끝 ms + 문장)
    error_kind TEXT,
    error TEXT,
    uploaded_at TEXT NOT NULL,
    transcribed_at TEXT
);
"""

# 승인된 Decision → 외부 knowledge layer 동기화 상태 (decisions 테이블은 바꾸지 않는다). target별 1행
SCHEMA_V4 = """
CREATE TABLE decision_knowledge_sync (
    decision_id TEXT NOT NULL,
    target TEXT NOT NULL,                 -- gbrain
    status TEXT NOT NULL,                 -- pending | synced | failed
    content_hash TEXT,                    -- 마지막으로 import에 성공한 Markdown의 sha256 (같으면 import 생략)
    document_ref TEXT,                    -- 예: Markdown 파일 경로 / gbrain slug
    attempted_at TEXT,
    synced_at TEXT,
    error TEXT,
    PRIMARY KEY (decision_id, target)
);
"""

# REVIEW_DUE 결정에 대한 Review Memo (Hermes 생성, advisory). 생성 시점의 review 상태/근거를 snapshot으로 함께 저장해
# 나중에 metric이 바뀌어도 memo의 의미가 조용히 바뀌지 않게 한다. 재생성은 새 행 (이전 memo는 그대로 둔다).
# decision_id에는 FK를 걸지 않는다 (이력 데이터). 결정당 generating은 1개만 (partial unique index → 중복 실행 방지)
SCHEMA_V5 = """
CREATE TABLE decision_review_memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    decision_id TEXT NOT NULL,
    review_event_id INTEGER,              -- 생성 시점에 열려 있던 REVIEW_DUE 이벤트 (baseline due면 NULL)
    review_state_changed_at TEXT,         -- 생성 시점의 decision_review_state.last_changed_at (REVIEW_DUE가 된 시각)
    detected_by TEXT NOT NULL,            -- review_event | review_state_baseline
    status TEXT NOT NULL,                 -- generating | completed | failed
    content TEXT,                         -- Markdown (completed일 때만)
    prompt_version TEXT NOT NULL,
    observed_snapshot_json TEXT,          -- 생성 시점의 trigger / 관측값 / 감지 방식
    source_snapshot_json TEXT,            -- 생성 시점의 sources / evidence
    model TEXT,
    provider TEXT,
    agent_session_id TEXT,                -- Hermes session id (tool 사용 기록 확인용)
    tool_audit_json TEXT,                 -- Hermes가 실제 호출한 tool 목록 + 허용 여부
    error TEXT,
    created_at TEXT NOT NULL,
    generated_at TEXT
);
CREATE INDEX idx_review_memos_decision ON decision_review_memos(decision_id, id);
CREATE UNIQUE INDEX idx_review_memos_one_generating ON decision_review_memos(decision_id) WHERE status = 'generating';
"""

# 오디오 Source 제목의 출처. filename: 업로드 파일명(stem) 그대로 / auto_topics: 추출된 Candidate topic으로 자동 생성 /
# user: 사람이 직접 입력 → 자동 제목으로 덮어쓰지 않는다. 오디오 Source만 해당 (텍스트 Source 제목은 건드리지 않음)
SCHEMA_V6 = """
ALTER TABLE source_audio ADD COLUMN title_origin TEXT NOT NULL DEFAULT 'filename';
"""


def _read_trigger(raw_json: str | None):
    """review_trigger_json → V2 모델. legacy/깨진 값도 예외 없이 해석한다 (DB 값은 그대로 둔다)."""
    if not raw_json:
        return None
    return normalize_review_trigger(raw_json)


class ProjectScopeError(Exception):
    """다른 프로젝트의 데이터를 연결하려는 경우"""


class InvalidState(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().date().isoformat()


# True면 모든 연결을 SQLite read-only(mode=ro)로 연다. 조회 전용 프로세스(mcp_server.py)가 켠다 → 쓰기는 SQLite가 거부
READ_ONLY = False


@contextmanager
def connect():
    conn = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True) if READ_ONLY else sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- init / migration


def init_db():
    with connect() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    if version == 0 and "decisions" in tables:
        _migrate_v0_to_v1()
        version = 1
    elif version == 0:
        with connect() as conn:
            conn.executescript(SCHEMA_V1 + SCHEMA_V2 + SCHEMA_V3 + SCHEMA_V4 + SCHEMA_V5 + SCHEMA_V6)
            pid = _insert_default_project(conn)
            _seed(conn, pid)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    if version == 1:
        _migrate_v1_to_v2()
        version = 2
    if version == 2:
        _migrate_v2_to_v3()
        version = 3
    if version == 3:
        _migrate_v3_to_v4()
        version = 4
    if version == 4:
        _migrate_v4_to_v5()
        version = 5
    if version == 5:
        _migrate_v5_to_v6()


def _audio_title_origin(title: str, original_filename: str) -> str:
    """업로드 때 받은 제목이 파일명(또는 stem) 그대로면 filename, 아니면 사람이 입력한 제목(user)"""
    name = (original_filename or "").strip()
    return "filename" if title.strip() in (name, Path(name).stem) else "user"


def _migrate_v5_to_v6():
    """오디오 Source 제목 출처 컬럼 추가 (Source 내용/제목은 바꾸지 않는다). 기존 행은 파일명과 비교해 filename/user로 분류. 백업 후 진행."""
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.v5-backup-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup)
    print(f"[migration] v5 → v6 (source_audio.title_origin), backup: {backup}")
    with connect() as conn:
        conn.executescript(SCHEMA_V6)
        rows = conn.execute(
            "SELECT a.source_id, a.original_filename, s.title FROM source_audio a JOIN sources s ON s.id = a.source_id"
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE source_audio SET title_origin = ? WHERE source_id = ?",
                         (_audio_title_origin(r["title"], r["original_filename"]), r["source_id"]))
        conn.execute("PRAGMA user_version = 6")


def _migrate_v4_to_v5():
    """Review Memo 테이블 추가 (기존 데이터는 바꾸지 않는다). 백업 후 진행."""
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.v4-backup-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup)
    print(f"[migration] v4 → v5 (decision_review_memos), backup: {backup}")
    with connect() as conn:
        conn.executescript(SCHEMA_V5)
        conn.execute("PRAGMA user_version = 5")


def _migrate_v3_to_v4():
    """knowledge layer 동기화 상태 테이블 추가 (기존 데이터는 바꾸지 않는다). 백업 후 진행."""
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.v3-backup-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup)
    print(f"[migration] v3 → v4 (decision_knowledge_sync), backup: {backup}")
    with connect() as conn:
        for stmt in SCHEMA_V4.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.execute("PRAGMA user_version = 4")


def _migrate_v2_to_v3():
    """오디오 Source 테이블 추가 (기존 데이터는 바꾸지 않는다). 백업 후 진행."""
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.v2-backup-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup)
    print(f"[migration] v2 → v3 (source_audio), backup: {backup}")
    with connect() as conn:
        for stmt in SCHEMA_V3.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.execute("PRAGMA user_version = 3")


def _migrate_v1_to_v2():
    """review monitor 테이블 추가 (기존 데이터는 바꾸지 않는다). 백업 후 진행."""
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.v1-backup-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup)
    print(f"[migration] v1 → v2 (review monitor tables), backup: {backup}")
    with connect() as conn:
        for stmt in SCHEMA_V2.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.execute("PRAGMA user_version = 2")


def _insert_default_project(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO projects (name, slug, description, metric_source, agent_source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            DEFAULT_PROJECT["name"],
            DEFAULT_PROJECT["slug"],
            DEFAULT_PROJECT["description"],
            "check_metric_default",  # 기존 check_metric 서버 = 이 프로젝트의 실제 metric
            "dify_default",  # Dify Knowledge v4 + Agent = 이 프로젝트의 Knowledge
            _now(),
        ),
    )
    return cur.lastrowid


def _migrate_v0_to_v1():
    """v0 DB를 보존하면서 v1으로 변환. 모든 기존 데이터는 기본 프로젝트에 소속된다.

    v0에는 Review Queue가 없었으므로 기존 결정은 모두 문서에서 가져온 seed다
    → origin='seed_document', human_verified=0, sources.human_authored=1.
    """
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.v0-backup-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup)
    print(f"[migration] v0 → v1, backup: {backup}")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE sources RENAME TO sources_v0")
        conn.execute("ALTER TABLE decisions RENAME TO decisions_v0")
        conn.execute("ALTER TABLE decision_sources RENAME TO decision_sources_v0")
        for stmt in SCHEMA_V1.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        pid = _insert_default_project(conn)
        conn.execute(
            """INSERT INTO sources (id, project_id, title, type, date, summary, content, human_authored, created_at)
               SELECT id, ?, title, type, date, summary, content, 1, NULL FROM sources_v0""",
            (pid,),
        )
        conn.execute(
            """INSERT INTO decisions (id, project_id, topic, title, decision, rationale, assumptions_json, notes_json,
                   review_trigger_json, status, origin, human_verified, superseded_by, history_json, created_at, updated_at)
               SELECT id, ?, topic, title, decision, rationale, assumptions_json, notes_json,
                   review_trigger_json, status, 'seed_document', 0, superseded_by, history_json, created_at, updated_at
               FROM decisions_v0""",
            (pid,),
        )
        conn.execute(
            "INSERT INTO decision_sources (id, decision_id, source_id, evidence_text) "
            "SELECT id, decision_id, source_id, evidence_text FROM decision_sources_v0"
        )
        for t in ("decision_sources_v0", "decisions_v0", "sources_v0"):
            conn.execute(f"DROP TABLE {t}")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    with connect() as c:
        problems = c.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"migration foreign key check failed: {problems}")


def _seed(conn: sqlite3.Connection, project_id: int):
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for s in seed["sources"]:
        _insert_source(conn, Source(**s, project_id=project_id))
    for d in seed["decisions"]:
        insert_decision(conn, Decision(**d, project_id=project_id))


# ---------------------------------------------------------------- projects


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        **dict(row),
        metrics_connected=row["metric_source"] in METRIC_SOURCES,
        agent_connected=row["agent_source"] in AGENT_SOURCES,
    )


def list_projects() -> list[Project]:
    with connect() as conn:
        return [_row_to_project(r) for r in conn.execute("SELECT * FROM projects ORDER BY id")]


def get_project(project_id: int) -> Project | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _row_to_project(row) if row else None


def _unique_slug(conn: sqlite3.Connection, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_project(name: str, description: str) -> Project:
    """새 프로젝트는 metric / Knowledge가 연결되지 않은 상태로 만든다."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, slug, description, metric_source, agent_source, created_at) VALUES (?, ?, ?, NULL, NULL, ?)",
            (name.strip(), _unique_slug(conn, name), description.strip(), _now()),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_project(row)


# ---------------------------------------------------------------- sources


def _insert_source(conn: sqlite3.Connection, s: Source):
    conn.execute(
        "INSERT INTO sources (id, project_id, title, type, date, summary, content, human_authored, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (s.id, s.project_id, s.title, s.type, s.date, s.summary, s.content, int(s.human_authored), _now()),
    )


_AUDIO_FIELDS = (
    "original_filename", "content_type", "size_bytes", "transcription_status", "transcription_engine",
    "transcription_model", "language", "duration_seconds", "transcription_seconds", "rtf", "segments",
    "error_kind", "error", "uploaded_at", "transcribed_at", "title_origin",
)


def _row_to_source(row: sqlite3.Row, audio: sqlite3.Row | None = None) -> Source:
    d = dict(row)
    d.pop("created_at", None)
    if audio is not None:
        d["audio"] = SourceAudio(**{k: audio[k] for k in _AUDIO_FIELDS})
    return Source(**d)


def _audio_rows(conn: sqlite3.Connection, source_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not source_ids:
        return {}
    marks = ",".join("?" * len(source_ids))
    rows = conn.execute(f"SELECT * FROM source_audio WHERE source_id IN ({marks})", source_ids).fetchall()
    return {r["source_id"]: r for r in rows}


def create_source(project_id: int, title: str, type_: str, content: str, date: str | None) -> Source:
    first_line = next((l.strip() for l in content.splitlines() if l.strip()), title)
    src = Source(
        id=f"src-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        title=title.strip(),
        type=type_,
        date=date,
        summary=first_line[:120],
        content=content,
        human_authored=True,  # 사용자가 직접 입력/업로드한 원문
    )
    with connect() as conn:
        _insert_source(conn, src)
    return src


def list_sources(project_id: int) -> list[Source]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sources WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
        audio = _audio_rows(conn, [r["id"] for r in rows])
        return [_row_to_source(r, audio.get(r["id"])) for r in rows]


def get_source(source_id: str) -> Source | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _row_to_source(row, _audio_rows(conn, [source_id]).get(source_id)) if row else None


# ---------------------------------------------------------------- decisions


def insert_decision(conn: sqlite3.Connection, d: Decision):
    # Evidence는 같은 프로젝트의 Source만 연결할 수 있다
    for e in d.evidence:
        row = conn.execute("SELECT project_id FROM sources WHERE id = ?", (e.source_id,)).fetchone()
        if row is None or row["project_id"] != d.project_id:
            raise ProjectScopeError(f"source {e.source_id} is not in project {d.project_id}")
    conn.execute(
        "INSERT INTO decisions (id, project_id, topic, title, decision, rationale, assumptions_json, notes_json, "
        "review_trigger_json, status, origin, human_verified, superseded_by, history_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            d.id,
            d.project_id,
            d.topic,
            d.title,
            d.decision,
            d.rationale,
            json.dumps(d.assumptions, ensure_ascii=False),
            json.dumps([n.model_dump() for n in d.notes], ensure_ascii=False),
            d.review_trigger.model_dump_json() if d.review_trigger else None,
            d.status,
            d.origin,
            int(d.human_verified),
            d.superseded_by,
            json.dumps([h.model_dump() for h in d.history], ensure_ascii=False),
            d.created_at,
            d.updated_at,
        ),
    )
    conn.executemany(
        "INSERT INTO decision_sources (decision_id, source_id, evidence_text) VALUES (?, ?, ?)",
        [(d.id, e.source_id, e.quote) for e in d.evidence],
    )


def _row_to_decision(conn: sqlite3.Connection, row: sqlite3.Row) -> Decision:
    evidence = conn.execute(
        "SELECT source_id, evidence_text FROM decision_sources WHERE decision_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    return Decision(
        id=row["id"],
        project_id=row["project_id"],
        topic=row["topic"],
        title=row["title"],
        decision=row["decision"],
        rationale=row["rationale"],
        assumptions=json.loads(row["assumptions_json"]),
        notes=json.loads(row["notes_json"]),
        review_trigger=_read_trigger(row["review_trigger_json"]),
        status=row["status"],
        origin=row["origin"],
        human_verified=bool(row["human_verified"]),
        evidence=[Evidence(source_id=e["source_id"], quote=e["evidence_text"]) for e in evidence],
        superseded_by=row["superseded_by"],
        history=json.loads(row["history_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_decisions(project_id: int) -> list[Decision]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at IS NULL, created_at, id", (project_id,)
        ).fetchall()
        return [_row_to_decision(conn, r) for r in rows]


def get_decision(decision_id: str) -> Decision | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return _row_to_decision(conn, row) if row else None


def _new_decision_id(conn: sqlite3.Connection, topic: str) -> str:
    tag = re.sub(r"[^A-Z0-9]+", "", topic.upper())[:12] or "DECISION"
    base = f"DEC-{datetime.now():%Y-%m%d}-{tag}"
    did, n = base, 2
    while conn.execute("SELECT 1 FROM decisions WHERE id = ?", (did,)).fetchone():
        did, n = f"{base}-{n}", n + 1
    return did


# ---------------------------------------------------------------- candidates


def _row_to_candidate(row: sqlite3.Row) -> Candidate:
    d = dict(row)
    return Candidate(
        **{k: d[k] for k in ("id", "project_id", "source_id", "topic", "title", "decision", "rationale",
                             "evidence", "status", "decision_id", "extractor", "created_at", "resolved_at")},
        source_title=d["source_title"],
        assumptions=json.loads(d["assumptions_json"]),
        review_trigger=_read_trigger(d["review_trigger_json"]),
        evidence_in_source=bool(d["evidence_in_source"]),
        edited=bool(d["edited"]),
    )


_CANDIDATE_SELECT = "SELECT c.*, s.title AS source_title FROM candidates c JOIN sources s ON s.id = c.source_id"


def insert_candidates(project_id: int, source: Source, drafts: list[dict], extractor: str) -> list[Candidate]:
    if source.project_id != project_id:
        raise ProjectScopeError(f"source {source.id} is not in project {project_id}")
    ids = []
    with connect() as conn:
        for d in drafts:
            cid = f"CAND-{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT INTO candidates (id, project_id, source_id, topic, title, decision, rationale, assumptions_json, "
                "review_trigger_json, evidence, evidence_in_source, status, edited, decision_id, extractor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?)",
                (
                    cid,
                    project_id,
                    source.id,
                    d["topic"],
                    d["title"],
                    d["decision"],
                    d["rationale"],
                    json.dumps(d["assumptions"], ensure_ascii=False),
                    json.dumps(d["review_trigger"], ensure_ascii=False) if d["review_trigger"] else None,
                    d["evidence"],
                    int(d["evidence_in_source"]),
                    extractor,
                    _now(),
                ),
            )
            ids.append(cid)
    return [get_candidate(i) for i in ids]


def list_candidates(project_id: int, status: str | None = None) -> list[Candidate]:
    with connect() as conn:
        if status == "processed":
            where, args = "c.project_id = ? AND c.status != 'pending'", (project_id,)
            order = "c.resolved_at DESC"
        elif status:
            where, args = "c.project_id = ? AND c.status = ?", (project_id, status)
            order = "c.created_at DESC"
        else:
            where, args = "c.project_id = ?", (project_id,)
            order = "c.created_at DESC"
        rows = conn.execute(f"{_CANDIDATE_SELECT} WHERE {where} ORDER BY {order}", args).fetchall()
        return [_row_to_candidate(r) for r in rows]


def get_candidate(candidate_id: str) -> Candidate | None:
    with connect() as conn:
        row = conn.execute(f"{_CANDIDATE_SELECT} WHERE c.id = ?", (candidate_id,)).fetchone()
        return _row_to_candidate(row) if row else None


def update_candidate(candidate_id: str, patch: CandidateUpdate) -> Candidate:
    with connect() as conn:
        row = conn.execute("SELECT status FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        if row["status"] != "pending":
            raise InvalidState(f"candidate {candidate_id} is already {row['status']}")
        sets, args = [], []
        for field in ("topic", "title", "decision", "rationale"):
            value = getattr(patch, field)
            if value is not None:
                sets.append(f"{field} = ?")
                args.append(value.strip())
        if patch.assumptions is not None:
            sets.append("assumptions_json = ?")
            args.append(json.dumps(patch.assumptions, ensure_ascii=False))
        if patch.clear_review_trigger:
            sets.append("review_trigger_json = NULL")
        elif patch.review_trigger is not None:
            sets.append("review_trigger_json = ?")
            args.append(patch.review_trigger.model_dump_json())
        if sets:
            sets.append("edited = 1")
            conn.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE id = ?", (*args, candidate_id))
    return get_candidate(candidate_id)


def ignore_candidate(candidate_id: str) -> Candidate:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE candidates SET status = 'ignored', resolved_at = ? WHERE id = ? AND status = 'pending'",
            (_now(), candidate_id),
        )
        if cur.rowcount == 0:
            _raise_not_pending(conn, candidate_id)
    return get_candidate(candidate_id)


def approve_candidate(candidate_id: str) -> tuple[Candidate, Decision]:
    """Candidate → Decision. 하나의 트랜잭션으로 Decision 저장과 Candidate 상태 변경을 함께 처리한다."""
    c = get_candidate(candidate_id)
    if c is None:
        raise KeyError(candidate_id)
    with connect() as conn:
        if c.status != "pending":
            raise InvalidState(f"candidate {candidate_id} is already {c.status}")
        today = _today()
        history = [
            Revision(date=c.created_at[:10], actor=f"Extraction ({c.extractor})", action="Candidate 추출", note=c.source_title),
        ]
        if c.edited:
            history.append(Revision(date=today, actor="사람 검토", action="Edit"))
        history.append(Revision(date=today, actor="사람 검토", action="Approve", note="Decision Memory 저장"))
        decision = Decision(
            id=_new_decision_id(conn, c.topic),
            project_id=c.project_id,
            topic=c.topic,
            title=c.title,
            decision=c.decision,
            rationale=c.rationale,
            assumptions=c.assumptions,
            notes=[],
            review_trigger=c.review_trigger,
            status="active",
            origin="review_approved",
            human_verified=True,
            evidence=[Evidence(source_id=c.source_id, quote=c.evidence)],
            created_at=today,
            updated_at=today,
            history=history,
        )
        insert_decision(conn, decision)
        cur = conn.execute(
            "UPDATE candidates SET status = 'approved', decision_id = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
            (decision.id, _now(), candidate_id),
        )
        if cur.rowcount == 0:  # 동시에 처리된 경우 Decision 저장도 롤백
            raise InvalidState(f"candidate {candidate_id} is no longer pending")
    return get_candidate(candidate_id), decision


def delete_ignored_candidates(project_id: int, candidate_ids: list[str]) -> tuple[list[str], list[dict]]:
    """현재 프로젝트의 ignored 후보만 삭제한다 (Review Queue 처리 기록 정리용).

    approved 후보는 Decision이 어떤 원문에서 승인됐는지 보여주는 승인 기록이라 삭제하지 않는다.
    Source 원문과 Decision은 건드리지 않는다. 조건에 맞지 않는 id는 건너뛰고 이유를 돌려준다.
    """
    deleted: list[str] = []
    skipped: list[dict] = []
    with connect() as conn:
        for cid in dict.fromkeys(candidate_ids):  # 중복 id 제거, 순서 유지
            row = conn.execute("SELECT project_id, status FROM candidates WHERE id = ?", (cid,)).fetchone()
            if row is None:
                skipped.append({"id": cid, "reason": "not_found"})
            elif row["project_id"] != project_id:
                skipped.append({"id": cid, "reason": "other_project"})
            elif row["status"] != "ignored":
                skipped.append({"id": cid, "reason": f"status_{row['status']}"})
            else:
                conn.execute("DELETE FROM candidates WHERE id = ? AND status = 'ignored'", (cid,))
                deleted.append(cid)
    return deleted, skipped


def _raise_not_pending(conn: sqlite3.Connection, candidate_id: str):
    row = conn.execute("SELECT status FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise KeyError(candidate_id)
    raise InvalidState(f"candidate {candidate_id} is already {row['status']}")


# ---------------------------------------------------------------- review monitor


def get_review_states(project_id: int) -> dict[str, sqlite3.Row]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM decision_review_state WHERE project_id = ?", (project_id,)).fetchall()
    return {r["decision_id"]: r for r in rows}


def record_review_check(
    project_id: int,
    decision_id: str,
    check_status: str,
    review_status: str,
    observed: dict | None,
    error: str | None,
    event: dict | None,
) -> int | None:
    """판정 결과 저장 (+ 상태 변화 이벤트). 한 트랜잭션. 생성된 이벤트 id를 돌려준다.

    review_status: 변화 감지 기준이 되는 확정 상태 (UNKNOWN이면 이전 확정 상태를 유지해서 넘긴다)
    event: {"previous_status", "new_status", "trigger"} — 상태가 바뀐 경우에만
    """
    now = _now()
    observed_json = json.dumps(observed, ensure_ascii=False) if observed is not None else None
    event_id = None
    with connect() as conn:
        conn.execute(
            """INSERT INTO decision_review_state (decision_id, project_id, last_review_status, last_check_status,
                   last_observed_json, last_error, last_checked_at, last_changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(decision_id) DO UPDATE SET
                   last_review_status = excluded.last_review_status,
                   last_check_status = excluded.last_check_status,
                   last_observed_json = COALESCE(excluded.last_observed_json, decision_review_state.last_observed_json),
                   last_error = excluded.last_error,
                   last_checked_at = excluded.last_checked_at,
                   last_changed_at = CASE WHEN decision_review_state.last_review_status != excluded.last_review_status
                                          THEN excluded.last_checked_at ELSE decision_review_state.last_changed_at END""",
            (decision_id, project_id, review_status, check_status, observed_json, error, now, now),
        )
        if event:
            if event["new_status"] == "ACTIVE":
                # 조건이 다시 풀림: 열려 있는 REVIEW_DUE 이벤트에 해소 시각 표시
                conn.execute(
                    "UPDATE review_events SET resolved_at = ? WHERE decision_id = ? AND new_status = 'REVIEW_DUE' AND resolved_at IS NULL",
                    (now, decision_id),
                )
            cur = conn.execute(
                """INSERT INTO review_events (project_id, decision_id, previous_status, new_status, trigger_json,
                       observed_json, detected_at, acknowledged, acknowledged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    decision_id,
                    event["previous_status"],
                    event["new_status"],
                    json.dumps(event.get("trigger"), ensure_ascii=False) if event.get("trigger") is not None else None,
                    observed_json,
                    now,
                    # 복구(→ ACTIVE)는 이력으로만 남긴다 (알림 대상 아님)
                    1 if event["new_status"] == "ACTIVE" else 0,
                    now if event["new_status"] == "ACTIVE" else None,
                ),
            )
            event_id = cur.lastrowid
    return event_id


def _row_to_review_event(row: sqlite3.Row) -> ReviewEvent:
    return ReviewEvent(
        id=row["id"],
        project_id=row["project_id"],
        decision_id=row["decision_id"],
        decision_title=row["decision_title"],
        previous_status=row["previous_status"],
        new_status=row["new_status"],
        trigger=json.loads(row["trigger_json"]) if row["trigger_json"] else None,
        observed=json.loads(row["observed_json"]) if row["observed_json"] else None,
        detected_at=row["detected_at"],
        acknowledged=bool(row["acknowledged"]),
        acknowledged_at=row["acknowledged_at"],
        resolved_at=row["resolved_at"],
    )


_EVENT_SELECT = """SELECT e.*, d.title AS decision_title FROM review_events e
                   LEFT JOIN decisions d ON d.id = e.decision_id"""


def list_review_events(
    project_id: int, acknowledged: bool | None = None, new_status: str | None = None, limit: int = 50
) -> list[ReviewEvent]:
    where, args = ["e.project_id = ?"], [project_id]
    if acknowledged is not None:
        where.append("e.acknowledged = ?")
        args.append(int(acknowledged))
    if new_status:
        where.append("e.new_status = ?")
        args.append(new_status)
    with connect() as conn:
        rows = conn.execute(
            f"{_EVENT_SELECT} WHERE {' AND '.join(where)} ORDER BY e.id DESC LIMIT ?", (*args, limit)
        ).fetchall()
    return [_row_to_review_event(r) for r in rows]


def acknowledge_review_event(event_id: int) -> ReviewEvent:
    """알림 확인 처리. Decision은 바꾸지 않는다."""
    with connect() as conn:
        conn.execute(
            "UPDATE review_events SET acknowledged = 1, acknowledged_at = ? WHERE id = ? AND acknowledged = 0",
            (_now(), event_id),
        )
        row = conn.execute(f"{_EVENT_SELECT} WHERE e.id = ?", (event_id,)).fetchone()
    if row is None:
        raise KeyError(event_id)
    return _row_to_review_event(row)


# ---------------------------------------------------------------- audio sources


def create_audio_source(
    project_id: int, title: str, original_filename: str, audio_path: str, content_type: str | None, size_bytes: int
) -> Source:
    """오디오 업로드 → Source(내용은 전사 후 채움) + source_audio(status=uploaded). 한 트랜잭션."""
    now = _now()
    src = Source(
        id=f"src-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        title=title.strip(),
        type="audio_transcript",
        date=_today(),
        summary=f"(전사 대기) {original_filename}"[:120],
        content="",
        human_authored=False,  # 사람이 쓴 문서가 아니라 STT 결과
    )
    with connect() as conn:
        _insert_source(conn, src)
        conn.execute(
            """INSERT INTO source_audio (source_id, project_id, original_filename, audio_path, content_type, size_bytes,
                   transcription_status, uploaded_at, title_origin) VALUES (?, ?, ?, ?, ?, ?, 'uploaded', ?, ?)""",
            (src.id, project_id, original_filename, audio_path, content_type, size_bytes, now,
             _audio_title_origin(src.title, original_filename)),
        )
    return get_source(src.id)


def list_source_candidate_topics(source_id: str) -> list[str]:
    """이 Source에서 추출된 Candidate의 topic (추출된 순서. 중복 포함 — 호출하는 쪽에서 unique 처리)"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT topic FROM candidates WHERE source_id = ? ORDER BY created_at, rowid", (source_id,)
        ).fetchall()
    return [r["topic"] for r in rows]


def set_source_title(source_id: str, title: str, origin: str) -> bool:
    """Source 제목 변경. 오디오 Source면 title_origin도 기록한다.
    origin=auto_topics는 사람이 정한 제목(title_origin=user)을 절대 덮어쓰지 않는다 (같은 트랜잭션 안에서 확인). 바뀌었으면 True."""
    with connect() as conn:
        audio = conn.execute("SELECT title_origin FROM source_audio WHERE source_id = ?", (source_id,)).fetchone()
        if origin == "auto_topics" and (audio is None or audio["title_origin"] == "user"):
            return False
        cur = conn.execute("UPDATE sources SET title = ? WHERE id = ?", (title.strip(), source_id))
        if audio is not None:
            conn.execute("UPDATE source_audio SET title_origin = ? WHERE source_id = ?", (origin, source_id))
    return cur.rowcount == 1


def get_audio_row(source_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM source_audio WHERE source_id = ?", (source_id,)).fetchone()


def start_transcription(source_id: str, engine: str, model: str, language: str) -> bool:
    """uploaded/failed → transcribing. 이미 전사 중이거나 완료면 False (중복 실행 방지)"""
    with connect() as conn:
        cur = conn.execute(
            """UPDATE source_audio SET transcription_status = 'transcribing', transcription_engine = ?,
                   transcription_model = ?, language = ?, error_kind = NULL, error = NULL
               WHERE source_id = ? AND transcription_status IN ('uploaded', 'failed')""",
            (engine, model, language, source_id),
        )
    return cur.rowcount == 1


def complete_transcription(source_id: str, transcript: str, segments_json: str, segments: int,
                           duration: float | None, elapsed: float, rtf: float | None):
    """전사 성공: Source 내용 = timestamp 포함 raw transcript (요약 아님)."""
    first_line = next((l.strip() for l in transcript.splitlines() if l.strip()), "")
    with connect() as conn:
        conn.execute("UPDATE sources SET content = ?, summary = ? WHERE id = ?", (transcript, first_line[:120], source_id))
        conn.execute(
            """UPDATE source_audio SET transcription_status = 'ready', duration_seconds = ?, transcription_seconds = ?,
                   rtf = ?, segments = ?, segments_json = ?, transcribed_at = ?, error_kind = NULL, error = NULL
               WHERE source_id = ?""",
            (duration, elapsed, rtf, segments, segments_json, _now(), source_id),
        )


def fail_transcription(source_id: str, kind: str, error: str, duration: float | None = None):
    with connect() as conn:
        conn.execute(
            """UPDATE source_audio SET transcription_status = 'failed', error_kind = ?, error = ?,
                   duration_seconds = COALESCE(?, duration_seconds) WHERE source_id = ?""",
            (kind, error[:1000], duration, source_id),
        )


def fail_interrupted_transcriptions() -> int:
    """backend 재시작 시 '전사 중'으로 남은 항목을 실패로 바꾼다 (다시 전사 버튼으로 재시도)."""
    with connect() as conn:
        cur = conn.execute(
            """UPDATE source_audio SET transcription_status = 'failed', error_kind = 'interrupted',
                   error = '전사 중 backend가 재시작되었습니다. 다시 전사하세요.' WHERE transcription_status = 'transcribing'"""
        )
    return cur.rowcount


# ---------------------------------------------------------------- knowledge layer sync 상태


def get_knowledge_sync(decision_id: str, target: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM decision_knowledge_sync WHERE decision_id = ? AND target = ?", (decision_id, target)
        ).fetchone()


def list_knowledge_sync(target: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM decision_knowledge_sync WHERE target = ? ORDER BY decision_id", (target,)).fetchall()


def record_knowledge_sync(
    decision_id: str, target: str, status: str, *, content_hash: str | None = None,
    document_ref: str | None = None, error: str | None = None,
):
    """동기화 상태 기록. synced일 때만 content_hash/synced_at을 갱신한다 (실패해도 마지막 성공 기록은 남김)."""
    now = _now()
    synced = status == "synced"
    with connect() as conn:
        conn.execute(
            """INSERT INTO decision_knowledge_sync (decision_id, target, status, content_hash, document_ref, attempted_at, synced_at, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(decision_id, target) DO UPDATE SET
                   status = excluded.status,
                   content_hash = CASE WHEN excluded.status = 'synced' THEN excluded.content_hash ELSE decision_knowledge_sync.content_hash END,
                   document_ref = COALESCE(excluded.document_ref, decision_knowledge_sync.document_ref),
                   attempted_at = excluded.attempted_at,
                   synced_at = CASE WHEN excluded.status = 'synced' THEN excluded.synced_at ELSE decision_knowledge_sync.synced_at END,
                   error = excluded.error""",
            (decision_id, target, status, content_hash if synced else None, document_ref, now, now if synced else None,
             (error or "")[:1000] or None),
        )



# ---------------------------------------------------------------- review memos (advisory. Decision/review 상태는 바꾸지 않는다)


class MemoInProgress(Exception):
    """같은 결정의 memo가 이미 생성 중 (partial unique index)"""


def _row_to_review_memo(row: sqlite3.Row) -> ReviewMemo:
    def _json(value):
        return json.loads(value) if value else None

    return ReviewMemo(
        id=row["id"],
        project_id=row["project_id"],
        decision_id=row["decision_id"],
        review_event_id=row["review_event_id"],
        review_state_changed_at=row["review_state_changed_at"],
        detected_by=row["detected_by"],
        status=row["status"],
        content=row["content"],
        prompt_version=row["prompt_version"],
        observed_snapshot=_json(row["observed_snapshot_json"]),
        source_snapshot=_json(row["source_snapshot_json"]),
        model=row["model"],
        provider=row["provider"],
        agent_session_id=row["agent_session_id"],
        tool_audit=_json(row["tool_audit_json"]),
        error=row["error"],
        created_at=row["created_at"],
        generated_at=row["generated_at"],
    )


def create_review_memo(
    project_id: int, decision_id: str, review_event_id: int | None, review_state_changed_at: str | None,
    detected_by: str, prompt_version: str, observed_snapshot: dict, source_snapshot: dict,
) -> ReviewMemo:
    """status=generating 행 생성. 같은 결정에 generating이 이미 있으면 MemoInProgress."""
    try:
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO decision_review_memos (project_id, decision_id, review_event_id, review_state_changed_at,
                       detected_by, status, prompt_version, observed_snapshot_json, source_snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?, 'generating', ?, ?, ?, ?)""",
                (project_id, decision_id, review_event_id, review_state_changed_at, detected_by, prompt_version,
                 json.dumps(observed_snapshot, ensure_ascii=False), json.dumps(source_snapshot, ensure_ascii=False), _now()),
            )
            memo_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise MemoInProgress(decision_id) from e
    return get_review_memo(memo_id)


def finish_review_memo(
    memo_id: int, status: str, *, content: str | None = None, model: str | None = None, provider: str | None = None,
    agent_session_id: str | None = None, tool_audit: dict | None = None, error: str | None = None,
) -> ReviewMemo:
    """generating → completed | failed. 이미 끝난 memo는 바꾸지 않는다."""
    with connect() as conn:
        conn.execute(
            """UPDATE decision_review_memos SET status = ?, content = ?, model = ?, provider = ?, agent_session_id = ?,
                   tool_audit_json = ?, error = ?, generated_at = ? WHERE id = ? AND status = 'generating'""",
            (status, content if status == "completed" else None, model, provider, agent_session_id,
             json.dumps(tool_audit, ensure_ascii=False) if tool_audit is not None else None,
             (error or "")[:1000] or None, _now(), memo_id),
        )
    return get_review_memo(memo_id)


def get_review_memo(memo_id: int) -> ReviewMemo | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM decision_review_memos WHERE id = ?", (memo_id,)).fetchone()
    return _row_to_review_memo(row) if row else None


def list_review_memos(decision_id: str) -> list[ReviewMemo]:
    """최신순. 재생성한 이전 memo도 모두 남아 있다."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decision_review_memos WHERE decision_id = ? ORDER BY id DESC", (decision_id,)
        ).fetchall()
    return [_row_to_review_memo(r) for r in rows]


def fail_interrupted_review_memos() -> int:
    """backend 재시작 시 '생성 중'으로 남은 memo → failed (Hermes 실행이 끊겼으므로)."""
    with connect() as conn:
        cur = conn.execute(
            """UPDATE decision_review_memos SET status = 'failed', generated_at = ?,
                   error = 'Review Memo 생성 중 backend가 재시작되었습니다. 다시 생성하세요.' WHERE status = 'generating'""",
            (_now(),),
        )
    return cur.rowcount
