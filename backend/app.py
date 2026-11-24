"""Decision Debt MVP backend.

실행: uvicorn app:app --host 127.0.0.1 --port 8001
(포트 8000은 기존 ~/decision-debt-tool check_metric 서버 — Dify Agent가 사용 중)

모든 조회/생성 API는 project 범위로 동작한다. 목록 조회는 ?project_id=, 생성은 body의 project_id가 필수다.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile

import config
import db
from config import AGENT_SOURCES, EXTRACTION_MODE, KNOWN_METRICS, METRIC_SOURCES
from schemas import (
    DeepReviewRun,
    AgentChatRequest,
    AgentChatResponse,
    ApproveResponse,
    Candidate,
    CandidateDeleteRequest,
    CandidateDeleteResponse,
    CandidateUpdate,
    DecisionView,
    DemoChange,
    DemoPreview,
    DemoState,
    DemoUpdateRequest,
    ExtractRequest,
    GbrainSyncStatus,
    ExtractResponse,
    Metric,
    MetricsResponse,
    Project,
    ProjectCreate,
    ReviewEvent,
    ReviewMemo,
    ReviewMemoRequest,
    Source,
    SourceCreate,
    SourceTitleUpdate,
)
from services import demo_state, dify, extraction
from services.knowledge_sync import sync_after_approve
from services import audio_ingest, gbrain_sync, source_title
from services import metrics as metric_service
from services import deep_review, review_memo, review_monitor
from services.review import to_view, trigger_metric_names


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    interrupted = db.fail_interrupted_transcriptions()  # 재시작 전에 전사 중이던 오디오 → failed (다시 전사 가능)
    if interrupted:
        print(f"[audio] 전사 중이던 오디오 {interrupted}건을 failed로 표시 (다시 전사 필요)")
    stale_memos = db.fail_interrupted_review_memos()  # 재시작 전에 생성 중이던 Review Memo → failed
    if stale_memos:
        print(f"[review_memo] 생성 중이던 Review Memo {stale_memos}건을 failed로 표시 (다시 생성 필요)")
    # 재검토 조건 주기 확인 (REVIEW_MONITOR_ENABLED=false로 끌 수 있음). 같은 프로세스 안의 asyncio task
    task = asyncio.create_task(review_monitor.run_forever()) if review_monitor.STATUS["enabled"] else None
    # Deep Review 진행 확인 (Paperclip ticket → 검증 → 다음 ticket). 꺼져 있거나 Paperclip이 없어도 다른 기능에는 영향 없음
    deep_task = asyncio.create_task(deep_review.run_forever()) if deep_review.ENABLED else None
    yield
    for t in (task, deep_task):
        if t:
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t


app = FastAPI(title="Decision Debt MVP API", version="0.2.0", lifespan=lifespan)


def _project(project_id: int) -> Project:
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project not found: {project_id}")
    return project


def _external_model_allowed(project: Project) -> bool:
    """이 프로젝트의 원문을 외부 모델(OpenRouter)로 보내도 되는지. 지금은 모두 허용.
    민감한 프로젝트를 local only로 묶는 정책은 여기에 붙인다."""
    return True


def _allowed_metrics(project: Project) -> list[str]:
    # 현재 metric source는 기존 check_metric 하나뿐이다. 프로젝트별 source가 생기면 source별 지표 목록으로 바꾼다.
    return KNOWN_METRICS if project.metrics_connected else []


# ---------------------------------------------------------------- health / projects


@app.get("/api/health")
async def health():
    tools = {}
    for key, url in METRIC_SOURCES.items():
        ok, error = await metric_service.ping(url)
        tools[key] = {"url": url, "ok": ok, "error": error}
    return {"backend": "ok", "metric_sources": tools, "review_monitor": review_monitor.STATUS}


@app.get("/api/projects", response_model=list[Project])
def list_projects():
    return db.list_projects()


@app.post("/api/projects", response_model=Project, status_code=201)
def create_project(body: ProjectCreate):
    if not body.name.strip():
        raise HTTPException(422, "Project name is empty")
    return db.create_project(body.name, body.description)


@app.get("/api/projects/{project_id}", response_model=Project)
def get_project(project_id: int):
    return _project(project_id)


# ---------------------------------------------------------------- metrics


@app.get("/api/metrics", response_model=MetricsResponse)
async def list_metrics(project_id: int = Query(...)):
    project = _project(project_id)
    names = _allowed_metrics(project)
    found, errors = await metric_service.check_metrics(project, names)
    return MetricsResponse(
        project_id=project.id,
        connected=project.metrics_connected,
        metric_tool_url=metric_service.metric_base_url(project),
        metrics=[found[n] for n in names if n in found],
        errors={n: e.message for n, e in errors.items()},
    )


@app.get("/api/metrics/{metric_name}", response_model=Metric)
async def get_metric(metric_name: str, project_id: int = Query(...)):
    project = _project(project_id)
    try:
        return await metric_service.check_metric(project, metric_name)
    except metric_service.MetricNotConnected as e:
        raise HTTPException(409, str(e))
    except metric_service.MetricNotFound as e:
        raise HTTPException(404, str(e))
    except metric_service.MetricToolUnavailable as e:
        raise HTTPException(503, str(e))


# ---------------------------------------------------------------- decisions


@app.get("/api/decisions", response_model=list[DecisionView])
async def list_decisions(project_id: int = Query(...)):
    project = _project(project_id)
    decisions = db.list_decisions(project.id)
    found, errors = await metric_service.check_metrics(project, trigger_metric_names(decisions))
    return [to_view(d, found, errors) for d in decisions]


@app.get("/api/decisions/{decision_id}", response_model=DecisionView)
async def get_decision(decision_id: str):
    decision = db.get_decision(decision_id)
    if decision is None:
        raise HTTPException(404, f"Decision not found: {decision_id}")
    project = _project(decision.project_id)
    found, errors = await metric_service.check_metrics(project, trigger_metric_names([decision]))
    return to_view(decision, found, errors)


# ---------------------------------------------------------------- sources


@app.get("/api/sources", response_model=list[Source])
def list_sources(project_id: int = Query(...)):
    return db.list_sources(_project(project_id).id)


@app.post("/api/sources", response_model=Source, status_code=201)
def create_source(body: SourceCreate):
    project = _project(body.project_id)
    return db.create_source(project.id, body.title, body.type, body.content, body.date)


@app.post("/api/sources/audio", response_model=Source, status_code=202)
async def upload_audio_source(
    background: BackgroundTasks,
    project_id: int = Form(...),
    title: str = Form(..., min_length=1, max_length=200),
    file: UploadFile = File(...),
):
    """오디오 업로드 → 로컬 저장 → 로컬 STT를 백그라운드로 시작. 응답은 바로 (status=uploaded/transcribing).
    전사가 끝나면 Source 내용이 timestamp가 붙은 raw transcript가 되고, 기존 /api/extract로 추출한다."""
    project = _project(project_id)
    try:
        path, size = await audio_ingest.save_upload(project.id, file)
    except audio_ingest.UploadRejected as e:
        raise HTTPException(e.status, str(e))
    source = db.create_audio_source(project.id, title, file.filename or path.name, str(path), file.content_type, size)
    background.add_task(audio_ingest.run_transcription, source.id)
    return source


@app.post("/api/sources/{source_id}/transcribe", response_model=Source, status_code=202)
def retranscribe_audio_source(source_id: str, background: BackgroundTasks):
    """전사 실패(또는 대기) 상태의 오디오를 다시 전사한다. 원본 오디오는 그대로 둔다."""
    source = db.get_source(source_id)
    if source is None or source.audio is None:
        raise HTTPException(404, f"Audio source not found: {source_id}")
    if source.audio.transcription_status not in ("uploaded", "failed"):
        raise HTTPException(409, f"이미 {source.audio.transcription_status} 상태입니다")
    background.add_task(audio_ingest.run_transcription, source_id)
    return source


@app.patch("/api/sources/{source_id}", response_model=Source)
def update_source_title(source_id: str, body: SourceTitleUpdate):
    """사람이 Source 제목을 정한다. 오디오 Source면 title_origin=user → 이후 추출이 자동 제목으로 덮어쓰지 않는다."""
    if db.get_source(source_id) is None:
        raise HTTPException(404, f"Source not found: {source_id}")
    if not body.title.strip():
        raise HTTPException(422, "title is empty")
    db.set_source_title(source_id, body.title, "user")
    return db.get_source(source_id)


@app.get("/api/sources/{source_id}", response_model=Source)
def get_source(source_id: str):
    source = db.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"Source not found: {source_id}")
    return source


# ---------------------------------------------------------------- review queue


@app.post("/api/extract", response_model=ExtractResponse)
async def extract_decisions(body: ExtractRequest):
    """원문(또는 등록된 Source)에서 Decision Candidate를 추출해 저장한다. Decision은 만들지 않는다."""
    project = _project(body.project_id)
    if body.source_id:
        source = db.get_source(body.source_id)
        if source is None:
            raise HTTPException(404, f"Source not found: {body.source_id}")
        if source.project_id != project.id:
            raise HTTPException(409, "Source belongs to a different project")
        # 오디오 Source는 전사가 끝난 뒤에만 추출한다 (전사 실패를 추출 실패로 섞지 않음)
        if source.audio is not None and source.audio.transcription_status != "ready":
            raise HTTPException(409, f"전사가 완료되지 않은 오디오 Source입니다 (status: {source.audio.transcription_status})")
        if not source.content.strip():
            raise HTTPException(409, "Source 내용이 비어 있습니다")
    elif body.title and body.content and body.content.strip():
        source = None
    else:
        raise HTTPException(422, "source_id 또는 title + content가 필요합니다")

    content = source.content if source else body.content
    mode = body.mode or (EXTRACTION_MODE if EXTRACTION_MODE in extraction.EXTRACTION_MODES else "local")
    info: dict = {}
    try:
        if mode == "auto":
            drafts, warnings, extractor = await extraction.extract_auto(
                content, _allowed_metrics(project), info, allow_external=_external_model_allowed(project)
            )
        else:
            drafts, warnings = await extraction.extract(content, _allowed_metrics(project), provider="local", stats=info)
            extractor = extraction.extractor_label("local", info.get("model") or extraction.EXTRACTION_MODEL)
            info = {"mode": "local", "final_provider": "local", "escalated": False, "escalation_reasons": []}
    except extraction.ExtractionUnavailable as e:
        raise HTTPException(503, str(e))
    except extraction.ExtractionError as e:
        raise HTTPException(502, str(e))

    # LLM 호출이 성공한 뒤에 Source를 등록한다 (실패 시 고아 Source를 남기지 않음)
    if source is None:
        source = db.create_source(project.id, body.title, body.type, body.content, None)
    # 결과는 항상 Candidate(사람 승인 대기)로만 저장한다. extractor에 최종 provider:model을 남긴다
    candidates = db.insert_candidates(project.id, source, drafts, extractor=extractor)
    # 오디오 Source: 파일명 제목 → 추출된 topic 기반 제목 (사람이 정한 제목은 유지). 실패해도 추출 결과에는 영향 없음
    if source.audio is not None and candidates:
        try:
            if source_title.update_audio_source_title(source.id):
                source = db.get_source(source.id)
        except Exception as e:  # noqa: BLE001
            print(f"[source_title] {source.id} 제목 갱신 실패: {type(e).__name__}: {e}")
    return ExtractResponse(source=source, candidates=candidates, warnings=warnings, extraction=info)


@app.get("/api/candidates", response_model=list[Candidate])
def list_candidates(
    project_id: int = Query(...),
    status: Literal["pending", "approved", "ignored", "processed"] | None = None,
):
    return db.list_candidates(_project(project_id).id, status)


def _candidate_op(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, f"Candidate not found: {args[0]}")
    except db.InvalidState as e:
        raise HTTPException(409, str(e))
    except db.ProjectScopeError as e:
        raise HTTPException(409, str(e))


@app.patch("/api/candidates/{candidate_id}", response_model=Candidate)
def update_candidate(candidate_id: str, body: CandidateUpdate):
    return _candidate_op(db.update_candidate, candidate_id, body)


@app.post("/api/candidates/{candidate_id}/ignore", response_model=Candidate)
def ignore_candidate(candidate_id: str):
    return _candidate_op(db.ignore_candidate, candidate_id)


@app.post("/api/candidates/delete", response_model=CandidateDeleteResponse)
def delete_candidates(body: CandidateDeleteRequest):
    """Processed 기록 정리: ignored 후보만 일괄 삭제 (approved 승인 기록, Decision, Source는 유지)"""
    deleted, skipped = db.delete_ignored_candidates(_project(body.project_id).id, body.ids)
    return CandidateDeleteResponse(deleted=deleted, skipped=skipped)


@app.post("/api/candidates/{candidate_id}/approve", response_model=ApproveResponse)
async def approve_candidate(candidate_id: str, background: BackgroundTasks):
    # 1) SQLite 승인 (Decision 저장 + candidate approved, 한 트랜잭션). 실패하면 여기서 끝나고 동기화하지 않는다.
    candidate, decision = _candidate_op(db.approve_candidate, candidate_id)
    # 2) 저장 성공 후에만 Knowledge 동기화. 실패해도 예외 없이 결과만 돌려주므로 승인은 유지된다.
    project = db.get_project(decision.project_id)
    sources = {s.id: s for s in db.list_sources(decision.project_id)}
    knowledge_sync = await sync_after_approve(decision, project, sources)
    # 3) gbrain(파생 knowledge layer): 응답 이후 background로 Markdown export + import. 실패해도 승인은 유지
    if not gbrain_sync.GBRAIN_SYNC_ENABLED:
        gbrain = GbrainSyncStatus(status="disabled", message="GBRAIN_SYNC_ENABLED=false")
    elif not gbrain_sync.is_exportable(decision):
        gbrain = GbrainSyncStatus(status="skipped", message="human_verified=false")
    else:
        db.record_knowledge_sync(decision.id, gbrain_sync.TARGET, "pending")
        background.add_task(gbrain_sync.sync_after_approve, decision.id)
        gbrain = GbrainSyncStatus(status="pending", message="승인 후 gbrain 동기화를 시작했습니다")
    return ApproveResponse(candidate=candidate, decision=decision, knowledge_sync=knowledge_sync, gbrain_sync=gbrain)


# ---------------------------------------------------------------- review events (review monitor가 감지한 상태 변화)


@app.get("/api/review-events", response_model=list[ReviewEvent])
def list_review_events(
    project_id: int = Query(...),
    acknowledged: bool | None = None,
    new_status: Literal["ACTIVE", "REVIEW_DUE", "UNKNOWN"] | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    return db.list_review_events(_project(project_id).id, acknowledged, new_status, limit)


@app.post("/api/review-events/{event_id}/acknowledge", response_model=ReviewEvent)
def acknowledge_review_event(event_id: int):
    """알림 확인. 이벤트만 숨긴다 (Decision 상태는 바꾸지 않음)"""
    try:
        return db.acknowledge_review_event(event_id)
    except KeyError:
        raise HTTPException(404, f"Review event not found: {event_id}")


# ---------------------------------------------------------------- review memo (advisory. Decision/review 상태를 바꾸지 않는다)


@app.post("/api/decisions/{decision_id}/review-memo", response_model=ReviewMemo)
async def create_review_memo(decision_id: str, background: BackgroundTasks, response: Response,
                             body: ReviewMemoRequest | None = None):
    """현재 REVIEW_DUE인 결정의 Review Memo를 Hermes로 생성한다 (background). 응답은 바로: 202 + status=generating.
    같은 review 상태의 완료 memo가 있으면 새로 만들지 않고 200으로 그것을 돌려준다 (regenerate=true면 새 행)."""
    try:
        memo, created = review_memo.start(decision_id, regenerate=bool(body and body.regenerate))
    except review_memo.ReviewMemoError as e:
        raise HTTPException(e.status, e.detail())
    if created:
        background.add_task(review_memo.run, memo.id)
        response.status_code = 202
    return memo


@app.get("/api/decisions/{decision_id}/review-memos", response_model=list[ReviewMemo])
def list_review_memos(decision_id: str):
    if db.get_decision(decision_id) is None:
        raise HTTPException(404, f"Decision not found: {decision_id}")
    return db.list_review_memos(decision_id)


# ---------------------------------------------------------------- Deep Review (Paperclip multi-agent, advisory)


@app.get("/api/deep-review/status")
def deep_review_status():
    """화면에서 Run Deep Review 버튼 표시 여부. 꺼져 있으면 enabled=false (다른 기능과 무관)"""
    return {"enabled": deep_review.ENABLED, "monitor": deep_review.STATUS}


@app.post("/api/decisions/{decision_id}/deep-review", response_model=DeepReviewRun, status_code=202)
async def create_deep_review(decision_id: str, background: BackgroundTasks, project_id: int | None = None):
    """현재 REVIEW_DUE인 결정의 Deep Review를 시작한다 (Paperclip: Researcher → Analyst → Critic).
    응답은 바로 202 + PENDING. 진행 중 run이 있으면 409. Decision은 바뀌지 않는다."""
    try:
        run = deep_review.start(decision_id, project_id)
    except deep_review.DeepReviewError as e:
        raise HTTPException(e.status, e.detail())
    background.add_task(asyncio.to_thread, deep_review.kickoff, run["id"])
    return deep_review.view(run)


@app.get("/api/decisions/{decision_id}/deep-reviews", response_model=list[DeepReviewRun])
def list_deep_reviews(decision_id: str):
    if db.get_decision(decision_id) is None:
        raise HTTPException(404, f"Decision not found: {decision_id}")
    return [deep_review.view(r) for r in db.list_deep_review_runs(decision_id)]


@app.get("/api/deep-reviews/{run_id}", response_model=DeepReviewRun)
def get_deep_review(run_id: int):
    run = db.get_deep_review_run(run_id)
    if run is None:
        raise HTTPException(404, f"Deep review not found: {run_id}")
    return deep_review.view(run)


@app.get("/api/review-memos/{memo_id}", response_model=ReviewMemo)
def get_review_memo(memo_id: int):
    memo = db.get_review_memo(memo_id)
    if memo is None:
        raise HTTPException(404, f"Review memo not found: {memo_id}")
    return memo


# ---------------------------------------------------------------- ask agent


@app.post("/api/agent/chat", response_model=AgentChatResponse)
async def agent_chat(req: AgentChatRequest):
    """프로젝트에 연결된 Dify Chatflow(RAG + check_metric) proxy. API Key는 backend에만 있다.

    Knowledge/Agent가 연결되지 않은 프로젝트는 Dify를 호출하지 않는다
    (다른 프로젝트의 Knowledge를 근거로 답하지 않기 위해).
    """
    project = _project(req.project_id)
    agent = AGENT_SOURCES.get(project.agent_source) if project.agent_source else None
    if agent is None:
        raise HTTPException(409, f"'{project.name}' 프로젝트에는 아직 Knowledge/Agent가 연결되지 않았습니다.")
    if not req.query.strip():
        raise HTTPException(422, "query is empty")
    try:
        return await dify.chat(agent, req.query.strip(), req.conversation_id)
    except dify.DifyNotConfigured as e:
        raise HTTPException(500, str(e))
    except dify.DifyUnavailable as e:
        raise HTTPException(503, str(e))
    except dify.DifyError as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------- demo control center (발표/개발 전용)
# DEMO_MODE_ENABLED=false면 조회까지 포함해 모두 404 (기능이 없는 것처럼). frontend에서 숨기는 것만으로 막지 않는다.
# 바꾸는 것은 check_metric이 읽는 sample DB 값뿐이다. REVIEW_DUE / Review Event / Telegram은 Review Monitor가 처리한다.


def _demo_enabled():
    if not config.DEMO_MODE_ENABLED:
        raise HTTPException(404, "Not Found")


def _demo_error(e: Exception) -> HTTPException:
    if isinstance(e, demo_state.UnknownMetric):
        return HTTPException(404, f"Unknown metric: {e}")
    if isinstance(e, demo_state.NotSimulatorBacked):
        return HTTPException(409, str(e))
    if isinstance(e, demo_state.InvalidValue):
        return HTTPException(422, str(e))
    if isinstance(e, demo_state.SourceUnavailable):
        return HTTPException(503, str(e))
    raise e


@app.get("/api/demo/status", dependencies=[Depends(_demo_enabled)])
def demo_status():
    """웹 Demo 메뉴 표시 여부 확인용 (꺼져 있으면 404)"""
    return {"enabled": True}


@app.get("/api/demo/state", response_model=DemoState, dependencies=[Depends(_demo_enabled)])
async def demo_get_state():
    return await demo_state.get_state()


@app.get("/api/demo/state/{metric_name}/preview", response_model=DemoPreview, dependencies=[Depends(_demo_enabled)])
async def demo_preview(metric_name: str, value: float = Query(...)):
    try:
        return await demo_state.preview(metric_name, value)
    except (demo_state.UnknownMetric, demo_state.InvalidValue, demo_state.SourceUnavailable) as e:
        raise _demo_error(e)


@app.patch("/api/demo/state/{metric_name}", response_model=DemoChange, dependencies=[Depends(_demo_enabled)])
async def demo_set_state(metric_name: str, req: DemoUpdateRequest):
    try:
        return await demo_state.set_value(metric_name, req.value)
    except (demo_state.UnknownMetric, demo_state.NotSimulatorBacked, demo_state.InvalidValue, demo_state.SourceUnavailable) as e:
        raise _demo_error(e)


@app.post("/api/demo/reset", response_model=list[DemoChange], dependencies=[Depends(_demo_enabled)])
async def demo_reset():
    try:
        return await demo_state.reset()
    except (demo_state.NotSimulatorBacked, demo_state.InvalidValue, demo_state.SourceUnavailable) as e:
        raise _demo_error(e)
