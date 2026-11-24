from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictFloat, StrictInt, model_validator

from config import KNOWN_METRICS

# Review Trigger 비교 연산자 (V2에서 ==, != 추가)
Operator = Literal[">", ">=", "<", "<=", "==", "!="]
DecisionLifecycle = Literal["active", "pending_review", "superseded"]
# UNKNOWN: 재검토 조건은 있지만 metric을 확인할 수 없어 판정 불가 (서버 꺼짐 / 프로젝트에 metric 미연결)
DisplayStatus = Literal["ACTIVE", "REVIEW_DUE", "PENDING_REVIEW", "SUPERSEDED", "UNKNOWN"]
SourceType = Literal["meeting", "issue", "adr", "pr", "audio_transcript"]
TranscriptionStatus = Literal["uploaded", "transcribing", "ready", "failed"]
# seed_document: 기존 문서에서 가져온 결정 / review_approved: Review Queue에서 사람이 승인한 결정
DecisionOrigin = Literal["seed_document", "review_approved"]
CandidateStatus = Literal["pending", "approved", "ignored"]


# ---- Project ----


class Project(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    created_at: str
    metric_source: str | None  # config.METRIC_SOURCES 키. None = metric 미연결
    agent_source: str | None  # config.AGENT_SOURCES 키. None = Knowledge/Agent 미연결
    metrics_connected: bool
    agent_connected: bool


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


# ---- Metric / Trigger ----


class Metric(BaseModel):
    metric_name: str
    value: int | float
    unit: str
    # check_metric resolver가 알려 주는 출처/관측 시각 (구버전 check_metric이면 없음)
    source: str | None = None
    observed_at: str | None = None


# ---- Review Trigger (V2) ----
# 모든 타입은 review_trigger_json(TEXT)에 JSON으로 저장된다 → DB 스키마 변경 없음.
# raw_condition: 원문 조건 문장. 구조화가 잘못됐을 때 사람이 원문과 비교하기 위해 보존한다.
# 기존(legacy) 데이터 호환: metric 트리거의 `metric`, 모든 트리거의 `description` 필드를 계속 채운다
# (기존 REVIEW_DUE 판정 코드와 프론트가 이 필드를 읽는다).


class MetricTrigger(BaseModel):
    """수치 지표 조건. 예: 가입자 수 > 1000

    metric_key는 시스템에 등록된 지표(config.KNOWN_METRICS)일 때만 채운다.
    모르는 지표는 기존 지표로 바꿔 넣지 않고 metric_key=None, needs_mapping=True로 둔다 (metric 환각 방지).
    legacy 형태 {"type": "metric", "metric": "user_count", ..., "description": "..."}도 그대로 읽는다.
    """

    type: Literal["metric"] = "metric"
    raw_metric: str | None = None  # 원문의 지표 표현 (예: "가입자 수", "일주일 프로덕션 배포 횟수")
    metric_key: str | None = None  # 등록된 지표 키. 모르면 None
    operator: Operator
    threshold: int | float
    unit: str | None = None
    needs_mapping: bool = False  # True = 등록된 지표에 아직 연결되지 않음 → 자동 판정 불가
    raw_condition: str | None = None
    # legacy 호환 필드
    metric: str | None = None  # = metric_key (기존 판정 코드/프론트용)
    description: str | None = None  # = raw_condition
    note: str | None = None  # 자동 판정의 한계 (예: 지속 기간은 확인 불가)

    @model_validator(mode="after")
    def _normalize(self):
        key = self.metric_key or self.metric
        if key and key not in KNOWN_METRICS:
            # 모르는 지표 키는 버리지 않고 원문 지표 표현으로 남긴 뒤 매핑 필요로 표시한다
            self.raw_metric = self.raw_metric or key
            key = None
        self.metric_key = key
        self.metric = key
        self.needs_mapping = key is None
        self.raw_condition = self.raw_condition or self.description
        self.description = self.description or self.raw_condition
        return self


class EventTrigger(BaseModel):
    """비정량 사건 조건. 예: 첫 유료 기업 고객이 SSO를 계약 조건으로 요구함 (조회할 source가 없으면 key를 만들지 않는다)"""

    type: Literal["event"] = "event"
    description: str
    raw_condition: str | None = None


class DurationTrigger(BaseModel):
    """지속 기간 조건. 예: 3일 연속, 7일 동안. 보통 compound 안에서 metric과 함께 쓴다."""

    type: Literal["duration"] = "duration"
    value: int | float
    unit: Literal["minute", "hour", "day", "week", "month"]
    mode: Literal["consecutive", "within"] = "consecutive"  # consecutive: 연속 / within: 기간 동안
    raw_condition: str | None = None
    description: str | None = None


class DateTrigger(BaseModel):
    """날짜 조건. 예: 2027-01-01 이후"""

    type: Literal["date"] = "date"
    operator: Operator
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD
    raw_condition: str | None = None
    description: str | None = None


class ManualTrigger(BaseModel):
    """자동 판정하기 어려운 자연어 조건 — 사람이 확인"""

    type: Literal["manual"] = "manual"
    description: str
    raw_condition: str | None = None


class CompoundTrigger(BaseModel):
    """여러 조건을 AND / OR로 묶는다 (중첩 가능)"""

    type: Literal["compound"] = "compound"
    logic: Literal["AND", "OR"]
    conditions: list["ReviewTrigger"] = Field(min_length=1)
    raw_condition: str | None = None
    description: str | None = None  # = raw_condition (legacy 화면 호환)

    @model_validator(mode="after")
    def _sync_description(self):
        self.raw_condition = self.raw_condition or self.description
        self.description = self.description or self.raw_condition
        return self


ReviewTrigger = Annotated[
    MetricTrigger | EventTrigger | DurationTrigger | DateTrigger | ManualTrigger | CompoundTrigger,
    Field(discriminator="type"),
]
CompoundTrigger.model_rebuild()


class Note(BaseModel):
    label: str  # 원문 항목명 (비고, 영향, 후보 등)
    text: str


class Evidence(BaseModel):
    source_id: str
    quote: str


class Revision(BaseModel):
    date: str | None
    actor: str
    action: str
    note: str | None = None


# ---- Source ----


class SourceAudio(BaseModel):
    """오디오 Source의 원본/전사 정보. 원본 오디오는 로컬 파일로만 두고 경로는 API로 내보내지 않는다."""

    original_filename: str
    content_type: str | None
    size_bytes: int
    transcription_status: TranscriptionStatus
    transcription_engine: str | None
    transcription_model: str | None
    language: str | None
    duration_seconds: float | None
    transcription_seconds: float | None
    rtf: float | None  # transcription_seconds / duration_seconds (1보다 작으면 실시간보다 빠름)
    segments: int | None
    # 실패 종류: stt_unavailable(whisper/model 없음) / decode_failed(오디오 해석 실패) / stt_failed / interrupted(전사 중 재시작)
    error_kind: str | None
    error: str | None
    uploaded_at: str
    transcribed_at: str | None
    # 제목 출처: filename(업로드 파일명) | auto_topics(추출된 topic으로 자동 생성) | user(사람이 입력 → 자동 변경 안 함)
    title_origin: Literal["filename", "auto_topics", "user"] = "filename"


class Source(BaseModel):
    id: str
    project_id: int
    title: str
    type: SourceType
    date: str | None  # issue/ADR 문서에는 날짜가 없다
    summary: str
    content: str
    human_authored: bool  # 사람이 작성한 원문인지 (Review Queue 승인 여부와는 별개)
    audio: SourceAudio | None = None  # 오디오에서 전사한 Source일 때만


class SourceTitleUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SourceCreate(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=200)
    type: SourceType = "meeting"
    content: str = Field(min_length=1)
    date: str | None = None


# ---- Decision ----


class TriggerEvaluation(BaseModel):
    metric: Metric | None
    met: bool | None  # None = 판정 불가
    ratio: float | None
    error: str | None = None
    # unavailable: metric 서버 꺼짐 / not_connected: 프로젝트에 metric source 없음 / not_found: 없는 지표
    reason: Literal["unavailable", "not_connected", "not_found"] | None = None


class Decision(BaseModel):
    id: str
    project_id: int
    topic: str
    title: str
    decision: str
    rationale: str
    assumptions: list[str]
    notes: list[Note] = []
    review_trigger: ReviewTrigger | None
    status: DecisionLifecycle
    origin: DecisionOrigin
    human_verified: bool  # Decision Candidate를 사람이 검토하고 승인했는지
    evidence: list[Evidence]
    superseded_by: str | None = None
    created_at: str | None
    updated_at: str | None
    history: list[Revision]


class DecisionView(Decision):
    """저장된 결정 + 현재 metric으로 계산한 상태 (계산값은 DB에 저장하지 않는다)"""

    display_status: DisplayStatus
    evaluation: TriggerEvaluation | None


class MetricsResponse(BaseModel):
    project_id: int
    connected: bool  # 이 프로젝트에 metric source가 연결되어 있는지
    metric_tool_url: str | None
    metrics: list[Metric]
    errors: dict[str, str]


# ---- Review Queue ----


class Candidate(BaseModel):
    id: str
    project_id: int
    source_id: str
    source_title: str
    topic: str
    title: str
    decision: str
    rationale: str
    assumptions: list[str]
    review_trigger: ReviewTrigger | None
    evidence: str
    evidence_in_source: bool  # evidence 문장이 원문에 그대로 존재하는지
    status: CandidateStatus
    edited: bool
    decision_id: str | None  # 승인 후 저장된 Decision
    extractor: str
    created_at: str
    resolved_at: str | None


class CandidateUpdate(BaseModel):
    topic: str | None = None
    title: str | None = None
    decision: str | None = None
    rationale: str | None = None
    assumptions: list[str] | None = None
    review_trigger: ReviewTrigger | None = None
    clear_review_trigger: bool = False


class ExtractRequest(BaseModel):
    """이미 등록된 source_id 또는 새 원문(title + content) 중 하나"""

    project_id: int
    source_id: str | None = None
    title: str | None = None
    type: SourceType = "meeting"
    content: str | None = None
    # None이면 서버 기본값(EXTRACTION_MODE). "local"이면 외부 모델을 쓰지 않는다
    mode: Literal["local", "auto"] | None = None


class ExtractResponse(BaseModel):
    source: Source
    candidates: list[Candidate]
    # 후보로 저장하지 않은 항목 안내 (예: 재검토 조건만 있고 현재 결정이 명시되지 않음). DB에는 저장하지 않는다.
    warnings: list[str] = []
    # 어떤 모델이 결과를 만들었는지 (mode, final_provider, escalated, escalation_reasons, 호출 수/usage). DB에는 저장하지 않는다
    extraction: dict | None = None


class KnowledgeSyncStatus(BaseModel):
    """승인 후 Dify Knowledge 동기화 결과. 실패해도 승인(Decision 저장)은 유지된다."""

    # synced: 문서 생성 / already_exists: 같은 이름 문서가 이미 있어 생성 안 함
    # failed: 동기화 실패 / not_connected: Knowledge가 연결되지 않은 프로젝트 (호출 안 함)
    status: Literal["synced", "already_exists", "failed", "not_connected"]
    message: str
    document_name: str | None = None
    document_id: str | None = None
    batch: str | None = None


class CandidateDeleteRequest(BaseModel):
    project_id: int
    ids: list[str] = Field(min_length=1, max_length=500)


class CandidateDeleteResponse(BaseModel):
    deleted: list[str]
    skipped: list[dict]  # [{id, reason: not_found | other_project | status_pending | status_approved}]


class GbrainSyncStatus(BaseModel):
    """승인 직후 gbrain 동기화 예약 상태. 실제 import는 응답 이후 background로 진행된다 (실패해도 승인은 유지)"""

    status: Literal["pending", "disabled", "skipped"]
    message: str


class ApproveResponse(BaseModel):
    candidate: Candidate
    decision: Decision
    knowledge_sync: KnowledgeSyncStatus
    gbrain_sync: GbrainSyncStatus | None = None  # 추가 필드 (기존 응답 형식과 호환)


# ---- Ask Agent (Dify Chatflow proxy) ----


class AgentChatRequest(BaseModel):
    project_id: int
    query: str
    conversation_id: str | None = None


class RetrievedChunk(BaseModel):
    source_id: str  # 문서명에서 확장자를 뺀 값 (sources.id와 동일 규칙)
    title: str  # Dify Knowledge 문서명
    score: float
    snippet: str


class ToolCall(BaseModel):
    name: str
    arguments: dict
    result: dict | str
    status: Literal["success", "error"]
    round: int


class AgentChatResponse(BaseModel):
    answer: str  # Agent 마지막 라운드 출력 (중간 Thought 제외)
    raw_answer: str  # Dify Answer 노드 원문 (중간 Thought 포함될 수 있음)
    # backend가 답변을 표준 응답으로 대체한 이유 (no_evidence: 근거 0건 / non_korean: 재질문 후에도 중국어 답변)
    guard: Literal["no_evidence", "non_korean"] | None = None
    retrieval: list[RetrievedChunk]
    tool_calls: list[ToolCall]
    conversation_id: str | None
    message_id: str | None
    latency_ms: int


# ---- Review monitor ----

ReviewStatus = Literal["ACTIVE", "REVIEW_DUE", "UNKNOWN"]


class ReviewEvent(BaseModel):
    """scheduler가 감지한 결정의 재검토 상태 변화 (ACTIVE → REVIEW_DUE 등). Decision 자체는 바꾸지 않는다."""

    id: int
    project_id: int
    decision_id: str
    decision_title: str | None  # 결정이 정리된 경우 None
    previous_status: ReviewStatus
    new_status: ReviewStatus
    trigger: dict | None  # 감지 시점의 review_trigger
    observed: dict | None  # {metric_key: {value, unit, source, observed_at}}
    detected_at: str
    acknowledged: bool
    acknowledged_at: str | None
    resolved_at: str | None  # 이후 조건이 다시 풀린 시각


# ---- Review Memo (REVIEW_DUE 결정에 대한 AI 검토 메모. advisory 산출물 — Decision/review 상태를 바꾸지 않는다) ----

ReviewMemoStatus = Literal["generating", "completed", "failed"]


class ReviewMemo(BaseModel):
    id: int
    project_id: int
    decision_id: str
    review_event_id: int | None  # 생성 시점에 열려 있던 REVIEW_DUE 이벤트 (baseline due면 None)
    review_state_changed_at: str | None
    detected_by: Literal["review_event", "review_state_baseline"]
    status: ReviewMemoStatus
    content: str | None  # Markdown (completed일 때만)
    prompt_version: str
    observed_snapshot: dict | None  # 생성 시점의 trigger / 관측값 (이후 metric이 바뀌어도 그대로)
    source_snapshot: dict | None  # 생성 시점의 sources / evidence
    model: str | None
    provider: str | None
    agent_session_id: str | None
    tool_audit: dict | None  # 실제 호출된 tool 목록 + 허용 여부
    error: str | None
    created_at: str
    generated_at: str | None


class ReviewMemoRequest(BaseModel):
    # false: 같은 review 상태에 대해 완료된 memo가 있으면 새로 만들지 않고 그것을 돌려준다
    regenerate: bool = False


# ---------------------------------------------------------------- Demo Control Center (발표/개발 전용)

DemoSimulator = Literal["users_table", "metrics_store"]


class DemoDecisionImpact(BaseModel):
    """이 metric을 쓰는 단일 metric 조건 결정 (Review Monitor 자동 판정 대상)"""
    decision_id: str
    title: str
    project_id: int
    project_name: str
    trigger: str
    operator: Operator
    threshold: float
    status: DisplayStatus  # 현재값 기준 판정 (/api/decisions와 같은 코드)
    monitor_status: str | None = None  # Review Monitor가 마지막으로 기록한 판정 (ACTIVE | REVIEW_DUE | UNKNOWN)
    monitor_checked_at: str | None = None


class DemoPreset(BaseModel):
    value: float
    label: str  # 예: "> 1000 · below"
    review_due: int  # 이 값이면 REVIEW_DUE가 되는 결정 수 (backend evaluate_trigger로 계산)


class DemoMetricState(BaseModel):
    name: str
    value: float | None = None
    unit: str | None = None
    source: str | None = None
    observed_at: str | None = None
    error: str | None = None
    simulator: DemoSimulator | None = None  # None = 조작 불가 (resolver source가 시뮬레이터가 아님)
    integer: bool = False
    baseline: float | None = None
    presets: list[DemoPreset] = []
    decisions: list[DemoDecisionImpact] = []
    compound_decisions: list[str] = []  # 이 metric을 포함하지만 자동 판정되지 않는 compound 조건 결정 id


class DemoConditionPart(BaseModel):
    type: str
    expression: str
    metric: str | None = None
    simulator_backed: bool = False


class DemoCondition(BaseModel):
    """시뮬레이터로 조작할 수 없는 재검토 조건 (read-only 표시)"""
    decision_id: str
    title: str
    project_id: int
    project_name: str
    trigger_type: str
    expression: str
    status: DisplayStatus
    logic: str | None = None  # compound일 때 AND | OR
    parts: list[DemoConditionPart] = []
    note: str


class DemoScenario(BaseModel):
    id: str
    title: str
    metric: str
    baseline: float | None
    value: float
    unit: str | None = None
    review_due: int  # 이 값이면 REVIEW_DUE가 되는 결정 수


class DemoProjectRef(BaseModel):
    id: int
    name: str
    simulated: bool  # 이 프로젝트가 시뮬레이터 metric source에 연결되어 있는지


class DemoState(BaseModel):
    metric_source: str
    metric_tool_url: str | None
    monitor: dict
    projects: list[DemoProjectRef]
    metrics: list[DemoMetricState]
    conditions: list[DemoCondition]
    scenarios: list[DemoScenario]


class DemoPreviewItem(BaseModel):
    decision_id: str
    title: str
    trigger: str
    current: DisplayStatus
    predicted: DisplayStatus


class DemoPreview(BaseModel):
    metric: str
    value: float
    unit: str | None = None
    affected: int  # 상태가 바뀌는 결정 수
    review_due: int  # 이 값에서 REVIEW_DUE인 결정 수
    decisions: list[DemoPreviewItem]


class DemoUpdateRequest(BaseModel):
    # 문자열 "1400" 등은 받지 않는다 (숫자만)
    value: StrictInt | StrictFloat


class DemoChange(BaseModel):
    metric: str
    before: float | None
    after: float
    unit: str | None = None
    source: str | None = None
    observed_at: str | None = None


# ---------------------------------------------------------------- Deep Review (Paperclip multi-agent, advisory)
# 역할 3개의 handoff JSON contract. LLM 출력은 이 모델 검증을 통과해야만 저장/다음 단계로 넘어간다.
# reference 형식 (실제로 식별 가능한 값만):
#   E<n>                       Decision evidence 줄 (Review Memo와 같은 evidence_items snapshot ID)
#   FACT:<key>                 backend가 저장값 그대로 만든 Authoritative Facts 항목 (예: FACT:assumptions[0], FACT:observed)
#   DEC-<...>                  관련 과거 Decision ID (Decision Pulse DB에 실제로 있어야 함)

DEEP_REVIEW_REF = r"^(E[1-9][0-9]*|FACT:[a-z_]+(\[[0-9]+\])?|DEC-[A-Z0-9][A-Z0-9-]*)$"
DEEP_REVIEW_DECISION_ID = r"^DEC-[A-Z0-9][A-Z0-9-]*$"
DeepReviewRef = Annotated[str, Field(pattern=DEEP_REVIEW_REF)]
DeepReviewText = Annotated[str, Field(min_length=1, max_length=1200)]
DeepReviewRole = Literal["researcher", "analyst", "critic"]
DeepReviewStatus = Literal[
    "PENDING", "RESEARCHING", "ANALYZING", "CRITIQUING", "REVISING", "COMPLETED", "HUMAN_REVIEW_REQUIRED", "FAILED"
]
DEEP_REVIEW_ACTIVE = ("PENDING", "RESEARCHING", "ANALYZING", "CRITIQUING", "REVISING")
DeepReviewVerdict = Literal["PASS", "REVISE"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ResearchEvidence(_Strict):
    ref: Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]
    source_id: str
    source_title: str | None = None
    text: str  # backend snapshot 원문 그대로 (LLM이 쓴 문장이 아님)
    relevance: DeepReviewText


class RelatedDecision(_Strict):
    decision_id: Annotated[str, Field(pattern=DEEP_REVIEW_DECISION_ID)]
    ref: Annotated[str, Field(min_length=1, max_length=300)]  # gbrain slug / page 등 조회한 위치
    relation: Literal["direct", "indirect"]
    summary: DeepReviewText


class ResearchFindingEvidence(_Strict):
    ref: Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]
    relevance: DeepReviewText


