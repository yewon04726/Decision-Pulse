"""Deep Review (services/deep_review + contract + worker + API). 임시 DB + 가짜 Paperclip / 가짜 LLM만 쓴다.

실행 (backend 폴더에서): .venv/bin/python -m unittest tests.test_deep_review -v
"""

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import deep_review_worker as worker  # noqa: E402
from services import deep_review, review_memo  # noqa: E402
from services import deep_review_contract as contract  # noqa: E402
from services.paperclip_client import PaperclipUnavailable  # noqa: E402

PID = 1
DUE = "DEC-2026-0314-DB"
DUE2 = "DEC-2026-0321-CACHE"
ACTIVE = "DEC-ADR-001-STORAGE"
PROTECTED_TABLES = ("decisions", "decision_sources", "decision_review_state", "review_events", "candidates", "sources",
                    "decision_review_memos")
AGENTS = {"researcher": "agent-r", "analyst": "agent-a", "critic": "agent-c"}


def _obs(key, value):
    return {key: {"value": value, "unit": "users", "source": "sqlite:users", "observed_at": "2026-11-06T00:00:00+00:00"}}


def _protected_dump() -> dict:
    with sqlite3.connect(db.DB_PATH) as conn:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in PROTECTED_TABLES}


class FakePaperclip:
    """deep_review가 쓰는 Paperclip API만 흉내 낸다 (메모리)."""

    def __init__(self, down: bool = False):
        self.down = down
        self.issues: dict[str, dict] = {}
        self.comments: dict[str, list[dict]] = {}
        self.runs: list[dict] = []
        self.cancelled_runs: list[str] = []
        self.counter = 0

    def _check(self):
        if self.down:
            raise PaperclipUnavailable("Paperclip(http://127.0.0.1:3110/api)에 연결할 수 없습니다: ConnectError")

    def close(self):
        pass

    def health(self):
        self._check()
        return {"status": "ok"}

    def create_issue(self, company_id, body):
        self._check()
        self.counter += 1
        issue = {"id": str(uuid.uuid4()), "identifier": f"DEC-{self.counter}", "companyId": company_id, **body}
        self.issues[issue["id"]] = issue
        self.comments[issue["id"]] = []
        return issue

    def get_issue(self, issue_id):
        self._check()
        return dict(self.issues[issue_id])

    def patch_issue(self, issue_id, body):
        self._check()
        self.issues[issue_id].update({k: v for k, v in body.items() if k != "comment"})
        if body.get("comment"):
            self.comments[issue_id].append({"body": body["comment"], "authorAgentId": None})
        return self.issues[issue_id]

    def list_comments(self, issue_id):
        self._check()
        return list(self.comments[issue_id])

    def add_comment(self, issue_id, body):
        self.comments[issue_id].append({"body": body, "authorAgentId": None})

    def list_runs(self, company_id, limit=100):
        self._check()
        return list(self.runs)

    def cancel_run(self, run_id):
        self.cancelled_runs.append(run_id)

    # ---- 테스트 helper
    def open_issues(self, role=None):
        return [i for i in self.issues.values() if i.get("assigneeAgentId") and i["status"] not in ("done", "cancelled")
                and (role is None or i["assigneeAgentId"] == AGENTS[role])]

    def agent_reply(self, role, output=None, error=None, author=None):
        """배정된 agent가 실행한 것처럼: output comment + status"""
        [issue] = self.open_issues(role)
        inp = contract.parse_input(issue["description"])
        env = {"ok": error is None, "role": role, "iteration": inp["iteration"], "meta": {"paperclip_run_id": "run-x"}}
        if error is None:
            env["output"] = output(inp) if callable(output) else output
        else:
            env["error"] = error
        self.comments[issue["id"]].append({"body": contract.render_output_comment(env),
                                           "authorAgentId": author or AGENTS[role]})
        issue["status"] = "done" if error is None else "cancelled"
        return issue, inp


def findings_for(inp):
    ids = [e["id"] for e in inp["evidence_items"]][:2]
    return {"decision_id": inp["decision_id"], "confirmed_review_due": True,
            "evidence": [{"ref": i, "relevance": "재검토 조건과 같은 회의 발언"} for i in ids],
            "related_decisions": [{"decision_id": DUE2, "ref": "decisions/dec-2026-0321-cache", "relation": "indirect",
                                   "summary": "규모가 커지면 다시 보는 결정"}],
            "unknowns": ["threshold를 넘은 시점"]}


