"""Single-Agent Review Memo vs Multi-Agent Deep Review 비교 (작은 평가 harness).

    cd backend && .venv/bin/python scripts/eval_deep_review.py                       # 저장된 결과만 비교 (LLM 호출 없음)
    cd backend && .venv/bin/python scripts/eval_deep_review.py --run DEC-2026-0314-DB # 해당 결정의 Deep Review를 먼저 실행(API, 유료 소액)

대상: 완료된 Review Memo(v4)와 완료된 Deep Review가 모두 있는 결정. 각 결정의 최신 결과끼리 비교한다.
결과: docs/evals/deep_review_eval.json

자동 지표 (저장된 결과에서 계산)
- evidence_reference_coverage : 인용한 evidence ID 수 / snapshot evidence 수
- unsupported_claim_count     : Critic이 찾은 unsupported claim + contradiction (Single은 검증자가 없어 측정 불가 → null)
- missing_info_items          : 확인되지 않은 정보 + 사람이 확인할 항목 수
- latency_seconds             : 요청 ~ 완료
- cost_usd                    : 보고된 LLM 비용 합 (Single memo는 저장하지 않으므로 null)
자동으로 숫자를 만들기 어려운 항목은 human_rubric에 비워 둔다 (사람이 채움).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from services.review_memo import _section  # noqa: E402

OUT = ROOT.parent / "docs" / "evals" / "deep_review_eval.json"
API = "http://127.0.0.1:8001/api"
HUMAN_RUBRIC = {
    "factual_accuracy_1to5": None,
    "fact_vs_inference_separation_1to5": None,
    "actionability_of_checks_1to5": None,
    "overstatement_found": None,
    "notes": "",
}


def _bullets(text: str) -> int:
    return sum(1 for line in text.splitlines() if re.match(r"\s*[-*]\s+\S", line) and "없음" not in line)


def _seconds(start: str | None, end: str | None) -> float | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def single_metrics(memo) -> dict:
    items = (memo.source_snapshot or {}).get("evidence_items") or []
    content = memo.content or ""
    cited = set(re.findall(r"\bE\d+\b", content))
    missing = _bullets(_section(content, "### 아직 확인되지 않은 정보")) + _bullets(_section(content, "### 사람이 추가로 확인할 사항"))
    audit = memo.tool_audit or {}
    return {
        "kind": "single_agent_review_memo", "id": memo.id, "prompt_version": memo.prompt_version, "model": memo.model,
        "evidence_total": len(items), "evidence_cited": len(cited & {i["id"] for i in items}),
        "evidence_reference_coverage": round(len(cited & {i["id"] for i in items}) / len(items), 2) if items else None,
        "unsupported_claim_count": None, "unsupported_claim_note": "검증 단계 없음 (자동 측정 불가)",
        "missing_info_items": missing,
        "latency_seconds": audit.get("elapsed_seconds") or _seconds(memo.created_at, memo.generated_at),
        "cost_usd": None, "llm_calls": "1 Hermes session",
        "tools_used": audit.get("tools_used"),
    }


def multi_metrics(run: dict) -> dict:
    arts = db.list_deep_review_artifacts(run["id"])
    research = next(a["payload"] for a in arts if a["role"] == "researcher")
    analysis = max((a for a in arts if a["role"] == "analyst"), key=lambda a: a["iteration"])["payload"]
    critique = max((a for a in arts if a["role"] == "critic"), key=lambda a: a["iteration"])["payload"]
    items = (run["input_snapshot"] or {}).get("evidence_items") or []
    refs = set(analysis["evidence_refs"]) | {r for c in analysis["known_changes"] + analysis["implications"] for r in c["evidence_refs"]}
    refs |= {e["ref"] for e in research["evidence"]}
    cited = {r for r in refs if re.fullmatch(r"E\d+", r)} & {i["id"] for i in items}
    costs = [a["meta"].get("cost_usd") for a in arts if a.get("meta") and isinstance(a["meta"].get("cost_usd"), (int, float))]
    return {
        "kind": "multi_agent_deep_review", "id": run["id"], "status": run["status"], "final_verdict": run["final_verdict"],
        "revision_count": run["revision_count"], "paperclip_parent": (run["paperclip"] or {}).get("parent_identifier"),
        "evidence_total": len(items), "evidence_cited": len(cited),
        "evidence_reference_coverage": round(len(cited) / len(items), 2) if items else None,
        "fact_refs_used": sorted(r for r in refs if r.startswith("FACT:")),
        "related_decisions": [d["decision_id"] for d in research["related_decisions"]],
        "unsupported_claim_count": len(critique["unsupported_claims"]) + len(critique["contradictions"]),
        "critic_missing_checks": len(critique["missing_checks"]),
        "missing_info_items": len(set(research["unknowns"]) | set(analysis["unknowns"])) + len(analysis["recommended_checks"]),
        "known_changes": len(analysis["known_changes"]), "implications": len(analysis["implications"]),
        "latency_seconds": _seconds(run["created_at"], run["completed_at"]),
        "cost_usd": round(sum(costs), 6) if costs else None, "llm_calls": len(arts),
    }


def run_deep_review(decision_id: str) -> None:
    res = httpx.post(f"{API}/decisions/{decision_id}/deep-review", timeout=30)
    print(f"start {decision_id}: HTTP {res.status_code}")
    if res.status_code not in (200, 202):
        print(res.text[:300])
        return
    run_id = res.json()["id"]
    for _ in range(120):
        time.sleep(5)
        run = httpx.get(f"{API}/deep-reviews/{run_id}", timeout=10).json()
        if run["status"] in ("COMPLETED", "HUMAN_REVIEW_REQUIRED", "FAILED"):
            print(f"  run #{run_id}: {run['status']} {run.get('error') or ''}")
            return
    print(f"  run #{run_id}: 아직 진행 중 (나중에 다시 실행)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", nargs="*", default=[], help="먼저 Deep Review를 실행할 Decision ID")
    args = parser.parse_args()
    for decision_id in args.run:
        run_deep_review(decision_id)

    with db.connect() as conn:
        ids = [r[0] for r in conn.execute(
            """SELECT DISTINCT m.decision_id FROM decision_review_memos m JOIN deep_review_runs r ON r.decision_id = m.decision_id
               WHERE m.status = 'completed' AND m.prompt_version = 'v4' AND r.status IN ('COMPLETED', 'HUMAN_REVIEW_REQUIRED')
               ORDER BY m.decision_id""")]
    cases = []
    for decision_id in ids:
        memo = next(m for m in db.list_review_memos(decision_id) if m.status == "completed" and m.prompt_version == "v4")
        run = next(r for r in db.list_deep_review_runs(decision_id) if r["status"] in ("COMPLETED", "HUMAN_REVIEW_REQUIRED"))
        cases.append({"decision_id": decision_id, "single": single_metrics(memo), "multi": multi_metrics(run),
                      "human_rubric": {"single": dict(HUMAN_RUBRIC), "multi": dict(HUMAN_RUBRIC)}})

    all_runs = []
    with db.connect() as conn:
        for r in conn.execute("SELECT status, COUNT(*) FROM deep_review_runs GROUP BY status"):
            all_runs.append({"status": r[0], "count": r[1]})
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": __doc__.strip().splitlines()[0],
        "metrics": ["evidence_reference_coverage", "unsupported_claim_count", "missing_info_items", "latency_seconds", "cost_usd"],
        "deep_review_runs_by_status": all_runs,
        "cases": cases,
        "notes": [
            "Single memo의 비용은 저장하지 않아 null. Deep Review 비용은 OpenRouter usage.cost(Analyst/Critic) + Hermes estimated_cost_usd(Researcher).",
            "Single memo에는 검증 단계가 없으므로 unsupported claim을 자동으로 세지 않는다 (human rubric).",
            "같은 결정이라도 실행 시점이 달라 관측값/관련 결정 목록이 다를 수 있다.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'decision':<26} {'kind':<7} {'coverage':>8} {'unsupported':>11} {'missing':>7} {'latency':>8} {'cost$':>8}")
    for c in cases:
        for k in ("single", "multi"):
            m = c[k]
            print(f"{c['decision_id']:<26} {k:<7} {str(m['evidence_reference_coverage']):>8} {str(m['unsupported_claim_count']):>11} "
                  f"{m['missing_info_items']:>7} {str(m['latency_seconds']):>8} {str(m['cost_usd']):>8}")
    print(f"saved: {OUT}")


if __name__ == "__main__":
    main()
