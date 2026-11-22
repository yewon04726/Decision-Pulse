"""승인된 Decision 1건을 Dify Knowledge에 수동으로 동기화한다.

사용 (backend 폴더에서):
  .venv/bin/python scripts/sync_decision_to_dify.py --list                 # human_verified=true 결정 목록
  .venv/bin/python scripts/sync_decision_to_dify.py DEC-ID --dry-run       # Dify 호출 없이 문서 이름/본문 확인
  .venv/bin/python scripts/sync_decision_to_dify.py DEC-ID                 # 실제 문서 생성 (같은 이름 문서가 있으면 생성 안 함)
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from services.knowledge_sync import (  # noqa: E402
    KnowledgeSyncError,
    decision_to_rag_text,
    dify_document_name,
    sync_decision_to_dify,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decision_id", nargs="?")
    parser.add_argument("--dry-run", action="store_true", help="Dify를 호출하지 않고 문서 이름과 본문만 출력")
    parser.add_argument("--list", action="store_true", help="동기화 가능한(human_verified=true) 결정 목록")
    args = parser.parse_args()

    if args.list:
        for project in db.list_projects():
            for d in db.list_decisions(project.id):
                if d.human_verified:
                    print(f"{d.id}\t[{project.name}] {d.title}")
        return 0
    if not args.decision_id:
        parser.error("decision_id 또는 --list 가 필요합니다")

    decision = db.get_decision(args.decision_id)
    if decision is None:
        print(f"Decision not found: {args.decision_id}", file=sys.stderr)
        return 1
    sources = {s.id: s for s in db.list_sources(decision.project_id)}

    if args.dry_run:
        print(f"document name: {dify_document_name(decision)}")
        print(f"human_verified: {decision.human_verified}\n")
        print(decision_to_rag_text(decision, sources))
        return 0

    try:
        result = asyncio.run(sync_decision_to_dify(decision, sources))
    except KnowledgeSyncError as e:
        print(f"동기화 실패: {e}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
