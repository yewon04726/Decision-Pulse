# Decision Debt Agent — Claude Handoff / MVP Build Guide

> 목적: 이 문서는 Claude가 지금까지 진행된 Decision Debt Agent의 맥락, 로컬 AI 환경, Dify RAG/Tool Calling 구현 상태, 그리고 다음 주 MVP 웹사이트 구현 목표를 한 번에 이해하고 바로 개발을 이어갈 수 있도록 작성한 handoff 문서다.

---

## 0. Claude에게 먼저 전달할 핵심 지시

이 프로젝트는 이미 로컬 LLM/RAG/Function Calling 기반의 핵심 실험이 끝난 상태다. **기존 인프라를 임의로 갈아엎지 말고, 현재 동작하는 구성 위에 웹 MVP를 얹는 방향으로 진행한다.**

특히 다음 원칙을 지킨다.

- Ollama를 새로 도입하지 않는다. 현재 `llama.cpp + llama-swap` 구성을 유지한다.
- 기존 Dify Knowledge/RAG와 `check_metric` Function Calling을 재사용한다.
- 기존 설정 파일이나 Dify Docker 볼륨을 파괴적으로 수정하지 않는다.
- `docker compose down -v` 사용 금지.
- API Key, 비밀값을 프론트엔드에 노출하지 않는다.
- 외부 SaaS 연동(Notion OAuth, Slack, GitHub 자동 수집 등)은 MVP 핵심이 아니다. 시간이 남을 때만 진행한다.
- MVP에서 가장 중요한 것은 “Decision의 생명주기”가 화면에서 보이는 것이다.

---

# 1. 프로젝트 한 줄 설명

**Decision Debt Agent는 프로젝트의 과거 기술적 의사결정과 그 근거를 기억하고, 당시의 재검토 조건과 현재 프로젝트 상태를 비교하여 ‘이 결정을 다시 검토할 시점이 되었는지’를 알려주는 에이전트다.**

예시:

- 과거 결정: `MVP에서는 SQLite를 사용한다.`
- 이유: 초기 개발 속도 우선
- 재검토 조건: `가입자 수 > 1000`
- 현재 상태: `가입자 수 = 1500`
- 판단: `REVIEW_DUE`
- 주의: 조건 충족은 자동 변경을 의미하지 않는다. 사람의 재검토가 필요하다는 의미다.

---

# 2. 문제 정의

프로젝트의 기술적 의사결정과 근거는 회의록, Issue, PR, ADR 등에 흩어져 있다.

시간이 지나면 다음 문제가 발생한다.

1. 과거에 무엇을 결정했는지 찾기 어렵다.
2. 왜 그렇게 결정했는지 근거를 잊는다.
3. 임시 결정이나 재검토 조건이 있었어도 다시 볼 시점을 놓친다.
4. 현재 시스템 상태가 바뀌었는데도 과거 결정을 계속 유지한다.

Decision Debt Agent는 이를 두 축으로 해결한다.

```text
Decision Memory
├─ 과거 결정 검색
│  └─ "그때 뭐 하기로 했지?"
└─ Decision Debt Monitoring
   ├─ 사용자 수가 임계값을 넘었나?
   ├─ 응답 시간이 기준을 넘었나?
   ├─ 서버 수가 늘었나?
   └─ 조건 충족 → REVIEW_DUE
```

---

# 3. 목표 Decision 데이터 모델

최종적으로 확정된 결정은 아래와 유사한 구조로 저장한다.

```json
{
  "decision_id": "DEC-2026-0314-DB",
  "topic": "database",
  "decision": "MVP에서는 SQLite를 사용한다",
  "rationale": "초기 개발 속도를 우선한다",
  "assumptions": ["사용자 수가 적다"],
  "review_trigger": {
    "type": "metric",
    "metric": "user_count",
    "operator": ">",
    "threshold": 1000
  },
  "source": "meeting_2026-03-14.md",
  "status": "active",
  "human_verified": true
}
```

