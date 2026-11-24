# Decision Debt Agent — Decision Pulse

과거 기술 결정과 그 **재검토 조건**을 기억하고, 외부 상태(지표)가 조건을 넘으면 "다시 검토할 때"라고 알려주는 에이전트.
웹 UI 이름은 **Decision Pulse**.

- 결정은 사람이 승인한 것만 저장한다 (AI 추출 결과는 항상 Candidate).
- 재검토 판정은 LLM이 아니라 코드(deterministic)로 한다.
- 조건 충족은 결정을 자동으로 바꾸지 않는다. 사람의 재검토가 필요하다는 신호일 뿐이다.

배경과 원래 설계는 [docs/CLAUDE_HANDOFF_DECISION_DEBT.md](docs/CLAUDE_HANDOFF_DECISION_DEBT.md),
구조도는 [docs/architecture/](docs/architecture/) (`01_decision_debt_system_architecture`, `02_decision_debt_lifecycle` — HTML/PNG).

---

## Lifecycle

```
Input (text · .md/.txt · audio)
  │  Sources > Add source
  ▼
Source ── audio면 whisper.cpp 로컬 STT → transcript
  │
  ▼
Extraction (Qwen2.5-7B local, llama-swap) ── 구조화 실패 시에만 OpenRouter로 재추출 (EXTRACTION_MODE=auto)
  │
  ▼
Candidate ── Candidates 화면에서 사람이 Approve / Edit / Ignore
  │ Approve
  ▼
Decision (SQLite = 유일한 원본) ──▶ 파생 사본: Dify Knowledge, gbrain(Markdown + embedding)
  │
  ▼
Review Monitor (30초 주기) ── check_metric으로 현재값 조회 → review trigger 판정
  │ ACTIVE → REVIEW_DUE
  ▼
Review Event ──▶ Web alert (모든 Workspace 화면 상단)
             └─▶ Telegram 알림 (hermes send, 이벤트당 1회)
  │
  ▼
REVIEW_DUE
      ├─ Review Memo   (Normal Review: 사용자 요청 시, Hermes 단일 agent + read-only MCP)
      └─ Deep Review   (고신뢰: 사용자 요청 시, Paperclip → Evidence Researcher → Decision Analyst → Review Critic)
                        ※ Deep Review는 ACTIVE / Needs mapping / Manual review 결정에서도 수동 실행 가능 (review_reason=MANUAL)
            ──▶ 사람이 재검토 (Decision은 자동으로 바뀌지 않는다)
```

---

## 구성

| 구성 요소 | 위치 / 포트 | 역할 |
| --- | --- | --- |
| web | `web/` · :3000 | Next.js 16 (App Router) + React 19 + Tailwind v4. `/api/*`는 backend로 rewrite |
| backend | `backend/` · :8001 | FastAPI + SQLite. 판정, Review Monitor, 추출, 연동 전부 |
| check_metric | `~/decision-debt-tool` · :8000 | 지표 resolver (`GET /metrics/{name}`). Dify Agent와 backend가 같은 서버를 호출 |
| llama-swap | :8080 | 로컬 LLM (Qwen2.5-7B, qwen3-embedding) |
| Dify | :80 | Ask Agent Chatflow + Knowledge |
| Hermes | `~/.hermes` (gateway) | Review Memo 작성, Telegram 발송. MCP: `decision-debt`(read-only), `gbrain`(read-only) |
| gbrain | `~/decision-debt-brain` | 승인된 Decision의 Markdown knowledge layer + semantic search |
| Paperclip | `paperclip/` · 127.0.0.1:3110 | Deep Review orchestration/control plane (조직 3 agent, ticket, checkout, budget). 프로젝트 전용 instance |

> Dify / llama-swap / check_metric 설정은 이 프로젝트에서 수정하지 않는다. `docker compose down -v` 금지.

---

## 실행

```bash
./dev.sh    # Dify → llama-swap → check_metric → Paperclip → backend → web → Hermes gateway, 이어서 health check
./stop.sh   # dev.sh가 시작한 프로세스만 종료
```