def research_for(inp):
    return contract.assemble_research(findings_for(inp), inp)


def analysis_for(inp, statement="user_count가 threshold를 넘은 상태가 관측됨"):
    research = inp["research"]
    ref = research["evidence"][0]["ref"]
    return {"decision_id": research["decision_id"],
            "known_changes": [{"statement": statement, "evidence_refs": ["FACT:observed", "FACT:raw_condition"]}],
            "implications": [{"statement": "현재 DB 선택의 전제를 다시 확인할 필요가 있을 수 있음", "evidence_refs": [ref],
                              "confidence": "medium"}],
            "unknowns": ["실제 부하"], "recommended_checks": [{"check": "쓰기 부하 확인", "reason": "관측값 없음"}],
            "evidence_refs": ["FACT:observed", ref]}


def critique(inp, verdict="PASS"):
    d = {"decision_id": inp["research"]["decision_id"], "verdict": verdict, "unsupported_claims": [], "contradictions": [],
         "missing_checks": ["백업 정책 확인"], "revision_instructions": []}
    if verdict == "REVISE":
        d["unsupported_claims"] = [{"claim": "x", "reason": "근거 없음"}]
        d["revision_instructions"] = ["근거 없는 주장 삭제"]
    return d


class DeepReviewBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls._orig = (db.DB_PATH, deep_review.ENABLED, deep_review.DEEP_REVIEW_ORG_FILE)
        db.DB_PATH = tmp / "test.db"
        db.READ_ONLY = False
        db.init_db()
        db.record_review_check(PID, DUE, "REVIEW_DUE", "REVIEW_DUE", _obs("user_count", 1400), None, None)
        db.record_review_check(PID, DUE2, "ACTIVE", "ACTIVE", _obs("search_api_p95_ms", 700), None, None)
        db.record_review_check(PID, DUE2, "REVIEW_DUE", "REVIEW_DUE", _obs("search_api_p95_ms", 850), None,
                               {"previous_status": "ACTIVE", "new_status": "REVIEW_DUE", "trigger": {"type": "metric"}})
        db.record_review_check(PID, ACTIVE, "ACTIVE", "ACTIVE", None, None, None)
        org = tmp / "org.json"
        org.write_text(json.dumps({"company_id": "co-1", "project_id": "pr-1", "goal_id": "go-1", "agents": AGENTS}))
        deep_review.DEEP_REVIEW_ORG_FILE = org
        deep_review.ENABLED = True

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH, deep_review.ENABLED, deep_review.DEEP_REVIEW_ORG_FILE = cls._orig
        cls._tmp.cleanup()

    def setUp(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM deep_review_artifacts")
            conn.execute("DELETE FROM deep_review_runs")
        self.pc = FakePaperclip()

    def run_to_end(self, decision_id=DUE, critic_verdicts=("PASS",)):
        run = deep_review.start(decision_id)
        run = deep_review.kickoff(run["id"], self.pc)
        self.assertEqual(run["status"], "RESEARCHING")
        self.pc.agent_reply("researcher", research_for)
        run = deep_review.advance(run, self.pc)
        verdicts = list(critic_verdicts)
        while run["status"] in db.DEEP_REVIEW_ACTIVE:
            if run["status"] in ("ANALYZING", "REVISING"):
                self.pc.agent_reply("analyst", analysis_for)
            elif run["status"] == "CRITIQUING":
                v = verdicts.pop(0)
                self.pc.agent_reply("critic", lambda inp, v=v: critique(inp, v))
            run = deep_review.advance(run, self.pc)
        return run


class ContractTest(DeepReviewBase):
    def snap(self):
        return deep_review.snapshot_for(DUE)[2]

    def test_snapshot_is_authoritative_record(self):
        snap = self.snap()
        decision = db.get_decision(DUE)
        self.assertEqual(snap["authoritative_facts"]["decision"], decision.decision)
        self.assertEqual(snap["authoritative_facts"]["assumptions"], decision.assumptions)
        self.assertEqual(snap["observed_state"]["review_status"], "REVIEW_DUE")
        self.assertEqual([e["id"] for e in snap["evidence_items"]],
                         [e["id"] for e in review_memo.evidence_items(DUE, [])])

    def test_research_schema_validation(self):
        snap = self.snap()
        research = contract.assemble_research(findings_for(snap), snap)
        self.assertEqual(research["evidence"][0]["text"], snap["evidence_items"][0]["text"])  # 원문은 snapshot에서
        bad = findings_for(snap) | {"evidence": [{"ref": "E999", "relevance": "x"}]}
        with self.assertRaisesRegex(contract.ContractError, "invalid evidence reference"):
            contract.assemble_research(bad, snap)
        missing = {k: v for k, v in findings_for(snap).items() if k != "unknowns"}
        with self.assertRaisesRegex(contract.ContractError, "unknowns"):
            contract.assemble_research(missing, snap)
        with self.assertRaisesRegex(contract.ContractError, "REVIEW_DUE"):
            contract.assemble_research(findings_for(snap) | {"confirmed_review_due": False}, snap)
        # 사실 부분을 바꾼 research는 backend 검증에서 거부
        tampered = json.loads(json.dumps(research))
        tampered["authoritative_facts"]["decision"] = "PostgreSQL로 전환한다"
        with self.assertRaisesRegex(contract.ContractError, "snapshot"):
            contract.validate_research(tampered, snap)
        # Decision Pulse에 없는 관련 Decision은 거부
        with self.assertRaisesRegex(contract.ContractError, "없는 Decision"):
            contract.validate_research(research, snap, decision_exists=lambda d: False)

    def test_json_parse_failure(self):
        with self.assertRaisesRegex(contract.ContractError, "JSON parse failure"):
            contract.extract_json("분석 결과: 문제 없음")
        with self.assertRaisesRegex(contract.ContractError, "JSON parse failure"):
            contract.extract_json('{"decision_id": "DEC-1",')
        self.assertEqual(contract.extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_analysis_schema_validation(self):
        snap = self.snap()
        inp = {"research": contract.assemble_research(findings_for(snap), snap)}
        ok = analysis_for(inp)
        self.assertEqual(contract.validate_analysis(ok, inp["research"])["decision_id"], DUE)
        no_refs = json.loads(json.dumps(ok)); no_refs["known_changes"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(contract.ContractError, "known_changes"):
            contract.validate_analysis(no_refs, inp["research"])
        unknown_ref = json.loads(json.dumps(ok)); unknown_ref["implications"][0]["evidence_refs"] = ["E77"]
        with self.assertRaisesRegex(contract.ContractError, "invalid evidence reference"):
            contract.validate_analysis(unknown_ref, inp["research"])
        bad_format = json.loads(json.dumps(ok)); bad_format["evidence_refs"] = ["회의록 3번째 줄"]
        with self.assertRaisesRegex(contract.ContractError, "schema validation"):
            contract.validate_analysis(bad_format, inp["research"])
        extra = ok | {"recommendation": "Supersede"}  # 최종 판단 필드는 contract에 없다
        with self.assertRaisesRegex(contract.ContractError, "schema validation"):
            contract.validate_analysis(extra, inp["research"])

    def test_critic_schema_validation(self):
        snap = self.snap()
        inp = {"research": contract.assemble_research(findings_for(snap), snap)}
        inp["analysis"] = analysis_for(inp)
        self.assertEqual(contract.validate_critique(critique(inp), inp["research"], inp["analysis"])["verdict"], "PASS")
        with self.assertRaisesRegex(contract.ContractError, "verdict"):
            contract.validate_critique(critique(inp) | {"verdict": "KEEP"}, inp["research"], inp["analysis"])
        with self.assertRaisesRegex(contract.ContractError, "revision_instructions"):
            contract.validate_critique(critique(inp) | {"verdict": "REVISE"}, inp["research"], inp["analysis"])
        with self.assertRaisesRegex(contract.ContractError, "PASS"):
            contract.validate_critique(critique(inp) | {"unsupported_claims": [{"claim": "a", "reason": "b"}]},
                                       inp["research"], inp["analysis"])
        bad_ref = critique(inp, "REVISE") | {"contradictions": [{"claim": "a", "reason": "b", "evidence_ref": "E55"}]}
        with self.assertRaisesRegex(contract.ContractError, "invalid evidence reference"):
            contract.validate_critique(bad_ref, inp["research"], inp["analysis"])


class FlowTest(DeepReviewBase):
    def test_start_creates_paperclip_lineage(self):
        run = deep_review.start(DUE)
        self.assertEqual(run["status"], "PENDING")
        run = deep_review.kickoff(run["id"], self.pc)
        self.assertEqual(run["status"], "RESEARCHING")
        parent = self.pc.issues[run["paperclip"]["parent_issue_id"]]
        self.assertEqual(parent["title"], f"Deep Review: {DUE}")
        self.assertNotIn("assigneeAgentId", parent)
        [research] = self.pc.open_issues()
        self.assertEqual(research["parentId"], parent["id"])
        self.assertEqual(research["assigneeAgentId"], AGENTS["researcher"])  # 역할당 한 agent
        inp = contract.parse_input(research["description"])
        self.assertEqual(inp["role"], "researcher")
        self.assertEqual(inp["authoritative_facts"], run["input_snapshot"]["authoritative_facts"])

    def test_non_review_due_rejected(self):
        with self.assertRaises(deep_review.DeepReviewError) as ctx:
            deep_review.start(ACTIVE)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_review_due"))
        with self.assertRaises(deep_review.DeepReviewError) as ctx:
            deep_review.start("DEC-NOPE")
        self.assertEqual(ctx.exception.status, 404)

    def test_duplicate_running_prevented(self):
        run = deep_review.start(DUE)
        with self.assertRaises(deep_review.DeepReviewError) as ctx:
            deep_review.start(DUE)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "deep_review_running"))
        self.assertEqual(ctx.exception.extra["run_id"], run["id"])
        with self.assertRaises(db.DeepReviewInProgress):  # DB 제약도 막는다 (동시 요청)
            db.create_deep_review_run(PID, DUE, None, None, {})
        deep_review.start(DUE2)  # 다른 결정은 가능

    def test_pass_flow(self):
        run = self.run_to_end(critic_verdicts=("PASS",))
        self.assertEqual((run["status"], run["final_verdict"], run["revision_count"]), ("COMPLETED", "PASS", 0))
        arts = db.list_deep_review_artifacts(run["id"])
        self.assertEqual([(a["role"], a["iteration"]) for a in arts], [("researcher", 0), ("analyst", 0), ("critic", 0)])
        parent = self.pc.issues[run["paperclip"]["parent_issue_id"]]
        self.assertEqual(parent["status"], "in_review")  # 사람에게 넘김
        view = deep_review.view(db.get_deep_review_run(run["id"]))
        self.assertEqual([s["state"] for s in view["stages"]], ["complete"] * 3)
        # Analyst ticket 입력은 검증된 research JSON뿐 (DB/gbrain 접근 정보 없음)
        analyst_issue = next(i for i in self.pc.issues.values() if i.get("assigneeAgentId") == AGENTS["analyst"])
        self.assertEqual(set(contract.parse_input(analyst_issue["description"])), {"role", "iteration", "research"})

    def test_revise_then_pass_retries_analyst_once(self):
        run = self.run_to_end(critic_verdicts=("REVISE", "PASS"))
        self.assertEqual((run["status"], run["final_verdict"], run["revision_count"]), ("COMPLETED", "PASS", 1))
        arts = [(a["role"], a["iteration"]) for a in db.list_deep_review_artifacts(run["id"])]
        self.assertEqual(arts, [("researcher", 0), ("analyst", 0), ("critic", 0), ("analyst", 1), ("critic", 1)])
        revision = next(i for i in self.pc.issues.values() if "(revision 1)" in i["title"] and i["assigneeAgentId"] == AGENTS["analyst"])
        inp = contract.parse_input(revision["description"])
        self.assertEqual(set(inp), {"role", "iteration", "research", "previous_analysis", "critique"})
        self.assertEqual(inp["critique"]["verdict"], "REVISE")

    def test_max_revision_terminates_human_review_required(self):
        run = self.run_to_end(critic_verdicts=("REVISE", "REVISE"))
        self.assertEqual((run["status"], run["final_verdict"], run["revision_count"]),
                         ("HUMAN_REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED", 1))
        analyst_issues = [i for i in self.pc.issues.values() if i.get("assigneeAgentId") == AGENTS["analyst"]]
        self.assertEqual(len(analyst_issues), 2)  # 첫 분석 + 수정 1회. 세 번째는 없다
        self.assertEqual(self.pc.open_issues(), [])

    def test_decision_unchanged_after_deep_review(self):
        before = _protected_dump()
        self.run_to_end(critic_verdicts=("REVISE", "REVISE"))
        self.run_to_end(DUE2, critic_verdicts=("PASS",))
        self.assertEqual(_protected_dump(), before)

    def test_project_isolation(self):
        with self.assertRaises(deep_review.DeepReviewError) as ctx:
            deep_review.start(DUE, project_id=999)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(db.list_deep_review_runs(DUE), [])
        run = deep_review.start(DUE, project_id=PID)
        self.assertEqual(run["project_id"], PID)


class FailureIsolationTest(DeepReviewBase):
    def test_paperclip_down_marks_failed(self):
        run = deep_review.start(DUE)
        run = deep_review.kickoff(run["id"], FakePaperclip(down=True))
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("연결할 수 없습니다", run["error"])
        # 재시도 가능 (FAILED는 진행 중이 아님)
        again = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        self.assertEqual(again["status"], "RESEARCHING")

    def test_paperclip_down_mid_run_waits_then_fails_on_timeout(self):
        run = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        self.pc.down = True
        self.assertEqual(deep_review.advance(run, self.pc)["status"], "RESEARCHING")  # 잠깐 끊김은 기다린다
        with db.connect() as conn:
            conn.execute("UPDATE deep_review_runs SET stage_started_at = '2000-01-01T00:00:00' WHERE id = ?", (run["id"],))
        run = deep_review.advance(db.get_deep_review_run(run["id"]), self.pc)
        self.assertEqual(run["status"], "FAILED")

    def test_agent_failure_and_invalid_output(self):
        run = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        self.pc.agent_reply("researcher", error="Hermes 실행 실패 (exit 1)")
        run = deep_review.advance(run, self.pc)
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("Evidence Researcher 실패", run["error"])
        self.assertEqual(self.pc.issues[run["paperclip"]["parent_issue_id"]]["status"], "cancelled")

        run = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        self.pc.agent_reply("researcher", research_for)
        run = deep_review.advance(run, self.pc)
        self.pc.agent_reply("analyst", {"decision_id": DUE, "known_changes": "많이 바뀜"})  # schema 위반
        run = deep_review.advance(run, self.pc)
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("schema validation failed", run["error"])
        self.assertEqual([a["role"] for a in db.list_deep_review_artifacts(run["id"])], ["researcher"])  # 위반 출력은 저장 안 함

    def test_output_from_other_author_ignored(self):
        run = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        self.pc.agent_reply("researcher", research_for, author="someone-else")
        run = deep_review.advance(run, self.pc)
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("결과 없이", run["error"])

    def test_stage_timeout_and_failed_paperclip_run(self):
        run = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        [issue] = self.pc.open_issues("researcher")
        self.pc.runs = [{"id": "r1", "issueId": issue["id"], "status": "failed", "error": "budget.blocked",
                         "createdAt": "2026-11-24T00:00:00Z"}]
        run = deep_review.advance(run, self.pc)
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("budget.blocked", run["error"])
        self.assertEqual(self.pc.issues[issue["id"]]["status"], "cancelled")

        self.pc.runs = []
        run = deep_review.kickoff(deep_review.start(DUE)["id"], self.pc)
        with db.connect() as conn:
            conn.execute("UPDATE deep_review_runs SET stage_started_at = '2000-01-01T00:00:00' WHERE id = ?", (run["id"],))
        run = deep_review.advance(db.get_deep_review_run(run["id"]), self.pc)
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("시간 초과", run["error"])

    def test_existing_features_work_while_paperclip_down(self):
        from fastapi.testclient import TestClient

        import app as app_module

        deep_review.kickoff(deep_review.start(DUE)["id"], FakePaperclip(down=True))
        client = TestClient(app_module.app)  # lifespan 없이 (monitor task 미실행)
        self.assertEqual(client.get(f"/api/decisions?project_id={PID}").status_code, 200)
        self.assertEqual(client.get(f"/api/decisions/{DUE}/review-memos").status_code, 200)
        runs = client.get(f"/api/decisions/{DUE}/deep-reviews").json()
        self.assertEqual(runs[0]["status"], "FAILED")
        self.assertEqual(review_memo.review_context(DUE)[0]["decision_id"], DUE)  # Review Memo 대상 확인 그대로


class ApiTest(DeepReviewBase):
    def test_api_start_list_get_and_disabled(self):
        from fastapi.testclient import TestClient

        import app as app_module

        client = TestClient(app_module.app)
        orig = deep_review.kickoff
        deep_review.kickoff = lambda run_id: None  # background에서 실제 Paperclip을 부르지 않게
        try:
            res = client.post(f"/api/decisions/{DUE}/deep-review?project_id={PID}")
            self.assertEqual(res.status_code, 202, res.text)
            run = res.json()
            self.assertEqual(run["status"], "PENDING")
            self.assertEqual(client.post(f"/api/decisions/{DUE}/deep-review").status_code, 409)
            self.assertEqual(client.post(f"/api/decisions/{ACTIVE}/deep-review").json()["detail"]["code"], "not_review_due")
            self.assertEqual(client.get(f"/api/deep-reviews/{run['id']}").json()["id"], run["id"])
            self.assertEqual(len(client.get(f"/api/decisions/{DUE}/deep-reviews").json()), 1)
            self.assertEqual(client.get("/api/deep-reviews/9999").status_code, 404)
            deep_review.ENABLED = False
            self.assertEqual(client.post(f"/api/decisions/{DUE2}/deep-review").status_code, 503)
            self.assertFalse(client.get("/api/deep-review/status").json()["enabled"])
        finally:
            deep_review.kickoff = orig
            deep_review.ENABLED = True


class WorkerTest(DeepReviewBase):
    def _inp(self):
        snap = deep_review.snapshot_for(DUE)[2]
        return {"role": "analyst", "iteration": 0, "research": contract.assemble_research(findings_for(snap), snap)}

    def _client(self, content, seen):
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "z-ai/glm-5.3", "choices": [{"finish_reason": "stop", "message": {
                "content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0012}})
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_analyst_has_no_tools_and_output_is_validated(self):
        orig = worker.OPENROUTER_API_KEY
        worker.OPENROUTER_API_KEY = "sk-test-not-real"
        try:
            inp = self._inp()
            seen: list = []
            out, meta = worker.run_analyst(inp, self._client(json.dumps(analysis_for(inp)), seen))
            self.assertNotIn("tools", seen[0])  # tool 자체를 주지 않는다
            self.assertEqual(seen[0]["response_format"]["type"], "json_schema")
            self.assertIn("Decision Analyst", seen[0]["messages"][0]["content"])  # 역할 instruction 파일
            user = seen[0]["messages"][1]["content"]
            self.assertIn('"research"', user)
            self.assertEqual(out["decision_id"], DUE)
            self.assertEqual(meta["cost_usd"], 0.0012)
            with self.assertRaises(contract.ContractError):  # JSON이 아닌 응답 → 실패 (저장 안 됨)
                worker.run_analyst(inp, self._client("검토했습니다. 문제 없습니다.", []))
            inp_c = {**inp, "role": "critic", "analysis": analysis_for(inp)}
            out_c, _ = worker.run_critic(inp_c, self._client(json.dumps(critique(inp_c)), []))
            self.assertEqual(out_c["verdict"], "PASS")
        finally:
            worker.OPENROUTER_API_KEY = orig

    def test_cost_event_and_instruction_files(self):
        ev = worker._cost_event("co", "ag", "is", "run", "analyst", {"cost_usd": 0.0012, "model": "m", "provider": "openrouter"})
        self.assertEqual(ev["costCents"], 1)
        self.assertIsNone(worker._cost_event("co", "ag", "is", "run", "analyst", {}))
        for role in worker.ROLES:
            text = worker.instructions(role)
            for heading in ("## 역할", "## 입력", "## 출력 JSON", "## 허용 도구", "## 금지"):
                self.assertIn(heading, text, f"{role}: {heading}")
        self.assertEqual(worker.RESEARCH_TOOLSETS, ("decision-debt", "gbrain"))


if __name__ == "__main__":
    unittest.main()