일반적인 영구 결정은 `review_trigger: null`일 수 있다.

최종 구조에서는 대략 다음 테이블을 고려한다.

- `decisions`
- `decision_sources`
- `review_triggers`
- `decision_revisions`

하지만 다음 주 MVP에서는 단순화를 위해 SQLite + 단순 스키마로 시작해도 된다.

---

# 4. 현재까지 구현 완료된 핵심 기능

## 4.1 로컬 LLM 실행 환경

Mac Apple Silicon 환경.

### llama.cpp / llama-swap

`llama-server` 경로:

```text
/opt/homebrew/bin/llama-server
```

llama-swap config:

```text
~/llama-swap/config.yaml
```

현재 등록 모델:

```text
lfm2.5-2.6b
qwen2.5-3b-instruct
qwen2.5-7b-instruct
qwen3-embedding
```

현재 실제 Agent용 모델은:

```text
qwen2.5-7b-instruct
```

GGUF 파일:

```text
/Users/yewon/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf
/Users/yewon/models/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf
```

llama-swap 설정 예시:

```yaml
"qwen2.5-7b-instruct":
  cmd: |
    llama-server --port ${PORT} -m /Users/yewon/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf -c 8192 --n-gpu-layers 99 --jinja
  ttl: 300
```

시작:

```bash
llama-swap --config ~/llama-swap/config.yaml --listen 0.0.0.0:8080
```

확인:

```bash
curl http://localhost:8080/v1/models
```

### Qwen2.5-7B Tool Calling 검증 완료

실제 `/v1/chat/completions` 테스트에서 다음 형태를 정상 반환했다.

```json
{
  "finish_reason": "tool_calls",
  "tool_calls": [
    {
      "type": "function",
      "function": {
        "name": "check_metric",
        "arguments": "{\"metric_name\": \"user_count\"}"
      }
    }
  ]
}
```

즉 llama.cpp + Qwen2.5-7B 조합에서 native `tools/tool_calls` Function Calling이 정상 동작한다.

---

# 5. Dify 환경

Dify는 self-hosted Docker로 실행 중이다.

경로:

```text
~/dify/docker
```

시작:

```bash
cd ~/dify/docker
docker compose up -d
```

로컬 UI:

```text
http://localhost
```

중지:

```bash
cd ~/dify/docker
docker compose down
```

주의:

```text
docker compose down -v
```

는 사용하지 않는다. 데이터/볼륨이 삭제될 수 있다.

---

# 6. Dify Knowledge / RAG 현재 상태

현재 Knowledge 이름:

```text
Decision Debt Knowledge v4
```

현재 문서는 10개 수준의 테스트 문서이며, 핵심 결정이 한 청크 안에 들어가도록 재작성되어 있다.

주요 문서:

```text
meeting_01_database.md
meeting_02_cache.md
meeting_03_auth_v2.md
meeting_04_logging.md
meeting_05_deploy.md
meeting_06_monitoring.md
issue_01_database_scale.md
issue_02_redis.md
adr_01_storage.md
adr_02_api_version.md
```

RAG 설정:

```text
Retrieval: Vector Search
Embedding: qwen3-embedding
Top K: 5
Score Threshold: 0.50
Rerank: OFF
```

중요한 실험 결과:

### DB 질문

질문:

```text
현재 DB 결정을 다시 검토해야 해?
```

검색 결과 상위에 다음이 정상 노출된다.

```text
issue_01_database_scale.md
meeting_01_database.md
```

내용:

```text
MVP에서는 SQLite 사용
가입자 수 > 1000이면 PostgreSQL 재검토
```

### Storage 질문

정답 문서 점수가 약 0.53 수준이어서 threshold를 0.55로 올리면 정답이 사라졌다.
따라서 현재 threshold는 0.50 유지.

### Kafka 같은 없는 질문

threshold 0.50에서 관련 근거가 없으면 “근거 문서에서 확인할 수 없습니다. 확인 필요” 형태로 처리 가능.