- 이미 실행 중인 서비스(포트 LISTEN 중)는 건너뛰고, stop.sh도 종료하지 않는다.
- dev.sh가 시작한 프로세스는 `.run/<name>.pid`(pid, 시작 시각, 명령 패턴)에 기록되고, stop.sh는 세 값이 모두 맞을 때만 프로세스 그룹 단위로 종료한다.
- 로그: `logs/{dify,llama-swap,check-metric,paperclip,backend,web,hermes}.log`
- Paperclip은 `paperclipai run`(foreground)을 dev.sh 프로세스 그룹으로 띄운다 → stop.sh가 embedded PostgreSQL까지 함께 종료. launchd 서비스는 설치하지 않는다. 설치 안 돼 있으면 건너뛰고 Deep Review만 비활성.
- **Next.js 보안 업그레이드 안내**: 새 안내가 나오면 `next dev`가 첫 실행에서 멈춘다(health check에서 web 실패). 같은 명령을 한 번 더 실행하면 이어서 뜬다.

수동 실행:

```bash
# backend (최초 1회: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
cd backend && .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8001

# web (최초 1회: npm install)
cd web && npm run dev          # http://localhost:3000
```

`decision_debt.db`는 최초 실행 시 `seed_data.json`으로 만들어진다. 스키마가 바뀌면 시작할 때 백업(`decision_debt.v<N>-backup-*.db`) 후 자동 migration.

---

## 화면 (web)

| 경로 | 화면 |
| --- | --- |
| `/` | **Project Selector** — 프로젝트 carousel(drag/swipe/키보드/trackpad) + 선택 프로젝트 요약(상태 donut, Review Due 미리보기). Open project → Workspace |
| `/overview` | Workspace 첫 화면: 01 Review due → 02 Candidates → 03 Review memos → 04 Decision status → 05 Knowledge & metrics |
| `/decisions`, `/decisions/[id]` | Decision 목록 / 상세 (evidence, review trigger, revision history, **Review Memo**(single) + **Deep Review**(multi) 패널) |
| `/review` | **Candidates** — Review Queue 전용 (Pending review → Processed). Edit / Ignore / Approve |
| `/sources` | **Sources** — 모든 원문 입력(`+ Add source`) + Calendar + All sources |
| `/agent` | Ask Agent (Dify Chatflow, Evidence / Tool Activity) |
| `/demo` | **Demo Control Center** (발표/개발 전용, `DEMO_MODE_ENABLED=true`일 때만) |

### 프로젝트 선택

현재 프로젝트는 쿠키 `dd_project`에 저장된다. Selector의 Open project, Workspace 상단의 프로젝트 전환 메뉴가 쿠키를 바꾼다.
모든 데이터(decisions / sources / candidates)는 `project_id` 범위로 조회된다.

### Sources

- **Add source** (Sources · Candidates · Overview에서 같은 컴포넌트):
  - Paste text / Upload document(.md, .txt) → `POST /api/extract` 1회. 추출이 성공해야 Source가 저장되고 Candidate가 생긴다.
  - Upload audio(.wav, .mp3, .m4a) → 업로드 즉시 Source 생성 → 로컬 STT → 전사 완료 후 Extract.
- **Calendar**: Candidate(pending/approved) 또는 Decision evidence와 연결된 회의록(meeting / audio)만 표시. 날짜는 회의록 첫머리에 적힌 날짜, 없으면 그 회의록 후보를 처음 Approve한 날짜. 둘 다 없으면 표시하지 않는다.
- **All sources**: 등록된 모든 원문 (최신순). 행마다 `Decisions N / Candidates N` 또는 `Unlinked`.

### Demo Control Center (`/demo`)

발표 중 터미널 없이 **외부 상태값**(check_metric이 읽는 sample DB)을 바꿔 실제 흐름을 시연한다.

- 바꾸는 것은 sample DB 값뿐. REVIEW_DUE / Review Event / Web alert / Telegram은 기존 Review Monitor가 처리한다.
- 값 변경은 tool의 CLI script를 그대로 실행한다 (`set_user_count.py`, `set_metric.py`) → 터미널과 같은 코드.
- trigger 경계 preset, 변경 미리보기(backend 판정 함수), 영향받는 결정, Review Monitor 대기/결과 피드백, Reset(baseline: `user_count 900`, `search_api_p95_ms 500`, `server_count 1`), 시나리오.
- `DEMO_MODE_ENABLED=false`(기본)면 메뉴가 숨겨지고 `/demo`와 `/api/demo/*` 전부 404.

