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
Review Memo (사용자가 요청 시, Hermes + read-only MCP) ──▶ 사람이 재검토
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

> Dify / llama-swap / check_metric 설정은 이 프로젝트에서 수정하지 않는다. `docker compose down -v` 금지.

---

## 실행

```bash
./dev.sh    # Dify → llama-swap → check_metric → backend → web → Hermes gateway, 이어서 health check
./stop.sh   # dev.sh가 시작한 프로세스만 종료
```

- 이미 실행 중인 서비스(포트 LISTEN 중)는 건너뛰고, stop.sh도 종료하지 않는다.
- dev.sh가 시작한 프로세스는 `.run/<name>.pid`(pid, 시작 시각, 명령 패턴)에 기록되고, stop.sh는 세 값이 모두 맞을 때만 프로세스 그룹 단위로 종료한다.
- 로그: `logs/{dify,llama-swap,check-metric,backend,web,hermes}.log`
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
| `/decisions`, `/decisions/[id]` | Decision 목록 / 상세 (evidence, review trigger, revision history, **Review Memo** 패널) |
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

| type | 자동 판정 | 설명 |
| --- | --- | --- |
| `metric` (등록된 지표) | O | `user_count`, `search_api_p95_ms`, `server_count` (`config.KNOWN_METRICS`) |
| `metric` (미등록 지표) | X | "지표 매핑 필요" |
| `compound` (AND/OR) | X | 예: `search_api_p95_ms > 800 AND 3일 연속` — 사람이 확인 |
| `event`, `duration`, `date`, `manual` | X | 원문 조건을 보여주고 사람이 확인 |

- 판정 코드: `services/review.py` (`evaluate_trigger`, `display_status`). `/api/decisions`, Review Monitor, Demo 미리보기가 같은 함수를 쓴다.
- check_metric 조회 실패 → `UNKNOWN` (이벤트 없음).

### Review Monitor (`services/review_monitor.py`)

- `REVIEW_MONITOR_INTERVAL_SECONDS`(기본 30초)마다 metric source가 연결된 프로젝트의 active 결정을 확인한다. LLM 호출 없음.
- 기준 상태는 `decision_review_state`, 변화 이력은 `review_events`.
  - 처음 확인: 기준만 저장 (이벤트 없음)
  - ACTIVE → REVIEW_DUE: 이벤트 1개 + **Telegram 1회**
  - REVIEW_DUE → ACTIVE: 복구 이벤트 (알림 없음)
  - 같은 상태 유지: 이벤트 없음
- Telegram은 `hermes send --to telegram`으로 보낸다. backend에는 token / chat id를 두지 않는다. 실패해도 판정·기록에 영향 없음, 재시도 큐 없음.
- 웹은 `GET /api/review-events?acknowledged=false`를 10초마다 polling해 상단 alert로 보여주고, `확인`으로 acknowledge한다 (Decision 상태는 바뀌지 않음).

### Review Memo (`services/review_memo.py`, prompt v4)

- REVIEW_DUE 결정 상세에서 사용자가 Generate할 때만 만든다 (advisory — Decision / review 상태 / Candidate를 바꾸지 않는다).
- Hermes를 `-z` oneshot으로 실행하고 toolset은 `decision-debt,gbrain`(둘 다 read-only MCP)만 허용한다. 사용한 tool은 Hermes `state.db`로 감사(tool_audit)한다.
- 사실 부분(결정, 조건, 관측값, 근거 인용)은 backend가 DB에서 그대로 렌더링하고, Hermes는 분석 섹션만 쓴다.
- 재생성하면 이전 memo는 History로 남는다.

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
| `POST /api/agent/chat` | Ask Agent (Dify Chatflow proxy) |
| `GET /api/demo/status`, `GET /api/demo/state`, `GET /api/demo/state/{metric}/preview`, `PATCH /api/demo/state/{metric}`, `POST /api/demo/reset` | Demo Control Center (Demo Mode일 때만) |

## DB (SQLite, `PRAGMA user_version = 6`)

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

---

## 테스트

```bash
cd backend && .venv/bin/python -m unittest discover -s tests   # 125개 (실제 Telegram / Hermes / gbrain 호출 없음)
cd web && npm run lint && npx tsc --noEmit && npm test          # npm test: Node 내장 test runner (lib/*.test.ts)
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

---

## 디렉터리

```
backend/
├─ app.py              API, lifespan(Review Monitor 시작 / 중단된 memo 정리)
├─ config.py           .env 로드, 연결 설정, 지표 registry, Demo baseline / 시나리오
├─ db.py               스키마 v1~v6 + migration + CRUD
├─ schemas.py          Pydantic 모델 (Review Trigger V2 포함)
├─ mcp_server.py       Hermes용 read-only MCP (stdio)
├─ services/           review, review_monitor, review_trigger, review_memo, telegram_notifier,
│                      extraction, metrics, stt, audio_ingest, source_title, knowledge_sync,
│                      gbrain_sync, decision_read, demo_state, dify, text
├─ scripts/            위 운영 script
└─ tests/              unittest (fake_hermes / fake_gbrain 포함)

web/
├─ app/page.tsx                     Project Selector
├─ app/(workspace)/                 overview · decisions · review(Candidates) · sources · agent (공통 header layout)
├─ app/demo/                        Demo Control Center
├─ components/                      portfolio · layout · decision · review · sources · demo · agent · project · ui
└─ lib/                             api(서버) · client-api · types · metrics · portfolio · meeting-date · project
```

디자인: monochrome editorial (ink / paper / hairline). 색 accent(orange)는 REVIEW_DUE에만 쓴다. 토큰은 `web/app/globals.css`.

---

## 알려진 제한

- **중복 Source 방지 없음**: 같은 원문을 다시 Extract하면 새 Source가 생긴다. 과거 중복은 `dedupe_sources.py`로 정리했다 (2026-11-13, 60 → 28).
- **compound / duration 조건은 자동 판정하지 않는다** (예: `MVP 레디스 미도입`). Demo에서 latency를 올려도 이 결정은 REVIEW_DUE가 되지 않는다.
- metric source는 기본 프로젝트(`check_metric_default`)만 연결되어 있다. 다른 프로젝트의 metric 조건은 `Metric not connected`.
- `backend/.env.example`은 초기 4개 키만 담고 있다 — 전체 목록은 위 환경 변수 표 참고.