---

# 7. 현재 Dify Agent 구조

최종적으로 원하는 워크플로우 구조:

```text
Start
  ↓
Knowledge Retrieval
  ↓
Agent
  ├─ 검색된 과거 결정 읽기
  └─ check_metric 호출
  ↓
Answer
```

Agent 설정:

```text
Strategy: FunctionCalling (Support MCP Tools)
Model: Qwen2.5-7B-Instruct
Tool: Decision Debt Metric → check_metric
MCP Servers config: {}
Maximum Iterations: 5
Query: Start → query
```

반드시 Agent Instruction 안에 `Knowledge Retrieval → result` 변수를 실제 Dify 변수로 삽입해야 한다.

과거에 이 변수를 빠뜨렸더니 Agent가 DB 관련 지표를 임의로 만들어낸 문제가 있었다.

---

# 8. check_metric Tool 구현

FastAPI 프로젝트 경로:

```text
~/decision-debt-tool
```

가상환경:

```bash
cd ~/decision-debt-tool
source .venv/bin/activate
```

실행:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

현재 Tool API 예시:

```text
GET /metrics/user_count
GET /metrics/search_api_p95_ms
GET /metrics/server_count
```

현재 mock metrics:

```python
METRICS = {
    "user_count": {
        "value": 1500,
        "unit": "users"
    },
    "search_api_p95_ms": {
        "value": 850,
        "unit": "ms"
    },
    "server_count": {
        "value": 1,
        "unit": "servers"
    }
}
```

예:

```bash
curl http://localhost:8000/metrics/user_count
```

결과:

```json
{
  "metric_name": "user_count",
  "value": 1500,
  "unit": "users"
}
```

---

# 9. Dify Tool 등록

Dify에서 `Swagger API as Tool`을 사용했다.

FastAPI의 `/openapi.json` 자동 가져오기는 Dify 쪽 파서 문제로 바로 되지 않아, OpenAPI 스키마를 직접 등록했다.

중요 부분:

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "Decision Debt Metric API",
    "version": "1.0.0"
  },
  "servers": [
    {
      "url": "http://host.docker.internal:8000"
    }
  ],
  "paths": {
    "/metrics/{metric_name}": {
      "get": {
        "operationId": "check_metric"
      }
    }
  }
}
```

Tool 이름:

```text
Decision Debt Metric
```

Function:

```text
check_metric(metric_name)
```

---

# 10. Dify SSRF 예외 설정

Dify Docker 내부에서 Mac의 FastAPI에는 정상 접근 가능했지만, Dify SSRF proxy가 `host.docker.internal` 사설 주소를 차단했다.

Docker 내부 접근 테스트:

```bash
cd ~/dify/docker

docker compose exec api python -c \
"import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8000/metrics/user_count').read().decode())"
```

정상 결과:

```json
{"metric_name":"user_count","value":1500,"unit":"users"}
```

수정 파일:

```text
~/dify/docker/ssrf_proxy/squid.conf.template
```

추가한 예외:

```text
acl decision_metric_host dstdomain host.docker.internal
acl decision_metric_port port 8000
http_access allow decision_metric_host decision_metric_port
http_access deny to_private_networks
```

그 후:

```bash
docker compose restart ssrf_proxy
```

현재 Tool Calling 정상 동작.

---

# 11. 실제 Tool Call 로그 검증

Dify Agent 실행 로그에서 다음이 확인되었다.

```text
label: CALL check_metric
status: success
```

입력:

```json
{
  "metric_name": "user_count"
}
```

응답:

```json
{
  "metric_name": "user_count",
  "unit": "users",
  "value": 1500
}
```

실제 Function Calling 흐름:

```text
ROUND 1
→ Agent가 check_metric(user_count) 호출

CALL check_metric
→ 1500 반환