---

## Review Trigger와 판정

```
Decision → Review Trigger → Canonical Metric → (resolver) Data Source Adapter → Observed Value
```

Decision은 특정 DB/API를 알지 않는다. 지표 정의는 **Canonical Metric Registry**(`backend/metric_registry.py`) 한 곳에 있다.

| mapping_status | 의미 | Review Monitor | 화면 |
| --- | --- | --- | --- |
| `CONNECTED` | registry 지표에 resolver(data source adapter)가 있고 프로젝트에 metric source가 연결됨 | 자동 평가 → ACTIVE / REVIEW_DUE (읽기 실패면 UNKNOWN) | ● 자동 모니터링 + 현재값 + 데이터 소스 |
| `NEEDS_MAPPING` | 측정 가능한 수치 조건인데 연결된 data source가 없음 (registry에 resolver 없음 / 미등록 지표 / 프로젝트 미연결) | 평가·이벤트 없음 | ○ 데이터 연결 필요 |
| `MANUAL` | 객관적 자동 측정값으로 표현하기 어려움 (manual / event / date / duration / 주관적 표현 / compound) | 평가·이벤트 없음 | ○ 수동 확인 필요 |

- `UNKNOWN`은 **CONNECTED인데 지금 값을 읽지 못한** 평가 결과다 (`evaluation_status`). 연결 안 된 지표를 UNKNOWN으로 표시하지 않는다.
- 상태는 저장하지 않고 읽을 때 registry로 계산한다 (`services/trigger_status.py`). API: `DecisionView.trigger_status`
  `{mapping_status, evaluation_status, canonical_metric, metric_label, resolver, data_source, window, reason}`. 기존 필드는 그대로.
- 연결된 지표: `user_count`(sqlite:users), `search_api_p95_ms`, `server_count`(metrics table) — 기존 check_metric resolver 재사용.
- 정의만 있는 지표(NEEDS_MAPPING): `deployment_count`, `error_rate`, `monthly_revenue`, `failed_job_count`, `daily_log_volume_gb`.
- alias 정규화: 원문 지표 표현 → canonical 이름. 완전 일치 + 단위 호환만 (LLM이 지표 이름을 만들지 않는다). 집계 기간(`일주일에 5회` → `7d`)은 원문에서 읽는다.
- 추출 시 주관적 표현(불만, 부담, 판단, 전략 …)의 metric은 validator가 `manual`로 내린다. 등록 안 된 수치 지표는 실패가 아니라 NEEDS_MAPPING으로 저장된다.
- **새 data source 붙이는 곳**: `services/metric_adapters.py`에 adapter(`resolve(project, names) -> (readings, errors)`)를 구현해
  `ADAPTERS`에 등록하고, registry 지표의 `resolver`를 그 이름으로 바꾼다. 저장된 Decision은 고치지 않아도 CONNECTED가 된다.
- 판정 코드: `services/review.py` (`evaluate_trigger`, `display_status`, `to_view`). `/api/decisions`, Review Monitor, Demo 미리보기가 같은 함수를 쓴다.

### Review Monitor (`services/review_monitor.py`)

- `REVIEW_MONITOR_INTERVAL_SECONDS`(기본 30초)마다 metric source가 연결된 프로젝트의 active 결정을 확인한다. LLM 호출 없음.
- 기준 상태는 `decision_review_state`, 변화 이력은 `review_events`.
  - 처음 확인: 기준만 저장 (이벤트 없음)
  - ACTIVE → REVIEW_DUE: 이벤트 1개 + **Telegram 1회**
  - REVIEW_DUE → ACTIVE: 복구 이벤트 (알림 없음)
  - 같은 상태 유지: 이벤트 없음
- Telegram은 `hermes send --to telegram`으로 보낸다. backend에는 token / chat id를 두지 않는다. 실패해도 판정·기록에 영향 없음, 재시도 큐 없음.
- 웹은 `GET /api/review-events?project_id=&acknowledged=false`를 10초마다 polling해 상단 alert로 보여주고, `확인`으로 acknowledge한다 (Decision 상태는 바뀌지 않음).

### Review Memo (`services/review_memo.py`, prompt v4)

