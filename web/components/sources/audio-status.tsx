"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { extractFromSource, getSource, retranscribeSource } from "@/lib/client-api";
import type { SourceAudio } from "@/lib/types";

const POLL_MS = 3000;

const STT_ERRORS: Record<string, string> = {
  stt_unavailable: "STT 실행 환경 없음",
  decode_failed: "오디오 해석 실패",
  stt_failed: "전사 실패",
  interrupted: "전사 중단",
};

function seconds(v: number | null) {
  return v === null ? "-" : `${v.toFixed(1)}s`;
}

/** 오디오 Source의 전사 상태 + 동작 (전사 중이면 polling, 완료되면 Extract) */
export function AudioStatus({ sourceId, projectId, audio }: { sourceId: string; projectId: number; audio: SourceAudio }) {
  const router = useRouter();
  const [busy, setBusy] = useState<null | "extract" | "retry">(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string; link?: boolean } | null>(null);
  const status = audio.transcription_status;
  const pending = status === "uploaded" || status === "transcribing";

  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(async () => {
      try {
        const s = await getSource(sourceId);
        if (s.audio && s.audio.transcription_status !== status) router.refresh(); // 상태가 바뀌면 목록 다시 그림
      } catch {
        // 다음 주기에 다시 확인
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [pending, sourceId, status, router]);

  async function extract() {
    setBusy("extract");
    setMessage(null);
    try {
      const r = await extractFromSource(projectId, sourceId);
      const by = r.extraction?.escalated ? " (OpenRouter로 재처리됨)" : "";
      setMessage({ kind: "ok", text: `결정 후보 ${r.candidates.length}개를 Review Queue에 저장했습니다${by}.`, link: true });
    } catch (e) {
      // 추출 실패 — 전사는 정상. STT 실패와 구분
      setMessage({ kind: "error", text: `Extraction 실패 (전사는 정상): ${e instanceof Error ? e.message : e}` });
    } finally {
      setBusy(null);
    }
  }

  async function retry() {
    setBusy("retry");
    try {
      await retranscribeSource(sourceId);
      router.refresh();
    } catch (e) {
      setMessage({ kind: "error", text: `다시 전사 실패: ${e instanceof Error ? e.message : e}` });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3 border-l-2 border-ink bg-surface px-4 py-3 text-xs text-ink-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {pending && (
          <span className="inline-flex items-center gap-1 font-semibold text-ink">
            <Loader2 className="size-3.5 animate-spin" /> Transcribing…
          </span>
        )}
        {status === "ready" && (
          <span className="inline-flex items-center gap-1 font-semibold text-ink-2">
            <CheckCircle2 className="size-3.5" /> Ready
          </span>
        )}
        {status === "failed" && (
          <span className="inline-flex items-center gap-1 font-semibold text-danger">
            <AlertTriangle className="size-3.5" /> Failed · {STT_ERRORS[audio.error_kind ?? ""] ?? "전사 실패"}
          </span>
        )}
        <span className="font-mono">{audio.original_filename}</span>
        <span>
          {audio.transcription_engine ?? "whisper.cpp"} {audio.transcription_model ?? ""}
        </span>
        {status === "ready" && (
          <span className="tabular-nums">
            오디오 {seconds(audio.duration_seconds)} · 전사 {seconds(audio.transcription_seconds)} · RTF {audio.rtf ?? "-"} ·{" "}
            {audio.segments} segments
          </span>
        )}
      </div>
      {status === "failed" && audio.error && <p className="text-danger">{audio.error}</p>}
      <div className="flex flex-wrap gap-2">
        {status === "ready" && (
          <Button size="sm" onClick={extract} disabled={busy !== null}>
            {busy === "extract" ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
            Extract Decisions
          </Button>
        )}
        {status === "failed" && (
          <Button size="sm" variant="secondary" onClick={retry} disabled={busy !== null}>
            {busy === "retry" ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />}
            다시 전사
          </Button>
        )}
      </div>
      {message && (
        <p className={message.kind === "ok" ? "text-ink-2" : "text-danger"}>
          {message.text}{" "}
          {message.link && (
            <Link href="/review" className="font-semibold underline">
              Review candidates →
            </Link>
          )}
        </p>
      )}
    </div>
  );
}
