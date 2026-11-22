// Decision Debt 데이터 모델 (handoff §3, §21 기준)
// 저장되는 lifecycle 상태(DecisionLifecycle)와 metric으로 계산되는 표시 상태(DisplayStatus)를
// 의도적으로 분리한다. (handoff §29: decision status와 metric state를 섞지 않는다)

export type MetricName = "user_count" | "search_api_p95_ms" | "server_count";

export type Operator = ">" | ">=" | "<" | "<=" | "==" | "!=";

// ---- Review Trigger V2 (backend schemas.py와 동일) ----
// legacy 데이터({type:"metric", metric, operator, threshold, description})도 backend가 같은 형태로 내려준다.

/** 수치 지표 조건. metric(=metric_key)이 null이면 등록된 지표에 아직 연결되지 않은 것 (자동 판정 불가) */
export interface MetricTrigger {
  type: "metric";
  /** 등록된 지표 키. 모르는 지표면 null — 화면에서 다른 지표로 바꾸거나 "null"로 표시하지 않는다 */
  metric: MetricName | null;
  metric_key?: MetricName | null;
  /** 원문의 지표 표현 (예: "일주일 프로덕션 배포 횟수") */
  raw_metric?: string | null;
  operator: Operator;
  threshold: number;
  unit?: string | null;
  /** true = 지표 매핑 필요 */
  needs_mapping?: boolean;
  raw_condition?: string | null;
  /** 원문 조건 문장 */
  description?: string | null;
  /** 자동 판정의 한계 (예: 지속 기간은 확인 불가) */
  note?: string | null;
}

/** 비정량 사건 조건 */
export interface EventTrigger {
  type: "event";
  description: string;
  raw_condition?: string | null;
}

/** 지속 기간 조건 (보통 compound 안에서 metric과 함께) */
export interface DurationTrigger {
  type: "duration";
  value: number;
  unit: "minute" | "hour" | "day" | "week" | "month";
  mode: "consecutive" | "within";
  raw_condition?: string | null;
  description?: string | null;
}

/** 날짜 조건 */
export interface DateTrigger {
  type: "date";
  operator: Operator;
  date: string; // YYYY-MM-DD
  raw_condition?: string | null;
  description?: string | null;
}

/** check_metric에 없는 지표/사건 기반 조건 — 사람이 확인 */
export interface ManualTrigger {
  type: "manual";
  description: string;
  raw_condition?: string | null;
}

/** 여러 조건을 AND / OR로 묶음 (중첩 가능) */
export interface CompoundTrigger {
  type: "compound";
  logic: "AND" | "OR";
  conditions: ReviewTrigger[];
  raw_condition?: string | null;
  description?: string | null;
}

export type ReviewTrigger = MetricTrigger | EventTrigger | DurationTrigger | DateTrigger | ManualTrigger | CompoundTrigger;

export interface Note {
  label: string;
  text: string;
}

export interface Project {
  id: number;
  name: string;
  slug: string;
  description: string;
  created_at: string;
  metric_source: string | null;
  agent_source: string | null;
  /** 이 프로젝트에 metric source가 연결되어 있는지 (현재는 기본 프로젝트만 check_metric 연결) */
  metrics_connected: boolean;
  /** 이 프로젝트에 Knowledge/Agent가 연결되어 있는지 (현재는 기본 프로젝트만 Dify 연결) */
  agent_connected: boolean;
}

/** DB에 저장되는 결정의 생명주기 상태 */
export type DecisionLifecycle = "active" | "pending_review" | "superseded";

/** 화면에 표시되는 상태: backend가 lifecycle + 현재 metric 비교 결과로 계산.
 *  UNKNOWN = 재검토 조건은 있지만 check_metric 조회 실패로 판정 불가 */
export type DisplayStatus = "ACTIVE" | "REVIEW_DUE" | "PENDING_REVIEW" | "SUPERSEDED" | "UNKNOWN";

export type SourceType = "meeting" | "issue" | "adr" | "pr" | "audio_transcript";

export type TranscriptionStatus = "uploaded" | "transcribing" | "ready" | "failed";

/** 오디오 Source의 원본/전사 정보 (로컬 STT). 원본 오디오 경로는 API로 내려오지 않는다 */
export interface SourceAudio {
  original_filename: string;
  content_type: string | null;
  size_bytes: number;
  transcription_status: TranscriptionStatus;
  transcription_engine: string | null;
  transcription_model: string | null;
  language: string | null;
  duration_seconds: number | null;
  transcription_seconds: number | null;
  /** 전사 시간 / 오디오 길이 (1보다 작으면 실시간보다 빠름) */
  rtf: number | null;
  segments: number | null;
  /** stt_unavailable | decode_failed | stt_failed | interrupted */
  error_kind: string | null;
  error: string | null;
  uploaded_at: string;
  transcribed_at: string | null;
  /** 제목 출처: filename(업로드 파일명) | auto_topics(추출된 topic으로 자동 생성) | user(사람이 입력 → 자동 변경 안 함) */
  title_origin?: "filename" | "auto_topics" | "user";
}