class ResearchFindings(_Strict):
    """Researcher(Hermes)가 직접 쓰는 부분: evidence 선택 + 관련 과거 Decision + unknowns.
    worker가 backend snapshot(authoritative_facts / observed_state / evidence 원문)과 합쳐 DeepReviewResearch를 만든다."""
    decision_id: Annotated[str, Field(pattern=DEEP_REVIEW_DECISION_ID)]
    confirmed_review_due: bool
    evidence: list[ResearchFindingEvidence]
    related_decisions: list[RelatedDecision]
    unknowns: list[DeepReviewText]


class DeepReviewResearch(_Strict):
    """Evidence Researcher: 사실 수집만 (해석/판단 없음). authoritative_facts / observed_state는 backend snapshot."""
    decision_id: Annotated[str, Field(pattern=DEEP_REVIEW_DECISION_ID)]
    authoritative_facts: dict
    observed_state: dict
    evidence: list[ResearchEvidence]
    related_decisions: list[RelatedDecision]
    unknowns: list[DeepReviewText]


class AnalysisClaim(_Strict):
    statement: DeepReviewText
    evidence_refs: Annotated[list[DeepReviewRef], Field(min_length=1)]


class AnalysisImplication(AnalysisClaim):
    confidence: Literal["low", "medium", "high"]


