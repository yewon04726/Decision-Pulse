# Normal Review vs Deep Review

Decision Pulse는 REVIEW_DUE 결정에 대해 두 가지 검토를 제공한다. 둘 다 **advisory** 산출물이며 Decision을 바꾸지 않는다.

| | Normal Review (Review Memo) | Deep Review |
| --- | --- | --- |
| 구성 | Hermes 단일 agent | Paperclip 조직: Evidence Researcher → Decision Analyst → Review Critic |
| 비용 / 시간 | 1 Hermes session · 약 25초 | Hermes 1 + tool 없는 LLM 2~4회 · 약 30초 · 약 $0.01 |
| 검증 | 형식·사실성 규칙 검사 (backend) | 단계마다 JSON schema + reference 검증 (backend) + Critic 교차 검증 |
| 결과 | Markdown memo | 구조화 JSON (facts / changes / implications / unknowns / checks / verdict) |
| 언제 | 일반 검토 | 고신뢰 검토가 필요할 때 사용자가 실행 |

```
Meeting / Audio
→ Decision Extraction
→ Candidate
→ Human Approval
→ Decision Memory (SQLite, gbrain)
→ State Monitoring (Review Monitor, deterministic)
→ REVIEW_DUE
       ├─ Generate Review Memo
       │      └─ Single Agent / Hermes (read-only MCP)
       │
       └─ Run Deep Review
              └─ Paperclip (control plane)
                  → Evidence Researcher   (Hermes + Decision Debt MCP + gbrain MCP, read-only)
                  → Decision Analyst      (no tools, Researcher JSON only)
                  → Review Critic         (no tools, PASS | REVISE)
                  → Human Review
```

**Paperclip is the orchestration/control plane. SQLite remains the Decision Pulse application database.
Agents never autonomously modify confirmed Decisions.**

---

## 역할 분담

| 구성 요소 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| SQLite | Decision 원본, Deep Review run / artifact 저장 | — |
| Review Monitor | ACTIVE / REVIEW_DUE 판정 (deterministic) | Paperclip은 이 판정을 하지 않는다 |
| Paperclip | 조직·보고선, ticket 계보(parent + 역할별 하위 ticket), 배정 → 실행(wake), atomic checkout, budget hard stop, 실행 기록·비용 | Decision 저장/수정, trigger 평가, Candidate 승인 |
| backend `services/deep_review.py` | handoff gate: ticket 결과를 schema 검증한 뒤에만 다음 ticket 생성, revision 상한·종료 조건 강제, 결과 저장 | AI 판단 (별도 Orchestrator agent 없음) |
| worker `deep_review_worker.py` | Paperclip process adapter로 실행되는 역할별 실행기 | SQLite 쓰기, gbrain 쓰기 |

## 흐름

```
POST /api/decisions/{id}/deep-review
  → REVIEW_DUE 확인 (Review Memo와 같은 read service) · 진행 중 run 없음 확인
  → deep_review_runs: PENDING + Authoritative Facts snapshot
  → Paperclip parent ticket "Deep Review: <id>" + "Collect authoritative evidence" (Evidence Researcher 배정) → RESEARCHING
backend monitor (3초)
  → ticket done → 담당 agent의 output comment 검증 → deep_review_artifacts 저장 → 다음 ticket
     research → "Analyze decision assumptions vs current state" (ANALYZING)
              → "Critically verify analysis" (CRITIQUING)
     Critic PASS                     → COMPLETED (final_verdict PASS)
     Critic REVISE (수정 0회)         → Analyst 수정 ticket (REVISING) → Critic 한 번 더
     Critic REVISE (수정 1회 후)      → HUMAN_REVIEW_REQUIRED
  → parent ticket in_review ("HUMAN REVIEW — Decision은 자동으로 변경되지 않았습니다")
실패 (Paperclip down, 검증 실패, timeout, Paperclip run 실패/budget 차단) → FAILED, 남은 ticket 취소, 재시도 가능
```

## Adapter 선택 (Paperclip 2026.1117.0)

| 후보 | 결과 |
| --- | --- |
| `hermes_gateway` | Hermes API server(`API_SERVER_KEY`, 포트)가 꺼져 있다 (`~/.hermes/config.yaml`의 `api_server`에 포트/키 없음). 켜려면 Hermes 설정 변경 필요 → 사용 안 함 |
| `hermes_local` | 실행 시 Paperclip skill을 사용자 전역 `~/.hermes/skills/`에 symlink로 설치한다 (Telegram/gateway가 쓰는 Hermes 설정 변경). 기존 `paperclip` skill symlink와 충돌해 실행이 실패했고, `--yolo`로 승인 없이 실행한다 → 사용 안 함 |
| `process` (선택) | 역할별 worker를 Paperclip이 실행. Researcher는 Review Memo와 **같은** Hermes 실행 경로(`hermes -z -t decision-debt,gbrain --ignore-rules`) + state.db tool audit를 재사용. `~/.hermes`를 바꾸지 않는다 |

- Researcher: Hermes oneshot, toolset은 Decision Debt MCP(SQLite read-only) + gbrain MCP(`--access read-only`)만.
  실행 후 Hermes session 기록으로 tool 경로를 감사: 허용 외 tool이 있거나 `list_review_due` / `get_decision_detail` / gbrain 조회가 없으면 실패.