ROUND 2
→ Agent가 RAG 근거 + 1500을 사용해 최종 답변 생성
```

---

# 12. 현재 Agent 출력의 알려진 사소한 문제

Dify Agent가 ROUND 1의 자연어 Thought까지 최종 text에 합치는 경우가 있다.

예:

```text
먼저 가입자 수와 관련된 재검토 조건을 확인하겠습니다.
```

이 한 줄이 최종 답변 앞에 붙을 수 있다.

이것은 Function Calling 자체의 실패가 아니다.
실제 Tool Call은 정상이며, Dify Agent의 intermediate text 병합 동작에 가깝다.

MVP에서는 크게 신경 쓰지 않아도 된다.
나중에 사용자 출력 품질이 중요해지면:

```text
Agent
→ Tool Call / 판단
→ Final Formatter LLM
→ 사용자 답변
```

구조로 분리 가능하다.

---

# 13. 현재 Agent가 정상적으로 해야 하는 대표 시나리오

질문:

```text
현재 DB 결정을 다시 검토해야 해?
```

정상 처리:

```text
RAG
→ MVP에서는 SQLite 사용
→ 가입자 수 > 1000이면 PostgreSQL 도입 재검토

Tool
→ check_metric("user_count")
→ 1500

판단
→ 1500 > 1000
→ 재검토 조건 충족
→ PostgreSQL 도입 여부 재검토 필요
→ 즉시 PostgreSQL로 자동 변경하는 것은 아님
```

---

# 14. 현재 Agent Instruction 권장 버전

Dify Agent에는 너무 긴 프롬프트를 넣지 않는 것이 좋다.
Qwen2.5-7B가 안정적으로 따라갈 정도로 아래 수준을 유지한다.

```text
너는 Decision Debt Agent다.

[검색된 문서]
<여기에 실제 Knowledge Retrieval → result 변수를 삽입>

규칙:
1. 과거 결정, 결정 이유, 재검토 조건은 검색된 문서에서만 가져온다.
2. 재검토에 필요한 지표 이름도 검색된 재검토 조건에서 찾는다.
   문서에 없는 지표 이름을 추측하거나 만들어내지 마라.
3. 현재 상태가 필요하면 check_metric을 호출한다.
4. check_metric에는 검색된 재검토 조건에 실제로 등장한 지표만 사용한다.
5. 현재값과 임계값을 비교한다.
   - 현재값 > 임계값: 충족됨
   - 현재값 <= 임계값: 충족되지 않음
6. 조건이 충족되면 해당 결정을 재검토해야 한다.
   단, 조건 충족만으로 기존 결정이 자동 변경되는 것은 아니다.
7. 문서와 도구 결과에 없는 대안이나 지표를 제안하지 않는다.

답변 형식:
- 결정 내용:
- 결정 이유:
- 재검토 조건:
- 현재 지표 값:
- 재검토 조건 충족 여부:
- 현재 판단:
- 근거 문서:
```

---

# 15. 중요한 개념적 구분

Decision Debt Agent에서 역할을 명확히 구분한다.

```text
RAG
→ 과거를 기억

check_metric
→ 현재 상태를 관찰

Agent
→ 과거 조건과 현재 상태를 연결해 재검토 필요 여부를 판단
```

장기적으로는 `1500 > 1000` 같은 deterministic 비교는 LLM이 아니라 코드에서 처리하는 것이 더 안정적이다.

하지만 4주차 Function Calling 실습에서는 Agent가 실제 Tool을 선택하고 호출하는 모습을 보여주기 위해 현재 구조를 유지했다.

---

# 16. 아직 구현하지 않은 핵심 기능: Decision Extraction

현재 Knowledge의 `.md` 문서는 사람이 직접 작성한 테스트 데이터다.

최종 제품에서는 사람이 md를 만들면 안 된다.

목표 파이프라인:

```text
회의록 / Slack / Issue / PR / ADR
        ↓
전처리 / 주제 분할
        ↓
Decision Candidate 탐지
        ↓