class RecommendedCheck(_Strict):
    check: DeepReviewText
    reason: DeepReviewText


class DeepReviewAnalysis(_Strict):
    """Decision Analyst: Researcher JSON만 입력. known_changes(관측된 사실)와 implications(추론)를 분리한다."""
    decision_id: Annotated[str, Field(pattern=DEEP_REVIEW_DECISION_ID)]
    known_changes: list[AnalysisClaim]
    implications: list[AnalysisImplication]
    unknowns: list[DeepReviewText]
    recommended_checks: list[RecommendedCheck]
    evidence_refs: list[DeepReviewRef]


class CritiqueFinding(_Strict):
    claim: DeepReviewText
    reason: DeepReviewText


class CritiqueContradiction(CritiqueFinding):
    evidence_ref: DeepReviewRef


class DeepReviewCritique(_Strict):
    """Review Critic: 교차 검증만 (새 분석/새 evidence 없음). verdict는 PASS | REVISE 두 값만."""
    decision_id: Annotated[str, Field(pattern=DEEP_REVIEW_DECISION_ID)]
    verdict: DeepReviewVerdict
    unsupported_claims: list[CritiqueFinding]
    contradictions: list[CritiqueContradiction]
    missing_checks: list[DeepReviewText]
    revision_instructions: list[DeepReviewText]

    @model_validator(mode="after")
    def _consistent(self):
        if self.verdict == "REVISE" and not self.revision_instructions:
            raise ValueError("verdict=REVISE이면 revision_instructions가 1개 이상 필요")
        if self.verdict == "PASS" and (self.unsupported_claims or self.contradictions):
            raise ValueError("unsupported_claims / contradictions가 있으면 verdict=PASS일 수 없음")
        return self