- Analyst / Critic: tool이 없는 chat completion (요청에 `tools` 자체가 없음). 입력은 ticket 본문의 JSON뿐.
  OpenRouter `z-ai/glm-5.3`, `reasoning.effort=low`, `provider.sort=throughput` (정렬 없이 실행하면 reasoning이 긴 provider에 걸려 180초·$0.095, 정렬 후 7초·$0.005).
- worker는 자기 ticket만: run context로 ticket 확인 → atomic checkout (`expectedStatuses: todo, in_progress`) → output comment → status → cost event.
  실패도 comment로 보고하고 exit 0 (Paperclip의 transient retry로 LLM이 다시 호출되지 않게).

## JSON contract (`backend/schemas.py`, `services/deep_review_contract.py`)

reference 형식: `E<n>` (evidence 줄 — Review Memo와 같은 snapshot ID), `FACT:<key>` (backend Authoritative Facts), `DEC-...` (Decision Pulse에 실제로 있는 Decision)

- `DeepReviewResearch` — `decision_id, authoritative_facts, observed_state, evidence[{ref, source_id, source_title, text, relevance}], related_decisions[{decision_id, ref, relation, summary}], unknowns`
  - Hermes는 evidence **선택**(`ref`, `relevance`), 관련 Decision, unknowns만 쓴다. `authoritative_facts` / `observed_state` / evidence `text`는 backend snapshot 그대로이며, backend가 snapshot과 다르면 거부한다.
- `DeepReviewAnalysis` — `known_changes[{statement, evidence_refs≥1}]`, `implications[{statement, evidence_refs≥1, confidence}]`, `unknowns`, `recommended_checks[{check, reason}]`, `evidence_refs`
- `DeepReviewCritique` — `verdict: PASS | REVISE`, `unsupported_claims`, `contradictions[{claim, reason, evidence_ref}]`, `missing_checks`, `revision_instructions`
  - REVISE면 revision_instructions 1개 이상, unsupported/contradiction이 있으면 PASS 불가.
- 모든 모델 `extra="forbid"`: Keep/Revise/Supersede 같은 최종 판단 필드는 넣을 수 없다.
- 실패로 처리: JSON parse 실패, 필수 필드 누락, 잘못된 verdict, 없는 reference / 형식이 틀린 reference. 실패한 출력은 저장하지 않는다.

## Governance (`backend/scripts/setup_paperclip_org.py`, `paperclip/org.json`)

| 항목 | 값 |
| --- | --- |
| 보고선 | Evidence Researcher → Decision Analyst → Review Critic (`reportsTo`) |
| 실행 계기 | 사용자의 Run Deep Review → ticket 배정. heartbeat 타이머 끔, agent당 동시 실행 1 |
| revision loop | Analyst ↔ Critic 최대 1회 (backend 상수 `DEEP_REVIEW_MAX_REVISIONS = 1`) |
| budget (월, cents) | company 500 · Researcher 100 · Analyst 150 · Critic 150, hard stop on (80% 경고) |
| unpriced usage | `allow` — process run은 Paperclip에 가격 없는 receipt를 남긴다. 기본값(block)이면 그것만으로 company 전체가 정지됨. 실제 LLM 비용은 worker가 cost event로 보고 |
| timeout | process: Researcher 330s, Analyst/Critic 300s · 단계 대기 420s (backend) |
| HITL | Decision 수정·삭제, trigger 수정, 상태 변경, Keep/Revise/Supersede 판단은 agent에게 없음 |

## 데이터 (SQLite v7)

- `deep_review_runs`: decision_id, review_event_id, status, revision_count, final_verdict, input_snapshot_json, paperclip_json(company / project / goal / parent / stage ticket id), error, 시각. 결정당 진행 중 run 1개 (partial unique index)
- `deep_review_artifacts`: run_id, role, iteration, payload_json(검증 통과분만), paperclip issue / run id, meta_json(model, tokens, cost, tool audit)

## 운영

- Paperclip: `paperclip/` 프로젝트 전용 instance (npm `paperclipai@2026.1117.0`, Node ≥ 24.11, data `paperclip/data`, 127.0.0.1:3110, embedded PostgreSQL 127.0.0.1:54339, `local_trusted` + `private`).
  `./dev.sh`가 `paperclipai run`을 foreground 프로세스 그룹으로 띄우고 `./stop.sh`가 종료한다. launchd 서비스는 설치하지 않는다.
- ticket 접두어는 Paperclip이 company 이름 앞 3글자로 정한다(`DEC-7`). Decision ID(`DEC-2026-…`)와 비슷하지만, reference 검증은 SQLite에 있는 Decision만 인정한다.
- 처음 설정: `cd paperclip && npm install` → `npx paperclipai onboard -y --no-install-service -d "$PWD/data"` (config의 port 3110 / pg 54339 / telemetry off는 README 참고) → `cd ../backend && .venv/bin/python scripts/setup_paperclip_org.py --apply`
- Single vs Multi 비교: `backend/scripts/eval_deep_review.py` → `docs/evals/deep_review_eval.json`