결정 / 이유 / 전제 / 재검토 조건 구조화 추출
        ↓
기존 결정과 중복 / 충돌 검사
        ↓
Human-in-the-Loop
Approve / Edit / Ignore
        ↓
Decision Memory 저장
        ↓
Trigger Monitoring
```

Extraction Agent 출력 예시:

```json
{
  "topic": "database",
  "decision": "MVP에서는 SQLite를 사용한다",
  "rationale": "초기 개발 속도를 우선한다",
  "assumptions": ["사용자 수가 적다"],
  "review_trigger": {
    "type": "metric",
    "metric": "user_count",
    "operator": ">",
    "threshold": 1000
  },
  "evidence": "가입자가 1000명을 넘으면 PostgreSQL을 다시 검토한다"
}
```

중요:

- LLM이 바로 확정 DB에 INSERT하지 않는다.
- Candidate로 추출한다.
- 사람이 Approve/Edit/Ignore 한다.
- 승인된 것만 Decision Memory에 저장한다.

---

# 17. 다음 주 MVP 목표

다음 주 중간고사 MVP에서는 단순 챗봇이 아니라 아래 한 사이클을 실제로 보여주는 것을 목표로 한다.

```text
회의록 입력
   ↓
AI가 Decision Candidate 추출
   ↓
사람이 확인 / 수정 / 승인
   ↓
Decision Memory 저장
   ↓
Dashboard에 결정 표시
   ↓
현재 metric 조회
   ↓
재검토 조건 판단
   ↓
REVIEW DUE 표시
   ↓
Agent에게 질문
"현재 DB 결정을 다시 검토해야 해?"
   ↓
RAG + Tool Call + Evidence 기반 답변
```

---

# 18. 웹 기술 스택 — 권장안

## 결론

**React를 직접 bare setup으로 시작하지 말고 Next.js를 사용한다.**
Next.js는 React 기반이고, 라우팅/레이아웃/API 연동/배포 구조가 이미 잡혀 있어서 1주 MVP에 더 적합하다.

권장 스택:

```text
Frontend
- Next.js (React)
- TypeScript
- App Router
- Tailwind CSS
- shadcn/ui
- lucide-react

Backend
- FastAPI
- Pydantic
- SQLAlchemy 또는 SQLModel
- SQLite (MVP persistence)

AI / Agent
- Dify
- llama.cpp
- llama-swap
- Qwen2.5-7B-Instruct
- Qwen3 Embedding

RAG
- Dify Knowledge

Tool
- FastAPI check_metric
```

### 왜 Next.js인가

- React 기반이라 컴포넌트 개발이 빠름
- Dashboard / 상세 페이지 / 채팅 페이지 라우팅이 쉬움
- TypeScript와 궁합 좋음
- Tailwind + shadcn/ui로 관리형 SaaS 스타일 UI를 빠르게 만들 수 있음
- 프론트에서 직접 Dify secret을 노출하지 않고 backend proxy 구조를 만들기 쉬움

### MVP에서는 굳이 넣지 말 것

- Redux
- 복잡한 상태관리 라이브러리
- GraphQL
- Kubernetes
- 별도 microservice 분리
- Notion OAuth
- GitHub App
- Slack App

---

# 19. 추천 프로젝트 구조

새 웹 프로젝트 루트 예시:

```text
decision-debt-mvp/
├─ web/                    # Next.js
│  ├─ app/
│  │  ├─ page.tsx          # Dashboard
│  │  ├─ decisions/
│  │  │  ├─ page.tsx
│  │  │  └─ [id]/page.tsx
│  │  ├─ review/page.tsx
│  │  ├─ sources/page.tsx
│  │  └─ agent/page.tsx
│  ├─ components/
│  └─ lib/
│
├─ backend/                # FastAPI
│  ├─ app.py
│  ├─ models.py
│  ├─ schemas.py
│  ├─ db.py
│  └─ services/
│     ├─ dify.py
│     ├─ extraction.py
│     └─ metrics.py
│
└─ docs/
   └─ architecture.md