DEEP_REVIEW_OUTPUT_MODELS = {"researcher": DeepReviewResearch, "analyst": DeepReviewAnalysis, "critic": DeepReviewCritique}


class DeepReviewArtifact(BaseModel):
    id: int
    run_id: int
    role: DeepReviewRole
    iteration: int  # analyst / critic: 0 = 첫 실행, 1 = Critic REVISE 후 한 번 더
    payload: dict  # 검증을 통과한 JSON (위 모델)
    paperclip_issue_id: str | None
    paperclip_issue_identifier: str | None
    paperclip_run_id: str | None
    meta: dict | None  # model / usage / cost / tool audit / elapsed
    created_at: str


class DeepReviewStage(BaseModel):
    key: Literal["research", "analysis", "critique"]
    label: str
    state: Literal["waiting", "running", "complete", "failed"]
    iteration: int


class DeepReviewRun(BaseModel):
    id: int
    project_id: int
    decision_id: str
    review_event_id: int | None
    review_state_changed_at: str | None
    status: DeepReviewStatus
    revision_count: int
    final_verdict: Literal["PASS", "HUMAN_REVIEW_REQUIRED"] | None
    error: str | None
    paperclip: dict  # company / project / goal / parent issue / 현재 issue 식별자
    paperclip_url: str | None  # 개발용 링크 (PAPERCLIP_UI_URL이 설정된 경우만)
    input_snapshot: dict | None  # 시작 시점 Authoritative Facts (Decision은 바뀌지 않는다)
    stages: list[DeepReviewStage]
    artifacts: list[DeepReviewArtifact]
    created_at: str
    started_at: str | None
    completed_at: str | None