- REVIEW_DUE 결정 상세에서 사용자가 Generate할 때만 만든다 (advisory — Decision / review 상태 / Candidate를 바꾸지 않는다).
- Hermes를 `-z` oneshot으로 실행하고 toolset은 `decision-debt,gbrain`(둘 다 read-only MCP)만 허용한다. 사용한 tool은 Hermes `state.db`로 감사(tool_audit)한다.
- 사실 부분(결정, 조건, 관측값, 근거 인용)은 backend가 DB에서 그대로 렌더링하고, Hermes는 분석 섹션만 쓴다.
- 재생성하면 이전 memo는 History로 남는다.

### Deep Review (Paperclip multi-agent, `services/deep_review.py`)

자세한 설계: [docs/architecture/deep_review.md](docs/architecture/deep_review.md)

- 일반 검토는 저비용 Review Memo(single agent), 고신뢰 검토는 Deep Review, **최종 결정은 항상 사람**. Review Memo를 대체하지 않는다.
- 유효한(active) Decision 상세에서 **Run Deep Review**를 눌렀을 때만 시작한다 (자동 실행 없음, heartbeat 타이머 없음).
  - **REVIEW_DUE = 시스템이 '지금 재검토할 시점'이라고 판단한 상태 / Deep Review = 사람이 필요할 때 실행하는 분석.**
    REVIEW_DUE가 아니어도(ACTIVE, UNKNOWN, NEEDS_MAPPING, MANUAL) 실행할 수 있다. 막는 경우: 없는/대체된 Decision, 진행 중 run, 기능 꺼짐.
  - `review_reason`: 시작 시점에 REVIEW_DUE면 `TRIGGERED`, 아니면 `MANUAL` (누가 눌렀는지가 아니라 왜 재검토 대상인지).
  - **Deep Review 실행 ≠ REVIEW_DUE 전환**: review state / review event / Telegram / trigger 평가는 바뀌지 않는다.
  - snapshot에 실행 시점 `review_reason` / `review_state` / `mapping_status` / `current_value`를 고정한다. 현재 관측값이 없으면
    (NEEDS_MAPPING: metric not mapped, MANUAL: 사람이 판단) backend가 unknown을 결정적으로 넣고, `FACT:observed`를 근거로 쓸 수 없게 한다.
    agent가 값을 만들거나 manual run에서 trigger 충족을 주장하면 거부(Researcher) 또는 unsupported claim(Critic).
- Paperclip = control plane: company `Decision Pulse Deep Review`, 보고선 Evidence Researcher → Decision Analyst → Review Critic,
  결정 1건 = parent ticket 1개 + 역할별 하위 ticket (역할당 1 agent, atomic checkout), 월 budget hard stop.
- 역할 / 권한
  - Evidence Researcher: Hermes + Decision Debt MCP + gbrain MCP (둘 다 read-only, state.db로 tool 감사). 사실 수집만
  - Decision Analyst: tool 없음. Researcher JSON만 보고 known_changes(사실)와 implications(추론)를 분리
  - Review Critic: tool 없음. PASS | REVISE. REVISE면 Analyst에게 **한 번만** 반환, 그래도 PASS가 아니면 HUMAN_REVIEW_REQUIRED
- backend가 단계마다 JSON schema + reference(`E1`, `FACT:…`, 실제 `DEC-…`)를 검증한 뒤에만 다음 ticket을 만든다. 실패한 출력은 저장하지 않는다.
- Authoritative Facts(결정·rationale·전제·조건·관측값·evidence 원문)는 backend snapshot 그대로. 화면에서 `Authoritative record`와 `AI inference`를 구분해 표시.
- Paperclip이 꺼져 있거나 agent가 실패/timeout/budget 차단이면 run만 FAILED (재시도 가능). Review Monitor / Telegram / Review Memo / Ask Agent는 그대로 동작.
- adapter: Paperclip `process` adapter + `backend/deep_review_worker.py`. `hermes_gateway`는 Hermes API server가 꺼져 있고,
  `hermes_local`은 사용자 전역 `~/.hermes/skills`에 skill을 설치하므로 쓰지 않는다 (이유는 설계 문서).

### Read-only MCP (`backend/mcp_server.py`)

Hermes가 stdio subprocess로 띄운다 (`~/.hermes/config.yaml`의 `mcp_servers.decision-debt`). tool은 두 개뿐:
`list_review_due`, `get_decision_detail`. DB를 읽기 전용으로 연다.