export interface Source {
  id: string;
  project_id: number;
  title: string; // 파일명 (Dify Knowledge 문서명과 동일하게 유지)
  type: SourceType;
  date: string | null; // YYYY-MM-DD, issue/ADR 문서에는 없음
  summary: string;
  content: string;
  /** 사람이 작성한 원문인지 (Review Queue 승인 여부와는 별개) */
  human_authored: boolean;
  /** 오디오에서 전사한 Source일 때만 */
  audio?: SourceAudio | null;
}

export interface Evidence {
  source_id: string;
  quote: string;
}

export interface Revision {
  date: string | null;
  actor: string;
  action: string;
  note?: string | null;
}

/** seed_document: 기존 문서에서 가져온 결정 / review_approved: Review Queue에서 사람이 승인한 결정 */
export type DecisionOrigin = "seed_document" | "review_approved";

export interface Decision {
  id: string; // e.g. DEC-2026-0314-DB
  project_id: number;
  topic: string; // e.g. database
  title: string; // 짧은 결정명 e.g. SQLite
  decision: string;
  rationale: string;
  assumptions: string[];
  notes: Note[];
  review_trigger: ReviewTrigger | null;
  status: DecisionLifecycle;
  origin: DecisionOrigin;
  /** Decision Candidate를 사람이 검토하고 승인했는지 */
  human_verified: boolean;
  evidence: Evidence[];
  superseded_by?: string | null;
  created_at: string | null;
  updated_at: string | null;
  history: Revision[];
}

/** check_metric 응답 형태 (~/decision-debt-tool GET /metrics/{metric_name}) */
export interface Metric {
  metric_name: MetricName;
  value: number;
  unit: string;
  /** check_metric resolver가 읽은 출처 (예: sqlite:users) */
  source?: string | null;
  observed_at?: string | null;
}

export interface TriggerEvaluation {
  metric: Metric | null;
  /** null = 판정 불가 (metric 조회 실패) */
  met: boolean | null;
  /** 현재값 / 임계값. 게이지 표시용 */
  ratio: number | null;
  error?: string | null;
  /** unavailable: metric 서버 꺼짐 / not_connected: 프로젝트에 metric 미연결 / not_found: 없는 지표 */
  reason?: "unavailable" | "not_connected" | "not_found" | null;
}

/** GET /api/decisions 응답: 저장된 결정 + 계산된 상태 */
export interface DecisionView extends Decision {
  display_status: DisplayStatus;
  evaluation: TriggerEvaluation | null;
}

/** GET /api/metrics 응답 */
export interface MetricsResponse {
  project_id: number;
  connected: boolean;
  metric_tool_url: string | null;
  metrics: Metric[];
  errors: Record<string, string>;
}

/** Review Queue 후보 (backend candidates 테이블). 자동 확정 금지: Approve해야 Decision이 된다 */
export interface Candidate {
  id: string;
  project_id: number;
  source_id: string;
  source_title: string;
  topic: string;
  title: string;
  decision: string;
  rationale: string;
  assumptions: string[];
  review_trigger: ReviewTrigger | null;
  evidence: string;
  /** evidence 문장이 원문에 그대로 존재하는지 */
  evidence_in_source: boolean;
  status: "pending" | "approved" | "ignored";
  edited: boolean;
  /** 승인 후 저장된 Decision id (저장 성공 시에만 존재) */
  decision_id: string | null;
  extractor: string;
  created_at: string;
  resolved_at: string | null;
}

/** 승인 후 Dify Knowledge 동기화 결과 (POST /api/candidates/{id}/approve 응답). 실패해도 승인은 유지된다 */
export interface KnowledgeSyncStatus {
  status: "synced" | "already_exists" | "failed" | "not_connected";
  message: string;
  document_name: string | null;
  document_id: string | null;
  batch: string | null;
}

// ---- Ask Agent (POST /api/agent/chat → backend → Dify Chatflow) ----

export interface RetrievedChunk {
  source_id: string;
  title: string;
  score: number;
  snippet: string;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | string;
  status: "success" | "error";
  round: number;
}

export interface AgentResponse {
  /** Agent 마지막 라운드 출력 (중간 Thought 제외) */
  answer: string;
  /** Dify Answer 노드 원문 */
  raw_answer: string;
  /** backend가 표준 응답으로 대체한 이유. no_evidence: 근거 0건 / non_korean: 재질문 후에도 중국어 답변 */
  guard: "no_evidence" | "non_korean" | null;
  retrieval: RetrievedChunk[];
  tool_calls: ToolCall[];
  conversation_id: string | null;
  message_id: string | null;
  latency_ms: number;
}

