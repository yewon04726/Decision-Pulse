"""테스트용 가짜 gbrain CLI (실제 gbrain/PGLite/embedding 없음).

받은 argv / cwd / 환경변수 이름을 FAKE_GBRAIN_LOG(JSON lines)에 남기고, FAKE_GBRAIN_MODE에 따라 응답한다.
- noop:       exit 0, sync_status=up_to_date, added/modified/embedded=0 (실제 gbrain의 no-op 출력과 같은 형태)
- synced:     exit 0, sync_status=synced, added=1
- busy:       exit 1, pglite_busy (stderr)
- not_synced: exit 0, outcome=failed
"""

import json
import os
import sys

log = os.environ.get("FAKE_GBRAIN_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": sorted(os.environ)}) + "\n")

mode = os.environ.get("FAKE_GBRAIN_MODE", "noop")
print("[sync] Delegating to the registered PGLite owner.", file=sys.stderr)
if mode == "busy":
    print("Error [pglite_busy]: GBrain's local database is already open through `gbrain serve`", file=sys.stderr)
    sys.exit(1)
result = {"schema_version": 1, "source_id": "decision-debt", "sync_status": "up_to_date", "added": 0, "modified": 0,
          "deleted": 0, "chunks_created": 0, "embedded": 0, "outcome": "synced",
          "drain": {"outcome": "synced", "passes": 1, "processed": 0, "written": 0, "waived": 0, "remaining": 0}}
if mode == "synced":
    result.update(sync_status="synced", added=1, chunks_created=2, embedded=2)
if mode == "not_synced":
    result.update(sync_status="failed", outcome="failed", error="source not registered")
print(json.dumps(result))