```

기존 `~/decision-debt-tool`의 FastAPI 코드를 backend로 옮겨 합치거나, 초기에는 그대로 두고 web만 붙여도 된다.

---

# 20. 웹 화면 구성

## 20.1 Dashboard

최우선 페이지.

사이드바:

```text
Dashboard
Decisions
Review Queue
Sources
Ask Agent
```

상단 KPI 카드:

```text
Active Decisions
Review Due
Pending Review
Sources
```

핵심 카드 예시:

```text
⚠ Review Required

Database
Current Decision
SQLite

Review Trigger
User Count > 1,000

Current
1,500 users

Status
REVIEW DUE

[View Decision]
```

첫 화면에서 교수/사용자가 바로 제품 목적을 이해해야 한다.

---

## 20.2 Decisions

Decision Memory 목록.

카드 예시:

```text
Database
SQLite

Reason
초기 개발 속도 우선

Review Trigger
user_count > 1000

Current
1500

Status
REVIEW DUE
```

상태:

```text
ACTIVE
REVIEW_DUE
PENDING_REVIEW
SUPERSEDED
```

---

## 20.3 Decision Detail

표시 요소:

```text
Topic
Decision
Rationale
Assumptions
Review Trigger
Current Metric
Status
Evidence
Source
Revision History (stretch)
```

Evidence 예시:

```text
2026-03-14 Infrastructure Meeting
"가입자가 1,000명을 넘으면 PostgreSQL 도입을 다시 검토한다."
```

---

## 20.4 Review Queue

MVP에서 차별점이 강한 화면.

사용자가 회의록을 붙여넣거나 `.md/.txt`를 업로드한다.

예:

```text
이번 MVP에서는 SQLite를 사용하기로 했다.
현재 규모에서는 충분하고 구현 속도가 빠르기 때문이다.
다만 가입자가 1000명을 넘으면 PostgreSQL 도입을 다시 검토한다.
```

`Extract Decisions` 클릭.

AI 결과:

```text
Decision Candidate

Topic
Database

Decision
MVP에서는 SQLite를 사용한다.

Reason
초기 개발 속도 우선

Review Trigger
user_count > 1000

Source
meeting_2026_10_11.md

[Approve] [Edit] [Ignore]
```

Approve 시 Decision Memory에 저장.

---

## 20.5 Sources

회의록 / Issue / ADR 등의 원문 목록.

MVP에서는 실제 GitHub/Notion 동기화까지 하지 않아도 된다.

기능:

```text
- source 목록
- source type 표시
- 원문 보기
- 해당 source에서 추출된 Decision 연결
```

---

## 20.6 Ask Agent

현재 Dify Agent를 활용하는 화면.

왼쪽/중앙:

```text
User
현재 DB 결정을 다시 검토해야 해?

Agent
현재 가입자 수는 1,500명이며,
재검토 조건인 1,000명을 초과했습니다.
따라서 PostgreSQL 도입 여부를 재검토해야 합니다.
단, 조건 충족만으로 기존 결정이 자동 변경되는 것은 아닙니다.
```

오른쪽 panel:

```text
Evidence
- meeting_01_database.md
- issue_01_database_scale.md

Tool Activity
check_metric
metric_name: user_count
result: 1500
```

Tool Activity를 UI에서 보여주면 4주차 Function Calling 구현이 눈에 보인다.

---

# 21. MVP용 간단한 DB 스키마

SQLite로 빠르게 구현할 경우 예시.

## decisions

```text
id
slug
topic
decision
rationale
assumptions_json
review_trigger_json
status
human_verified
created_at
updated_at
```

## sources

```text
id
title
type
content
created_at
```

## decision_sources

```text
id
decision_id
source_id
evidence_text
```

## review_events (optional)

```text
id
decision_id
metric_name
metric_value
triggered
checked_at
```

MVP에서는 `review_trigger_json` 하나로 단순화해도 충분하다.

---

# 22. 추천 FastAPI 엔드포인트

```text
GET    /api/health