---

## Extraction

- `services/extraction.py`: 원문 → Decision Candidate (2-stage, JSON schema 강제, 원문 인용 evidence 확인).
- `EXTRACTION_MODE=local`(기본): Qwen만. `auto`: Qwen 먼저, **구조화 실패일 때만** OpenRouter로 다시 추출 (`should_escalate()`).
- 결과는 항상 Candidate로만 저장된다.
- 오디오 Source 제목은 추출된 topic으로 자동 생성된다 (사람이 고친 제목은 바꾸지 않음).

## Knowledge sync (승인 후, 실패해도 승인은 유지)

- **Dify Knowledge** (`services/knowledge_sync.py`): 승인된 Decision을 문서로 추가.
- **gbrain** (`services/gbrain_sync.py`, `GBRAIN_SYNC_ENABLED=true`): Decision → Markdown(`~/decision-debt-brain/decisions`) → `gbrain sync --no-pull --source decision-debt --working-tree`. gbrain serve(Hermes gateway)가 PGLite를 쥐고 있으면 sync가 owner에게 위임된다.

---

## Backend API

모든 목록/생성은 `project_id` 범위.

| API | 설명 |
| --- | --- |
| `GET /api/health` | backend, metric source, review monitor 상태 |
| `GET/POST /api/projects`, `GET /api/projects/{id}` | 프로젝트 |
| `GET /api/metrics?project_id=`, `GET /api/metrics/{name}?project_id=` | 현재 지표 (404 없는 지표, 409 미연결, 503 check_metric 불가) |
| `GET /api/decisions?project_id=`, `GET /api/decisions/{id}` | 결정 + `display_status` + `evaluation` |
| `GET /api/sources?project_id=`, `POST /api/sources`, `GET/PATCH /api/sources/{id}` | Source (PATCH: 제목 수정 → 이후 자동 제목 변경 안 함) |
| `POST /api/sources/audio`, `POST /api/sources/{id}/transcribe` | 오디오 업로드(202) / 다시 전사 |
| `POST /api/extract` | `{project_id, title, type, content}` 또는 `{project_id, source_id}` → Candidate |
| `GET /api/candidates?project_id=&status=` | `pending` / `approved` / `ignored` / `processed`, 생략 시 전체 |
| `PATCH /api/candidates/{id}`, `POST .../approve`, `POST .../ignore` | Edit / Approve(Decision 저장 + knowledge sync) / Ignore |
| `POST /api/candidates/delete` | ignored 후보 기록 삭제 (Source / Decision은 유지) |
| `GET /api/review-events`, `POST /api/review-events/{id}/acknowledge` | Review Monitor 이벤트 |
| `POST /api/decisions/{id}/review-memo`, `GET .../review-memos`, `GET /api/review-memos/{id}` | Review Memo |
| `POST /api/decisions/{id}/deep-review?project_id=`, `GET /api/decisions/{id}/deep-reviews`, `GET /api/deep-reviews/{run_id}`, `GET /api/deep-review/status` | Deep Review (202 + PENDING, `review_reason` TRIGGERED | MANUAL / 404 없음 / 409 진행 중·대체된 Decision / 503 꺼짐) |
| `POST /api/agent/chat` | Ask Agent (Dify Chatflow proxy) |
| `GET /api/demo/status`, `GET /api/demo/state`, `GET /api/demo/state/{metric}/preview`, `PATCH /api/demo/state/{metric}`, `POST /api/demo/reset` | Demo Control Center (Demo Mode일 때만) |

## DB (SQLite, `PRAGMA user_version = 8`)

| 테이블 | 내용 |
| --- | --- |
| `projects` | name, slug, metric_source / agent_source (config 키만 저장) |
| `sources` | 원문 (project_id, type: meeting / issue / adr / pr / audio_transcript, content, human_authored) |
| `source_audio` | 오디오 원본 경로, 전사 상태/엔진, title_origin (v3, v6) |
| `candidates` | AI 추출 후보 (pending / approved / ignored, extractor, decision_id) |
| `decisions` | 사람이 승인한 결정 (review_trigger JSON, origin, human_verified, history) |
| `decision_sources` | Decision ↔ Source evidence |
| `decision_review_state`, `review_events` | Review Monitor 기준 상태 / 변화 이력 (v2) |
| `decision_knowledge_sync` | gbrain 동기화 상태 (v4) |
| `decision_review_memos` | Review Memo + 생성 시점 snapshot + tool_audit (v5) |
| `deep_review_runs`, `deep_review_artifacts` | Deep Review run (상태 / verdict / Paperclip 식별자 / 시작 시점 snapshot) + 역할별 검증된 JSON (v7). `review_reason` TRIGGERED / MANUAL (v8, 기존 run은 TRIGGERED) |

