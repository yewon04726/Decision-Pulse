"""로컬 STT: 오디오 파일 → timestamp가 붙은 raw transcript. 외부 서비스로 오디오를 보내지 않는다.

whisper.cpp(`whisper-cli`)를 subprocess로 실행한다 (Apple Silicon에서는 Metal 가속).
1. ffprobe로 길이 확인
2. ffmpeg로 16kHz mono WAV 변환 (whisper.cpp 입력 형식. m4a 등은 직접 읽지 못함)
3. whisper-cli -oj → segment(시작/끝 ms, 문장) JSON
4. "[HH:MM:SS] 문장" 줄로 변환 (요약하지 않음, 화자 분리 없음)

실패 종류를 구분한다: STTUnavailable(whisper/model/ffmpeg 없음) / AudioDecodeError(오디오 해석 실패) / STTError(전사 실패)
"""

import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from config import FFMPEG, FFPROBE, STT_TIMEOUT_SECONDS, WHISPER_CLI, WHISPER_LANGUAGE, WHISPER_MODEL_PATH

ENGINE = "whisper.cpp"
# 동시에 하나만 전사한다 (로컬 GPU/CPU 부하와 발열 방지)
_LOCK = asyncio.Semaphore(1)


class STTError(Exception):
    kind = "stt_failed"


class STTUnavailable(STTError):
    kind = "stt_unavailable"


class AudioDecodeError(STTError):
    kind = "decode_failed"


@dataclass
class Transcript:
    text: str  # "[00:00:02] ..." 줄들
    segments: list[dict]  # [{"start_ms", "end_ms", "text"}]
    duration_seconds: float | None
    transcription_seconds: float
    model: str
    language: str

    @property
    def rtf(self) -> float | None:
        if not self.duration_seconds:
            return None
        return round(self.transcription_seconds / self.duration_seconds, 3)


def model_name() -> str:
    return WHISPER_MODEL_PATH.name


def check_ready() -> None:
    """STT 실행에 필요한 것이 모두 있는지. 없으면 무엇을 설치해야 하는지 알려 준다."""
    if not shutil.which(WHISPER_CLI):
        raise STTUnavailable(f"whisper.cpp 실행 파일({WHISPER_CLI})이 없습니다 → brew install whisper-cpp")
    if not WHISPER_MODEL_PATH.exists():
        raise STTUnavailable(
            f"Whisper 모델 파일이 없습니다: {WHISPER_MODEL_PATH} → "
            "https://huggingface.co/ggerganov/whisper.cpp 에서 ggml-large-v3-turbo-q5_0.bin을 받아 두세요"
        )
    for tool in (FFMPEG, FFPROBE):
        if not shutil.which(tool):
            raise STTUnavailable(f"{tool}이 없습니다 → brew install ffmpeg")


async def _run(*args: str, timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise STTError(f"{Path(args[0]).name} 시간 초과 ({timeout:.0f}초)") from None
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def probe_duration(path: Path) -> float | None:
    code, out, _ = await _run(FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path), timeout=60)
    try:
        return round(float(out.strip()), 2) if code == 0 else None
    except ValueError:
        return None


def _timestamp(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _segments(whisper_json: dict) -> list[dict]:
    segments = []
    for seg in whisper_json.get("transcription", []):
        text = (seg.get("text") or "").strip()
        offsets = seg.get("offsets") or {}
        if text:
            segments.append({"start_ms": int(offsets.get("from", 0)), "end_ms": int(offsets.get("to", 0)), "text": text})
    return segments


async def transcribe(audio_path: Path) -> Transcript:
    check_ready()
    async with _LOCK:
        started = time.monotonic()
        duration = await probe_duration(audio_path)
        with tempfile.TemporaryDirectory(prefix="dd-stt-") as tmp:
            wav = Path(tmp) / "audio.wav"
            code, _, err = await _run(
                FFMPEG, "-nostdin", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
                timeout=600,
            )
            if code != 0 or not wav.exists():
                raise AudioDecodeError(f"오디오를 해석할 수 없습니다 (ffmpeg): {err.strip().splitlines()[-1] if err.strip() else code}")
            base = Path(tmp) / "transcript"
            code, _, err = await _run(
                WHISPER_CLI, "-m", str(WHISPER_MODEL_PATH), "-f", str(wav), "-l", WHISPER_LANGUAGE, "-oj", "-of", str(base), "-np",
                timeout=STT_TIMEOUT_SECONDS,
            )
            out_json = base.with_suffix(".json")
            if code != 0 or not out_json.exists():
                tail = " ".join(err.strip().splitlines()[-2:]) if err.strip() else f"exit {code}"
                raise STTError(f"whisper.cpp 전사 실패: {tail[:300]}")
            data = json.loads(out_json.read_text(encoding="utf-8", errors="replace"))
        segments = _segments(data)
        if not segments:
            raise STTError("인식된 음성이 없습니다 (무음이거나 언어 설정이 다를 수 있음)")
        text = "\n".join(f"[{_timestamp(s['start_ms'])}] {s['text']}" for s in segments)
        return Transcript(
            text=text,
            segments=segments,
            duration_seconds=duration,
            transcription_seconds=round(time.monotonic() - started, 2),
            model=model_name(),
            language=WHISPER_LANGUAGE,
        )
