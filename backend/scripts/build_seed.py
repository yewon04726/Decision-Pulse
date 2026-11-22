"""Dify Knowledge v4 원본 문서(~/Downloads/decision_debt_one_decision_per_chunk)로 seed_data.json 생성.

사용: python scripts/build_seed.py [문서 폴더]
- sources.content 는 원문 그대로, evidence 는 원문의 '재검토 조건' 문장 그대로 사용한다.
- 문서에 없는 날짜/이력/승인 기록은 만들지 않는다.
- 재검토 조건 중 check_metric 지표로 표현 가능한 것만 metric 트리거, 나머지는 manual.
- project_id는 넣지 않는다. db.init_db()가 기본 프로젝트("Decision Debt MVP")에 적재한다.
"""

import json
import sys
from pathlib import Path

DOCS_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "~/Downloads/decision_debt_one_decision_per_chunk").expanduser()
OUT = Path(__file__).resolve().parent.parent / "seed_data.json"

# Dify Knowledge에 업로드된 문서명과 로컬 파일명이 다른 경우 (Agent Evidence의 문서명과 맞춘다)
DIFY_NAME = {"meeting_03_auth.md": "meeting_03_auth_v2.md"}

SOURCE_DATES = {
    "meeting_01_database": "2026-03-14",
    "meeting_02_cache": "2026-03-21",
    "meeting_03_auth_v2": "2026-03-28",
    "meeting_04_logging": "2026-04-04",
    "meeting_05_deploy": "2026-04-11",
    "meeting_06_monitoring": "2026-04-18",
}  # 문서 본문의 날짜. issue/ADR 문서에는 날짜가 없다.


def parse(text: str) -> dict[str, str]:
    """'키: 값 | 키: 값' 형식 → dict"""
    fields = {}
    for part in text.strip().split(" | "):
        if ": " in part:
            k, v = part.split(": ", 1)
            fields[k.strip()] = v.strip()
    return fields


def load_sources() -> dict[str, dict]:
    sources = {}
    for path in sorted(DOCS_DIR.glob("*.md")):
        title = DIFY_NAME.get(path.name, path.name)
        sid = Path(title).stem
        content = path.read_text(encoding="utf-8").strip()
        fields = parse(content)
        sources[sid] = {
            "id": sid,
            "title": title,
            "type": sid.split("_")[0],  # meeting / issue / adr
            "date": SOURCE_DATES.get(sid),
            "summary": fields.get("문서", title),
            "content": content,
            "human_authored": True,  # 사람이 작성한 원본 문서
            "_fields": fields,
        }
    return sources


# 결정 ↔ 문서 매핑과 트리거 구조화. 문장(결정/이유/조건)은 원문에서 가져온다.
DECISIONS = [
    {
        "id": "DEC-2026-0314-DB",
        "topic": "database",
        "title": "SQLite",
        "primary": "meeting_01_database",
        "related": ["issue_01_database_scale"],
        "trigger": {"type": "metric", "metric": "user_count", "operator": ">", "threshold": 1000},
    },
    {
        "id": "DEC-2026-0321-CACHE",
        "topic": "cache",
        "title": "내부 캐시 (Redis 미도입)",
        "primary": "meeting_02_cache",
        "related": ["issue_02_redis"],
        "trigger": {
            "type": "metric",
            "metric": "search_api_p95_ms",
            "operator": ">",
            "threshold": 800,
            "note": "원문 조건은 '3일 이상 지속'이지만 check_metric은 현재값만 제공하므로 지속 기간은 사람이 확인해야 한다.",
        },
    },
    {
        "id": "DEC-2026-0328-AUTH",
        "topic": "auth",
        "title": "Google · GitHub OAuth",
        "primary": "meeting_03_auth_v2",
        "related": [],
        "trigger": {"type": "manual"},
    },
    {
        "id": "DEC-2026-0404-LOG",
        "topic": "logging",
        "title": "stdout + Docker 로그",
        "primary": "meeting_04_logging",
        "related": [],
        "trigger": {"type": "manual"},
    },
    {
        "id": "DEC-2026-0411-DEPLOY",
        "topic": "deploy",
        "title": "단일 환경 수동 배포",
        "primary": "meeting_05_deploy",
        "related": [],
        "trigger": {"type": "manual"},
    },
    {
        "id": "DEC-2026-0418-MON",
        "topic": "monitoring",
        "title": "CPU · 메모리 · API 오류율",
        "primary": "meeting_06_monitoring",
        "related": [],
        "trigger": {"type": "manual"},
    },
    {
        "id": "DEC-ADR-001-STORAGE",
        "topic": "storage",
        "title": "로컬 디스크 저장",
        "primary": "adr_01_storage",
        "related": [],
        "trigger": {
            "type": "metric",
            "metric": "server_count",
            "operator": ">=",
            "threshold": 2,
            "note": "원문 '운영 서버가 2대 이상으로 확장되면'을 server_count >= 2 로 해석했다.",
        },
    },
    {
        "id": "DEC-ADR-002-API",
        "topic": "api",
        "title": "/api/v1 고정",
        "primary": "adr_02_api_version",
        "related": [],
        "trigger": {"type": "manual"},
    },
]

NOTE_KEYS = ["전제", "영향", "비고", "후보", "후보 지표"]


def build():
    sources = load_sources()
    decisions = []
    for spec in DECISIONS:
        f = sources[spec["primary"]]["_fields"]
        condition = f["재검토 조건"]
        trigger = {**spec["trigger"], "description": condition}
        evidence = [{"source_id": spec["primary"], "quote": condition}]
        for rid in spec["related"]:
            rf = sources[rid]["_fields"]
            quote = rf.get("기존 재검토 조건") or rf.get("재검토 조건")
            evidence.append({"source_id": rid, "quote": quote})
            if rf.get("판단"):
                evidence.append({"source_id": rid, "quote": rf["판단"]})
        history = [
            {
                "date": sources[sid]["date"],
                "actor": sources[sid]["title"],
                "action": "결정 기록" if sid == spec["primary"] else "재검토 상태 점검",
                "note": sources[sid]["summary"],
            }
            for sid in [spec["primary"], *spec["related"]]
        ]
        decisions.append(
            {
                "id": spec["id"],
                "topic": spec["topic"],
                "title": spec["title"],
                "decision": f["결정"],
                "rationale": f["이유"],
                "assumptions": [f["전제"]] if "전제" in f else [],
                "notes": [{"label": k, "text": f[k]} for k in NOTE_KEYS if k in f and k != "전제"],
                "review_trigger": trigger,
                "status": "active",
                "origin": "seed_document",
                # human_verified = Review Queue에서 사람이 Candidate를 검토·승인했는지. seed는 그 과정을 거치지 않았다.
                "human_verified": False,
                "evidence": evidence,
                "superseded_by": None,
                "created_at": sources[spec["primary"]]["date"],
                "updated_at": sources[spec["primary"]]["date"],
                "history": history,
            }
        )
    out = {
        "sources": [{k: v for k, v in s.items() if k != "_fields"} for s in sources.values()],
        "decisions": decisions,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(out['sources'])} sources, {len(decisions)} decisions → {OUT}")


if __name__ == "__main__":
    build()