---

## 환경 변수 (`backend/.env`)

값은 커밋하지 않는다 (`.env*`는 git 제외). 이미 설정된 환경변수가 `.env`보다 우선.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DIFY_BASE_URL`, `DIFY_API_KEY` | `http://localhost/v1`, — | Ask Agent Chatflow |
| `DIFY_API_BASE`, `DIFY_KNOWLEDGE_API_KEY`, `DIFY_DATASET_ID` | — | 승인 후 Dify Knowledge 동기화 |
| `METRIC_TOOL_URL` | `http://localhost:8000` | check_metric |
| `LLM_BASE_URL`, `EXTRACTION_MODEL` | `http://localhost:8080/v1`, `qwen2.5-7b-instruct` | 로컬 추출 |
| `EXTRACTION_MODE` | `local` | `auto`면 구조화 실패 시 OpenRouter 재추출 |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_*` | — | escalation / 비교 실험 |
| `WHISPER_CLI`, `WHISPER_MODEL_PATH`, `WHISPER_LANGUAGE` | `whisper-cli`, `~/models/whisper/ggml-large-v3-turbo-q5_0.bin`, `ko` | 로컬 STT |
| `AUDIO_DIR`, `AUDIO_MAX_UPLOAD_MB` | `backend/data/audio`, `200` | 오디오 저장 |
| `GBRAIN_SYNC_ENABLED`, `GBRAIN_BIN`, `GBRAIN_REPO_DIR`, `GBRAIN_SOURCE_ID` | `false`, `gbrain`, `~/decision-debt-brain`, `decision-debt` | gbrain 동기화 |
| `HERMES_REVIEW_MEMO_ENABLED`, `HERMES_BIN`, `HERMES_HOME` | `false`, `hermes`, `~/.hermes` | Review Memo |
| `TELEGRAM_REVIEW_NOTIFICATIONS_ENABLED` | `true` | REVIEW_DUE Telegram 알림 |
| `REVIEW_MONITOR_ENABLED`, `REVIEW_MONITOR_INTERVAL_SECONDS` | `true`, `30` | Review Monitor |
| `DEMO_MODE_ENABLED` | `false` | Demo Control Center (production에서는 켜지 않는다) |
| `DEMO_TOOL_DIR` | `~/decision-debt-tool` | Demo가 실행하는 sample DB script 위치 |
| `DEEP_REVIEW_ENABLED` | `false` | Deep Review (Paperclip) |
| `PAPERCLIP_API_URL`, `PAPERCLIP_UI_URL` | `http://127.0.0.1:3110/api`, — | Paperclip API / 개발용 링크 (비우면 링크 숨김) |
| `DEEP_REVIEW_MODEL`, `DEEP_REVIEW_REASONING_EFFORT`, `DEEP_REVIEW_PROVIDER_SORT` | `OPENROUTER_MODEL`, `OPENROUTER_REASONING_EFFORT`, `throughput` | Analyst / Critic LLM (tool 없음) |
| `DEEP_REVIEW_STAGE_TIMEOUT_SECONDS`, `DEEP_REVIEW_RESEARCH_TIMEOUT_SECONDS`, `DEEP_REVIEW_LLM_TIMEOUT_SECONDS` | `420`, `240`, `240` | 단계 대기 / Hermes / LLM 시간 상한 |

---

## 테스트

```bash
cd backend && .venv/bin/python -m unittest discover -s tests   # 162개 (실제 Telegram / Hermes / gbrain / Paperclip / LLM 호출 없음)
cd web && npm run lint && npx tsc --noEmit && npm test && npm run build   # npm test: Node 내장 test runner (lib/*.test.ts)
```

## 운영 script (`backend/scripts/`)

