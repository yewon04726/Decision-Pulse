"""Audio Source adapter: 업로드 저장 → 로컬 STT → Source 내용 채우기.

STT가 끝나면 이 Source는 일반 텍스트 Source와 똑같이 기존 /api/extract(auto 정책)로 추출한다.
오디오 전용 추출 로직은 없다. 전사 실패 시 추출을 자동으로 부르지 않는다.
"""

import json
import logging
import uuid
from pathlib import Path

from fastapi import UploadFile

import db
from config import AUDIO_DIR, AUDIO_EXTENSIONS, AUDIO_MAX_UPLOAD_MB
from services import stt

log = logging.getLogger("audio_ingest")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [audio] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

_CHUNK = 1024 * 1024


class UploadRejected(Exception):
    """업로드 단계 실패 (지원하지 않는 형식, 용량 초과). status = HTTP status"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def check_extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        raise UploadRejected(400, f"지원하지 않는 오디오 형식입니다: '{ext or '(없음)'}' (지원: {', '.join(AUDIO_EXTENSIONS)})")
    return ext


async def save_upload(project_id: int, upload: UploadFile) -> tuple[Path, int]:
    """원본 오디오를 로컬에 저장 (DB에는 경로만). 용량을 넘으면 지우고 거절한다."""
    ext = check_extension(upload.filename)
    limit = int(AUDIO_MAX_UPLOAD_MB * 1024 * 1024)
    folder = AUDIO_DIR / str(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{ext}"
    size = 0
    try:
        with path.open("wb") as f:
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise UploadRejected(413, f"파일이 너무 큽니다 (최대 {AUDIO_MAX_UPLOAD_MB:g}MB)")
                f.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise UploadRejected(400, "빈 파일입니다")
    return path, size


async def run_transcription(source_id: str) -> None:
    """백그라운드 전사. 예외를 밖으로 내지 않고 상태(ready/failed)로 남긴다."""
    row = db.get_audio_row(source_id)
    if row is None or not db.start_transcription(source_id, stt.ENGINE, stt.model_name(), stt.WHISPER_LANGUAGE):
        return
    log.info("%s 전사 시작 (%s)", source_id, row["original_filename"])
    try:
        t = await stt.transcribe(Path(row["audio_path"]))
    except stt.STTError as e:
        db.fail_transcription(source_id, e.kind, str(e))
        log.warning("%s 전사 실패 [%s] %s", source_id, e.kind, e)
        return
    except Exception as e:  # noqa: BLE001 — 예상하지 못한 오류도 실패 상태로 남긴다
        db.fail_transcription(source_id, "stt_failed", f"{type(e).__name__}: {e}")
        log.exception("%s 전사 오류", source_id)
        return
    db.complete_transcription(
        source_id, t.text, json.dumps(t.segments, ensure_ascii=False), len(t.segments),
        t.duration_seconds, t.transcription_seconds, t.rtf,
    )
    log.info(
        "%s 전사 완료: %d segments, audio %.1fs, STT %.1fs, RTF %s (%s %s)",
        source_id, len(t.segments), t.duration_seconds or 0, t.transcription_seconds, t.rtf, stt.ENGINE, t.model,
    )