// ---- Review monitor (backend scheduler가 감지한 재검토 상태 변화) ----

export type ReviewStatus = "ACTIVE" | "REVIEW_DUE" | "UNKNOWN";

export interface ObservedMetric {
  value: number;
  unit: string;
  source?: string | null;
  observed_at?: string | null;
}

export interface ReviewEvent {
  id: number;
  project_id: number;
  decision_id: string;
  decision_title: string | null;
  previous_status: ReviewStatus;
  new_status: ReviewStatus;
  /** 감지 시점의 review_trigger */
  trigger: ReviewTrigger | null;
  observed: Record<string, ObservedMetric> | null;
  detected_at: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  /** 이후 조건이 다시 풀린 시각 */
  resolved_at: string | null;
}

// ---- Review Memo (REVIEW_DUE 결정에 대한 Hermes 검토 메모. advisory — Decision/review 상태를 바꾸지 않는다) ----

export type ReviewMemoStatus = "generating" | "completed" | "failed";

export interface ReviewMemoToolAudit {
  session_id: string | null;
  tools_used: string[];
  counts: Record<string, number>;
  disallowed: string[];
  missing_required: string[];
  ok: boolean;
  elapsed_seconds?: number;
  toolsets?: string[];
  error?: string;
}

export interface ReviewMemo {
  id: number;
  project_id: number;
  decision_id: string;
  review_event_id: number | null;
  review_state_changed_at: string | null;
  detected_by: "review_event" | "review_state_baseline";
  status: ReviewMemoStatus;
  /** Markdown (completed일 때만) */
  content: string | null;
  prompt_version: string;
  /** 생성 시점의 trigger / 관측값 (이후 metric이 바뀌어도 그대로) */
  observed_snapshot: {
    trigger_text?: string;
    observed?: { metric: string; value: number; unit: string | null; source?: string | null; observed_at?: string | null } | null;
  } | null;
  source_snapshot: Record<string, unknown> | null;
  model: string | null;
  provider: string | null;
  agent_session_id: string | null;
  tool_audit: ReviewMemoToolAudit | null;
  error: string | null;
  created_at: string;
  generated_at: string | null;
}

// ---- Demo Control Center (발표/개발 전용. backend DEMO_MODE_ENABLED=true일 때만) ----

export type DemoSimulator = "users_table" | "metrics_store";

export interface DemoDecisionImpact {
  decision_id: string;
  title: string;
  project_id: number;
  project_name: string;
  trigger: string;
  operator: Operator;
  threshold: number;
  /** 현재값 기준 판정 (/api/decisions와 같은 코드) */
  status: DisplayStatus;
  /** Review Monitor가 마지막으로 기록한 판정. status와 다르면 아직 monitor가 확인하지 않은 것 */
  monitor_status: ReviewStatus | null;
  monitor_checked_at: string | null;
}

export interface DemoPreset {
  value: number;
  label: string;
  review_due: number;
}

export interface DemoMetricState {
  name: string;
  value: number | null;
  unit: string | null;
  source: string | null;
  observed_at: string | null;
  error: string | null;
  /** null = 조작 불가 (resolver source가 시뮬레이터가 아님) */
  simulator: DemoSimulator | null;
  integer: boolean;
  baseline: number | null;
  presets: DemoPreset[];
  decisions: DemoDecisionImpact[];
  /** 이 metric을 포함하지만 자동 판정되지 않는 compound 조건 결정 */
  compound_decisions: string[];
}

export interface DemoCondition {
  decision_id: string;
  title: string;
  project_id: number;
  project_name: string;
  trigger_type: string;
  expression: string;
  status: DisplayStatus;
  /** compound일 때 AND | OR */
  logic: "AND" | "OR" | null;
  parts: { type: string; expression: string; metric: string | null; simulator_backed: boolean }[];
  note: string;
}

export interface DemoScenario {
  id: string;
  title: string;
  metric: string;
  baseline: number | null;
  value: number;
  unit: string | null;
  review_due: number;
}

export interface DemoState {
  metric_source: string;
  metric_tool_url: string | null;
  monitor: { enabled: boolean; interval_seconds: number; runs: number; last_run_at: string | null; last_error: string | null };
  projects: { id: number; name: string; simulated: boolean }[];
  metrics: DemoMetricState[];
  conditions: DemoCondition[];
  scenarios: DemoScenario[];
}

export interface DemoPreview {
  metric: string;
  value: number;
  unit: string | null;
  affected: number;
  review_due: number;
  decisions: { decision_id: string; title: string; trigger: string; current: DisplayStatus; predicted: DisplayStatus }[];
}

export interface DemoChange {
  metric: string;
  before: number | null;
  after: number;
  unit: string | null;
  source: string | null;
  observed_at: string | null;
}
