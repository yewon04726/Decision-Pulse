"""Paperclip 조직 구성 (멱등): Decision Pulse Deep Review company + goal + project + agent 3명.

    cd backend && .venv/bin/python scripts/setup_paperclip_org.py            # 계획(dry-run)만 출력
    cd backend && .venv/bin/python scripts/setup_paperclip_org.py --apply    # 생성/갱신 후 paperclip/org.json 저장

- Paperclip은 이 프로젝트 전용 instance (paperclip/, 127.0.0.1:3110, local_trusted + private). dev.sh가 띄운다.
- 이미 있는 company / agent는 이름으로 찾아 설정만 맞춘다 (삭제하지 않음).
- governance를 agent 실행 전에 설정한다:
    heartbeat 타이머 끔 (배정될 때만 실행) · agent당 동시 실행 1 · 월 budget(cents) 상한 · 보고선 Researcher → Analyst → Critic
- org.json에는 id와 governance 값만 저장한다 (비밀값 없음).
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DEEP_REVIEW_ORG_FILE, PAPERCLIP_API_URL  # noqa: E402
from services.paperclip_client import PaperclipClient  # noqa: E402

COMPANY = {
    "name": "Decision Pulse Deep Review",
    "description": ("Review due technical decisions using evidence-grounded, cross-validated analysis "
                    "while preserving human final authority."),
}
# ticket 접두어는 Paperclip이 company 이름 앞 3글자로 정한다 (DEC). Decision ID(DEC-2026-…)와 비슷하지만
# reference 검증은 SQLite에 실제로 있는 Decision ID만 인정하므로 ticket 번호(DEC-3)가 Decision으로 통과하지 않는다.
GOAL_TITLE = "Review due technical decisions with evidence-grounded, cross-validated analysis"
PROJECT_NAME = "Deep Review"
INSTRUCTIONS = ROOT.parent / "agents" / "deep-review"
PYTHON = ROOT / ".venv" / "bin" / "python"
WORKER = ROOT / "deep_review_worker.py"

# 소액 실습용 월 budget (cents). Researcher < Analyst <= Critic. company 상한은 합계보다 약간 크게
COMPANY_BUDGET_CENTS = 500
AGENTS = [
    {"key": "researcher", "name": "Evidence Researcher", "role": "researcher", "title": "Evidence Researcher",
     "reportsTo": None, "budget": 100, "timeoutSec": 330,
     "capabilities": "Collects authoritative facts, evidence and related past decisions via read-only Decision Debt MCP + gbrain MCP (Hermes). No interpretation."},
    {"key": "analyst", "name": "Decision Analyst", "role": "general", "title": "Decision Analyst",
     "reportsTo": "researcher", "budget": 150, "timeoutSec": 300,
     "capabilities": "Compares decision, rationale and assumptions with the current state using only the Researcher JSON. No tools."},
    {"key": "critic", "name": "Review Critic", "role": "qa", "title": "Review Critic",
     "reportsTo": "analyst", "budget": 150, "timeoutSec": 300,
     "capabilities": "Cross-validates analysis against evidence: unsupported claims, contradictions, missing checks. Verdict PASS | REVISE. No tools."},
]
RUNTIME = {"heartbeat": {"enabled": False, "maxConcurrentRuns": 1}}


def agent_body(spec: dict, ids: dict) -> dict:
    return {
        "name": spec["name"], "role": spec["role"], "title": spec["title"], "capabilities": spec["capabilities"],
        "reportsTo": ids.get(spec["reportsTo"]) if spec["reportsTo"] else None,
        "adapterType": "process",
        "adapterConfig": {"command": str(PYTHON), "args": [str(WORKER), spec["key"]], "cwd": str(ROOT),
                          "timeoutSec": spec["timeoutSec"], "graceSec": 10},
        "runtimeConfig": RUNTIME,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    governance = {
        "company_budget_cents_per_month": COMPANY_BUDGET_CENTS,
        "budget_hard_stop": "on (warn at 80%) — reported LLM cost events; unpriced process receipts allowed",
        "agent_budget_cents_per_month": {a["key"]: a["budget"] for a in AGENTS},
        "heartbeat_timer": "disabled (wake on assignment only)",
        "max_concurrent_runs_per_agent": 1,
        "max_revision_loops": 1,
        "process_timeout_sec": {a["key"]: a["timeoutSec"] for a in AGENTS},
        "reporting_line": "Evidence Researcher → Decision Analyst → Review Critic",
        "human_authority": "Decision 수정/삭제, trigger 수정, 상태 변경, Keep/Revise/Supersede 판단은 agent에게 주지 않는다",
    }
    print(f"Paperclip: {PAPERCLIP_API_URL}")
    print("governance:", json.dumps(governance, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run: --apply로 실제 생성/갱신")
        return

    with PaperclipClient(PAPERCLIP_API_URL) as pc:
        pc.health()
        company = next((c for c in pc.list_companies() if c["name"] == COMPANY["name"]), None)
        if company is None:
            company = pc.create_company({**COMPANY, "budgetMonthlyCents": COMPANY_BUDGET_CENTS})
            print(f"company created: {company['id']} ({company.get('issuePrefix')})")
        else:
            pc.patch_company(company["id"], {"description": COMPANY["description"]})
            print(f"company exists: {company['id']} ({company.get('issuePrefix')})")
        cid = company["id"]
        pc.set_company_budget(cid, COMPANY_BUDGET_CENTS)

        goal = next((g for g in pc.list_goals(cid) if g["title"] == GOAL_TITLE), None)
        if goal is None:
            goal = pc.create_goal(cid, {"title": GOAL_TITLE, "description": COMPANY["description"], "level": "company",
                                        "status": "active"})
        project = next((p for p in pc.list_projects(cid) if p["name"] == PROJECT_NAME), None)
        if project is None:
            project = pc.create_project(cid, {"name": PROJECT_NAME, "status": "in_progress", "goalIds": [goal["id"]],
                                              "description": "REVIEW_DUE Decision 한 건 = parent ticket 1개 + 역할별 하위 ticket"})

        existing = {a["name"]: a for a in pc.list_agents(cid)}
        ids: dict[str, str] = {}
        for spec in AGENTS:  # 순서대로: reportsTo 대상이 먼저 만들어진다
            md = (INSTRUCTIONS / f"{spec['key']}.md").read_text(encoding="utf-8")
            body = agent_body(spec, ids)
            agent = existing.get(spec["name"])
            if agent is None:
                agent = pc.create_agent(cid, {**body, "instructionsBundle": {"files": {"AGENTS.md": md}},
                                              "budgetMonthlyCents": spec["budget"]})
                print(f"agent created: {spec['name']} {agent['id']}")
            else:
                pc.patch_agent(agent["id"], {**body, "replaceAdapterConfig": True})
                print(f"agent updated: {spec['name']} {agent['id']}")
            pc.set_agent_budget(agent["id"], spec["budget"])
            ids[spec["key"]] = agent["id"]

        # budget policy: 실제 LLM 비용은 worker가 cost event로 보고한다 (OpenRouter usage.cost / Hermes estimated_cost_usd).
        # process adapter run 자체는 Paperclip에 '가격 없음(unknown billing)' receipt를 남기는데, 기본값(block)이면 그것만으로
        # hard stop이 걸린다 → unpriced는 allow, 보고된 비용에는 hard stop 유지 (80%에서 경고)
        scopes = [("company", cid, COMPANY_BUDGET_CENTS)] + [("agent", ids[a["key"]], a["budget"]) for a in AGENTS]
        for scope_type, scope_id, amount in scopes:
            pc.upsert_budget_policy(cid, {"scopeType": scope_type, "scopeId": scope_id, "amount": amount,
                                          "hardStopEnabled": True, "warnPercent": 80, "unpricedUsagePolicy": "allow",
                                          "isActive": True})
        amounts = {scope_id: amount for _, scope_id, amount in scopes}
        for incident in pc.budget_overview(cid).get("activeIncidents", []):
            # 이전 설정(unpriced=block)으로 생긴 정지만 해제한다. 보고된 비용이 상한을 넘은 경우는 그대로 둔다
            policy = next((p for p in pc.budget_overview(cid)["policies"] if p["scopeId"] == incident.get("scopeId")), None)
            if policy and policy["observedAmount"] < amounts.get(policy["scopeId"], 0):
                pc.resolve_budget_incident(cid, incident["id"], {"action": "raise_budget_and_resume",
                                                                 "amount": amounts[policy["scopeId"]],
                                                                 "decisionNote": "unpriced usage policy → allow (reported costs only)"})
                print(f"budget incident resolved: {incident.get('scopeName')}")

        org = {
            "company_id": cid, "company_name": company["name"], "issue_prefix": company.get("issuePrefix"),
            "goal_id": goal["id"], "project_id": project["id"], "agents": ids, "governance": governance,
            "paperclip_api_url": PAPERCLIP_API_URL, "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    DEEP_REVIEW_ORG_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEEP_REVIEW_ORG_FILE.write_text(json.dumps(org, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved: {DEEP_REVIEW_ORG_FILE}")


if __name__ == "__main__":
    main()