GET    /api/decisions
GET    /api/decisions/{id}
POST   /api/decisions
PATCH  /api/decisions/{id}

GET    /api/sources
POST   /api/sources
GET    /api/sources/{id}

POST   /api/extract
POST   /api/review/{candidate_id}/approve
POST   /api/review/{candidate_id}/ignore

GET    /api/metrics/{metric_name}

POST   /api/agent/chat
```

`/api/agent/chat`는 프론트가 Dify secret을 직접 가지지 않도록 backend proxy 역할을 한다.

---

# 23. 웹에서 Review Due 계산 방식

MVP에서는 모든 조건을 LLM에 맡기지 않는다.

가능하면 FastAPI에서 deterministic하게 처리한다.

예:

```python
if metric_value > threshold:
    status = "REVIEW_DUE"
else:
    status = "ACTIVE"
```

Agent는 설명용.

```text
Code
→ 정확한 상태 판정

LLM
→ 사람이 읽기 좋은 설명
```

이 구조가 장기적으로 더 안정적이다.

---

# 24. MVP 데모 시나리오

발표에서는 아래 1~2분 시나리오가 가장 설득력 있다.

```text
1. Raw Meeting 입력

"MVP에서는 SQLite를 사용한다.
가입자가 1000명을 넘으면 PostgreSQL을 다시 검토한다."

2. AI Extraction

Decision: SQLite
Trigger: user_count > 1000

3. Human Approve

4. Dashboard

SQLite
Current Users: 1500
REVIEW DUE

5. Ask Agent

"현재 DB 결정을 다시 검토해야 해?"

6. Agent

RAG → 과거 결정 확인
Tool → user_count = 1500

7. 결과

"PostgreSQL 도입 여부를 재검토해야 합니다."

8. Evidence + Tool Activity 표시
```

---

# 25. 다음 주까지 구현 우선순위

## P0 — 반드시 구현

- Next.js 기본 Layout / Sidebar
- Dashboard
- Decision 목록
- Decision 상세
- 실제 `check_metric` 현재값 표시
- REVIEW_DUE 계산
- Ask Agent 페이지
- Dify Agent 연동
- Evidence 표시

## P1 — 가능하면 반드시 구현

- 회의록 입력
- Decision Candidate Extraction
- Approve / Edit / Ignore
- SQLite 저장
- Review Queue

## P2 — 여유 있으면

- Source 목록
- 검색 / 필터
- Revision History
- Tool Activity UI
- 간단한 차트

## 이번 MVP에서 제외

- Notion OAuth
- Slack 연동
- GitHub 자동 수집
- 실시간 background scheduler
- 복잡한 multi-agent orchestration
- Paperclip/Hermes 본격 통합

---

# 26. 구현 순서 권장

## Day 1

```text
Next.js 프로젝트 생성
→ Sidebar/Layout
→ Dashboard UI
→ Decisions mock data
```

## Day 2

```text
FastAPI backend 정리
→ SQLite
→ Decisions CRUD
→ check_metric 통합
→ Dashboard real data
```

## Day 3

```text
Ask Agent
→ FastAPI proxy
→ Dify API 연결
→ Evidence / Tool Activity 표시
```

## Day 4

```text
Raw Meeting 입력
→ Qwen2.5-7B Extraction
→ Candidate 생성
```

## Day 5

```text
Approve/Edit/Ignore
→ DB 저장
→ Review Queue
```

## Day 6

```text
Review Due 계산
→ UI polish
→ Evidence 연결
→ 에러 처리
```

## 마지막

```text
발표 데이터 고정
→ 데모 동선 고정
→ 화면 녹화 backup
→ DSL / DB / 설정 백업
```

---

# 27. 현재 전체 로컬 실행 구성

### 1. llama-swap

```bash
llama-swap --config ~/llama-swap/config.yaml --listen 0.0.0.0:8080
```

### 2. Dify

```bash
cd ~/dify/docker
docker compose up -d
```

### 3. FastAPI metric tool

```bash
cd ~/decision-debt-tool
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 4. OmniRoute