| script | 용도 |
| --- | --- |
| `build_seed.py` | Dify Knowledge v4 원본 문서 → `seed_data.json` |
| `sync_gbrain.py` | 승인된 Decision을 gbrain에 수동 (재)동기화 (`--status`, `--dry-run`, `--all-verified`) |
| `sync_decision_to_dify.py` | Decision을 Dify Knowledge에 수동 동기화 |
| `backfill_audio_titles.py` | 오디오 Source 제목 backfill |
| `dedupe_sources.py` | 같은 원문(정규화 content hash)으로 중복 등록된 미연결 Source 정리. 기본 dry-run, `--apply`면 백업 후 삭제 |
| `run_extraction_comparison.py` | local vs OpenRouter 추출 비교 실험 |
| `setup_paperclip_org.py` | Paperclip company / goal / project / agent 3명 + budget을 만들거나 맞춘다 (멱등, 기본 dry-run, `--apply`) |
| `eval_deep_review.py` | Single Review Memo vs Deep Review 비교 → `docs/evals/deep_review_eval.json` (`--run <id>`면 먼저 Deep Review 실행) |

---

## 디렉터리

```
backend/
├─ app.py              API, lifespan(Review Monitor 시작 / 중단된 memo 정리)
├─ config.py           .env 로드, 연결 설정, 지표 registry, Demo baseline / 시나리오
├─ db.py               스키마 v1~v6 + migration + CRUD
├─ schemas.py          Pydantic 모델 (Review Trigger V2 포함)
├─ metric_registry.py  Canonical Metric Registry (지표 정의, alias, resolver 이름)
├─ mcp_server.py       Hermes용 read-only MCP (stdio)
├─ deep_review_worker.py  Paperclip process adapter worker (researcher / analyst / critic)
├─ services/           review, review_monitor, review_trigger, review_memo, telegram_notifier,
│                      extraction, metrics, stt, audio_ingest, source_title, knowledge_sync,
│                      gbrain_sync, decision_read, demo_state, dify, text,
│                      deep_review, deep_review_contract, paperclip_client, metric_adapters, trigger_status
├─ scripts/            위 운영 script
└─ tests/              unittest (fake_hermes / fake_gbrain 포함)

web/
├─ app/page.tsx                     Project Selector
├─ app/(workspace)/                 overview · decisions · review(Candidates) · sources · agent (공통 header layout)
├─ app/demo/                        Demo Control Center
├─ components/                      portfolio · layout · decision · review · sources · demo · agent · project · ui
└─ lib/                             api(서버) · client-api · types · metrics · portfolio · meeting-date · project

agents/deep-review/   역할별 instruction (researcher / analyst / critic.md) — Paperclip AGENTS.md + worker prompt
paperclip/            Paperclip 설치(package.json, 버전 고정) + instance data(git 제외) + org.json(git 제외)
docs/evals/           Single vs Multi 평가 결과
```

디자인: monochrome editorial (ink / paper / hairline). 색 accent(orange)는 REVIEW_DUE에만 쓴다. 토큰은 `web/app/globals.css`.

---

## 알려진 제한

- **중복 Source 방지 없음**: 같은 원문을 다시 Extract하면 새 Source가 생긴다. 과거 중복은 `dedupe_sources.py`로 정리했다 (2026-11-13, 60 → 28).
- **compound / duration 조건은 자동 판정하지 않는다** (MANUAL, 예: `MVP 레디스 미도입`). Demo에서 latency를 올려도 이 결정은 REVIEW_DUE가 되지 않는다.
- NEEDS_MAPPING 지표의 실제 connector(GitHub Actions, Prometheus, Stripe …)는 아직 없다. 화면의 '지표 연결' 버튼은 비활성.
- metric source는 기본 프로젝트(`check_metric_default`)만 연결되어 있다. 다른 프로젝트의 metric 조건은 `Metric not connected`.
- `backend/.env.example`은 초기 4개 키 + Deep Review 키만 담고 있다 — 전체 목록은 위 환경 변수 표 참고.
- Deep Review의 ticket 접두어는 Paperclip이 company 이름으로 정해 `DEC-7`처럼 보인다 (Decision ID와 혼동 주의, 변경 API 없음).
- Paperclip process run은 비용 정보가 없는 receipt를 남기므로 budget policy를 `unpricedUsagePolicy: allow`로 둔다. 실제 LLM 비용은 worker가 보고한다.
