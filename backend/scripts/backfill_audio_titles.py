"""기존 오디오 Source 제목을 저장된 Candidate topic으로 한 번 갱신한다 (추출 후 자동 갱신과 같은 services.source_title 사용).

사용 (backend 폴더에서):
  .venv/bin/python scripts/backfill_audio_titles.py           # 미리 보기 (DB 변경 없음)
  .venv/bin/python scripts/backfill_audio_titles.py --apply   # DB 백업 후 적용
Source 내용/transcript/Candidate는 바꾸지 않는다. 사람이 정한 제목(title_origin=user)은 건너뛴다. LLM 호출 없음.
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from services import source_title  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="DB 백업 후 실제로 적용")
    args = parser.parse_args()
    db.init_db()

    sources = [s for p in db.list_projects() for s in db.list_sources(p.id) if s.audio is not None]
    if args.apply:
        backup = db.DB_PATH.with_name(f"{db.DB_PATH.stem}.before-audio-titles-{datetime.now():%Y%m%d-%H%M%S}{db.DB_PATH.suffix}")
        shutil.copy2(db.DB_PATH, backup)
        print(f"backup: {backup}")
    for s in sources:
        topics = db.list_source_candidate_topics(s.id)
        planned = source_title.title_from_topics(topics)
        if s.audio.title_origin == "user":
            result = "skip (user title)"
        elif planned is None:
            result = "skip (no candidates)"
        elif planned == s.title:
            result = "unchanged"
        else:
            result = f"→ {planned}" + ("" if args.apply and source_title.update_audio_source_title(s.id) else " (dry-run)" if not args.apply else " (not applied)")
        print(f"{s.id}\t{s.title!r}\torigin={s.audio.title_origin}\ttopics={topics}\t{result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
