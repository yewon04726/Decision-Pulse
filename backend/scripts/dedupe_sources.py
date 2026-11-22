"""중복 Source 정리 (과거 테스트로 같은 원문이 여러 번 등록된 것).

판정
- 같은 project_id 안에서 정규화한 content의 sha256이 같으면 같은 원문 (제목/파일명은 기준이 아님)
  정규화: 유니코드 NFC, \\r\\n → \\n, 줄 끝 공백 제거, 앞뒤 공백 제거 (그 밖의 내용은 그대로 비교)
- 연결 = candidates.source_id(상태 무관) / decision_sources.source_id(Decision evidence) / source_audio
삭제 대상 (그룹마다)
- 연결된 Source가 있으면: 연결된 Source는 모두 유지, 연결 없는 복제본만 삭제
- 모두 연결이 없으면: 가장 먼저 등록된 1개를 원본으로 유지, 나머지 복제본만 삭제 (결정이 없는 문서도 1개는 남김)
- 연결된 Source가 2개 이상이면 자동 삭제 대상에서 그 Source들은 제외하고 보고만 한다
- 오디오 Source는 삭제하지 않는다

사용 (backend 폴더에서)
  .venv/bin/python scripts/dedupe_sources.py            # dry-run: 대상 목록만 출력 (DB 변경 없음)
  .venv/bin/python scripts/dedupe_sources.py --apply    # 백업 후 한 트랜잭션으로 삭제
"""

import argparse
import hashlib
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def normalize(content: str) -> str:
    text = unicodedata.normalize("NFC", content or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def content_hash(content: str) -> str:
    return hashlib.sha256(normalize(content).encode("utf-8")).hexdigest()


def plan(conn: sqlite3.Connection):
    rows = conn.execute(
        """SELECT s.id, s.project_id, s.title, s.type, s.created_at, s.content,
                  (SELECT COUNT(*) FROM candidates c WHERE c.source_id = s.id) AS candidates,
                  (SELECT COUNT(*) FROM decision_sources d WHERE d.source_id = s.id) AS evidence,
                  (SELECT COUNT(*) FROM source_audio a WHERE a.source_id = s.id) AS audio
           FROM sources s ORDER BY s.project_id, s.created_at, s.rowid"""
    ).fetchall()
    groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault((r["project_id"], content_hash(r["content"])), []).append(r)

    result = []
    for (project_id, h), members in groups.items():
        if len(members) < 2:
            continue
        linked = [m for m in members if m["candidates"] or m["evidence"] or m["audio"]]
        if linked:
            keep = linked
            reason = "연결된 Source 유지 (Candidate / Decision evidence / audio)"
        else:
            keep = [members[0]]  # created_at 가장 이른 것 = 원본
            reason = "모두 미연결 → 가장 먼저 등록된 원본 1개 유지"
        keep_ids = {k["id"] for k in keep}
        delete = [m for m in members if m["id"] not in keep_ids and not m["audio"]]
        result.append({
            "project_id": project_id, "hash": h[:12], "members": members, "keep": keep, "delete": delete,
            "reason": reason, "multi_linked": len(linked) > 1,
        })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="백업 후 실제 삭제")
    args = parser.parse_args()

    with db.connect() as conn:
        groups = plan(conn)
        total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    to_delete = [m for g in groups for m in g["delete"]]
    print(f"{'APPLY' if args.apply else 'DRY-RUN'} — sources {total}, 중복 그룹 {len(groups)}, 삭제 대상 {len(to_delete)}\n")
    for g in groups:
        titles = sorted({m["title"] for m in g["members"]})
        print(f"[project {g['project_id']}] hash {g['hash']} · {len(g['members'])}개 · titles={titles}")
        print(f"  유지: {g['reason']}" + ("  ⚠ 연결된 Source 여러 개 → 자동 삭제 안 함, 보고만" if g["multi_linked"] else ""))
        for m in g["members"]:
            mark = "DELETE" if m in g["delete"] else "keep  "
            print(f"   {mark} {m['id']:<24} {m['type']:<16} created={m['created_at']} cand={m['candidates']} evid={m['evidence']} audio={m['audio']}")
        print()

    # 안전 검사: 삭제 대상에 연결이 하나라도 있으면 중단
    unsafe = [m["id"] for m in to_delete if m["candidates"] or m["evidence"] or m["audio"]]
    if unsafe:
        sys.exit(f"중단: 연결된 Source가 삭제 대상에 포함됨 {unsafe}")
    if not args.apply:
        print("dry-run: DB를 바꾸지 않았습니다. 실제 삭제는 --apply")
        return

    backup = db.DB_PATH.with_name(f"{db.DB_PATH.stem}.before-dedupe-sources-{datetime.now():%Y%m%d-%H%M%S}.db")
    with db.connect() as conn:  # 백업은 일관된 스냅샷으로 (sqlite backup API)
        dst = sqlite3.connect(backup)
        conn.backup(dst)
        dst.close()
    print(f"backup: {backup}")

    ids = [m["id"] for m in to_delete]
    with db.connect() as conn:  # 한 트랜잭션: 삭제 직전에 연결을 다시 확인
        for sid in ids:
            linked = conn.execute(
                "SELECT (SELECT COUNT(*) FROM candidates WHERE source_id = ?) + (SELECT COUNT(*) FROM decision_sources WHERE source_id = ?)"
                " + (SELECT COUNT(*) FROM source_audio WHERE source_id = ?)", (sid, sid, sid),
            ).fetchone()[0]
            if linked:
                raise SystemExit(f"중단(롤백): {sid}에 연결이 생김")
            conn.execute("DELETE FROM sources WHERE id = ?", (sid,))
    print(f"deleted {len(ids)}: {ids}")


if __name__ == "__main__":
    main()
