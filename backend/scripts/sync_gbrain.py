"""승인된 Decision을 gbrain에 수동으로 (재)동기화한다. gbrain이 꺼져 있었거나 실패한 경우에 쓴다.

사용 (backend 폴더에서):
  .venv/bin/python scripts/sync_gbrain.py --status                          # 동기화 상태
  .venv/bin/python scripts/sync_gbrain.py --decision DEC-ID --dry-run       # Markdown만 출력 (파일/gbrain 변경 없음)
  .venv/bin/python scripts/sync_gbrain.py --decision DEC-ID [--decision DEC-ID2]
  .venv/bin/python scripts/sync_gbrain.py --all-verified                    # human_verified=true 전체 (seed 제외)
  --force: 내용이 같아도 다시 sync (gbrain import가 아니라 같은 delegated `gbrain sync`를 실행)
backend 승인 경로와 같은 services.gbrain_sync를 쓴다. LLM을 호출하지 않는다 (embedding은 gbrain sync가 담당).
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from services import gbrain_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", action="append", default=[], help="Decision ID (반복 가능)")
    parser.add_argument("--all-verified", action="store_true", help="human_verified=true인 Decision 전체")
    parser.add_argument("--dry-run", action="store_true", help="Markdown만 출력")
    parser.add_argument("--force", action="store_true", help="내용이 같아도 다시 sync")
    parser.add_argument("--status", action="store_true", help="동기화 상태 출력")
    args = parser.parse_args()
    db.init_db()

    if args.status:
        for r in db.list_knowledge_sync(gbrain_sync.TARGET):
            print(f"{r['decision_id']}\t{r['status']}\tsynced_at={r['synced_at']}\t{(r['error'] or '')[:120]}")
        return 0

    if args.all_verified:
        decisions = [d for p in db.list_projects() for d in db.list_decisions(p.id) if gbrain_sync.is_exportable(d)]
    else:
        decisions = []
        for did in dict.fromkeys(args.decision):
            d = db.get_decision(did)
            if d is None:
                print(f"Decision not found: {did}", file=sys.stderr)
                return 1
            decisions.append(d)
    if not decisions:
        parser.error("--decision, --all-verified, --status 중 하나가 필요합니다")

    failed = 0
    for d in decisions:
        if args.dry_run:
            print(f"===== {gbrain_sync.markdown_filename(d.id)} (exportable={gbrain_sync.is_exportable(d)})")
            print(gbrain_sync.decision_to_gbrain_markdown(d, db.get_project(d.project_id), {s.id: s for s in db.list_sources(d.project_id)}))
            continue
        r = asyncio.run(gbrain_sync.sync_decision_to_gbrain(d, force=args.force))
        failed += r.status == "failed"
        print(f"{d.id}\t{r.status}\t{r.message}\t{r.path or ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