기존 LFM 기반 Dify 모델에 필요했던 중간 라우터.
현재 Qwen2.5-7B Agent는 Dify에서 llama-swap에 직접 연결할 수 있으므로 새 MVP 웹에서는 필수 아님.

기존 OmniRoute 포트:

```text
http://localhost:20128
```

---

# 28. 현재 Dify 모델 등록

Qwen2.5-7B Dify OpenAI-compatible provider 설정:

```text
Model Name: qwen2.5-7b-instruct
Model Type: LLM
Completion mode: Chat
API Base URL: http://host.docker.internal:8080/v1
model name for API endpoint: qwen2.5-7b-instruct
Model context size: 8192
Compatibility mode: OpenAI compatible
API Type: Chat Completions API (/chat/completions)
Function Call Type: Tool Call
Stream function calling: Not Support
Thinking Mode Support: Only Non-Thinking Mode
```

---

# 29. 하지 말아야 할 것

- 현재 잘 되는 Dify Knowledge를 다시 새로 만들지 않는다.
- threshold를 무작정 0.55 이상으로 올리지 않는다. Storage 문서가 사라질 수 있다.
- Agent가 RAG 결과 변수를 못 받게 하지 않는다.
- LLM에게 DB 이름, metric 이름을 자유롭게 추측하게 하지 않는다.
- `status: active` 같은 Decision status와 실제 metric state를 섞지 않는다.
- Tool Call 성공 여부와 최종 문장 품질 문제를 혼동하지 않는다.
- 중간고사 MVP에서 Notion OAuth에 시간을 쓰지 않는다.
- Ollama를 새로 도입하지 않는다.
- Dify Docker volume을 삭제하지 않는다.

---

# 30. Claude가 지금 바로 시작해야 할 작업

1. 새 `decision-debt-mvp` 프로젝트를 생성한다.
2. `web`은 Next.js + TypeScript + Tailwind로 만든다.
3. 관리형 SaaS Dashboard 스타일의 Sidebar/Layout을 먼저 만든다.
4. 아래 5개 route를 만든다.

```text
/
/decisions
/decisions/[id]
/review
/agent
```

5. 초기에는 mock data로 UI를 완성한다.
6. 그 다음 FastAPI backend를 붙인다.
7. 기존 `check_metric`을 backend service로 재사용한다.
8. Ask Agent는 Dify API를 backend에서 호출하도록 한다.
9. Raw meeting → Decision Candidate extraction은 Qwen2.5-7B를 사용한다.
10. Candidate는 자동 확정하지 않고 Approve/Edit/Ignore UI를 반드시 둔다.

---

# 31. 가장 중요한 제품 메시지

Meetily 같은 회의 기록 도구와 달리 Decision Debt의 핵심은 단순 회의 요약이 아니다.

```text
Meeting Memory
→ "무슨 얘기를 했지?"

Decision Debt
→ "무엇을 결정했고, 왜 그렇게 했고,
   지금 다시 검토해야 할 때가 되었나?"
```

이 차별점이 Dashboard, Review Queue, Review Due 상태, Evidence, Tool Activity에서 눈에 보이도록 설계한다.

---

# 32. MVP 성공 기준

다음 흐름이 실제로 끊기지 않고 동작하면 MVP 성공으로 본다.

```text
Raw Source 입력
→ Decision 추출
→ Human Approve
→ Decision 저장
→ Dashboard 표시
→ 현재 metric 조회
→ REVIEW_DUE 계산
→ Agent 질문
→ RAG + Tool Call 기반 답변
→ Evidence 표시
```

이 전체 흐름을 1~2분 내에 데모할 수 있는 것이 목표다.

