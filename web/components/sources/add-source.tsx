"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, FileUp, Loader2, Mic, Plus, X } from "lucide-react";
import { inputCls } from "@/components/project/create-project-form";
import { Button, type ButtonProps } from "@/components/ui/button";
import { extractDecisions, extractFromSource, getSource, uploadAudioSource } from "@/lib/client-api";
import { cn } from "@/lib/utils";
import type { Project, Source, SourceType } from "@/lib/types";

/**
 * 모든 원문 입력의 단일 진입점 (Sources / Candidates / Overview에서 같은 컴포넌트를 연다).
 * 기존 backend 흐름을 그대로 쓴다 — 새 ingestion API 없음, API 호출 횟수도 기존과 같다.
 *   Paste text / Upload document(.md .txt) → POST /api/extract 1회 (추출 성공 시 Source 저장 + Candidate 생성)
 *   Upload audio(.wav .mp3 .m4a)           → POST /api/sources/audio (즉시 Source 생성, 로컬 STT)
 *                                            → 전사 완료 후 Extract → POST /api/extract {source_id} 1회
 */

type Mode = "text" | "document" | "audio";

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "text", label: "Paste text", hint: "회의록 · Issue · ADR 원문 붙여넣기" },
  { value: "document", label: "Upload document", hint: ".md · .txt" },
  { value: "audio", label: "Upload audio", hint: ".wav · .mp3 · .m4a → 로컬 STT" },
];

const TEXT_TYPES: { value: SourceType; label: string }[] = [
  { value: "meeting", label: "Meeting" },
  { value: "issue", label: "Issue" },
  { value: "adr", label: "ADR" },
  { value: "pr", label: "PR" },
];

const PLACEHOLDER = `회의록 / Issue / ADR 원문을 붙여넣으세요.

형식 예)
- 무엇을 하기로 했다. (결정)
- 왜냐하면 ~ 때문이다. (이유)
- ~하면 다시 검토한다. (재검토 조건)`;

type Result =
  | { kind: "extracted"; count: number; notes: string[]; escalated?: boolean }
  | { kind: "error"; text: string };

/** 추출 응답 → 안내 문장 (기존 Review Queue 입력 폼과 같은 규칙) */
function extractionNotes(warnings: string[] = []) {
  // warnings에는 자동 교정·구조화 실패 안내도 섞여 있으므로 결정과 연결되지 않은 재검토 조건만 센다
  const unlinked = warnings.filter((w) => w.startsWith("review condition")).length;
  const other = warnings.length - unlinked;
  return [
    ...(unlinked ? [`현재 결정과 연결되지 않은 재검토 조건 ${unlinked}개는 후보로 만들지 않았습니다.`] : []),
    ...(other ? [`재검토 조건 자동 교정/구조화 안내 ${other}건은 backend 로그를 확인하세요.`] : []),
  ];
}

export function AddSourceButton({
  project,
  label = "Add source",
  variant = "primary",
  defaultOpen = false,
}: {
  project: Pick<Project, "id" | "name" | "metrics_connected">;
  label?: string;
  variant?: ButtonProps["variant"];
  /** /sources?add=1 처럼 바로 열고 들어올 때 */
  defaultOpen?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (defaultOpen) {
      dialog.current?.showModal();
      const t = setTimeout(() => setOpen(true), 0);
      return () => clearTimeout(t);
    }
  }, [defaultOpen]);

  function show() {
    dialog.current?.showModal();
    setOpen(true);
  }

  function close() {
    dialog.current?.close();
  }

  return (
    <>
      <Button variant={variant} onClick={show}>
        <Plus className="size-4" />
        {label}
      </Button>
      <dialog
        ref={dialog}
        onClose={() => setOpen(false)}
        onClick={(e) => e.target === dialog.current && close()} // 바깥(backdrop) 클릭으로 닫기
        aria-labelledby="add-source-title"
        className={cn(
          "m-0 max-h-[100dvh] w-full max-w-none overflow-y-auto bg-surface p-0 text-ink backdrop:bg-ink/30",
          // 모바일: 아래에서 올라오는 sheet / 데스크톱: 가운데 modal
          "fixed inset-x-0 bottom-0 top-auto rounded-t-2xl border-t border-line",
          "sm:inset-0 sm:m-auto sm:h-fit sm:max-h-[90dvh] sm:w-[min(40rem,calc(100vw-2rem))] sm:rounded-2xl sm:border",
        )}
      >
        {/* 닫았다 열면 입력/결과 상태를 새로 시작한다 */}
        {open && <AddSourcePanel project={project} onClose={close} />}
      </dialog>
    </>
  );
}

function AddSourcePanel({ project, onClose }: { project: Pick<Project, "id" | "name" | "metrics_connected">; onClose: () => void }) {
  const [mode, setMode] = useState<Mode>("text");
  const [busy, setBusy] = useState(false);

  return (
    <div className="animate-rise flex flex-col">
      <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-5 sm:px-7">
        <div>
          <div className="eyebrow">Sources · {project.name}</div>
          <h2 id="add-source-title" className="mt-2 text-2xl font-semibold tracking-[-0.03em]">
            Add source
          </h2>
          <p className="mt-1 text-sm text-muted">원문을 등록하면 AI가 결정 후보를 추출합니다. 결정은 사람이 Candidates에서 승인합니다.</p>
        </div>
        <button type="button" onClick={onClose} aria-label="닫기" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-soft hover:text-ink">
          <X className="size-5" />
        </button>
      </div>

      <div role="tablist" aria-label="입력 방식" className="flex gap-6 overflow-x-auto border-b border-line px-5 [scrollbar-width:none] sm:px-7">
        {MODES.map((m) => (
          <button
            key={m.value}
            role="tab"
            type="button"
            aria-selected={mode === m.value}
            disabled={busy}
            onClick={() => setMode(m.value)}
            className={cn(
              "-mb-px shrink-0 border-b-2 py-3 text-sm font-medium transition-colors disabled:opacity-50",
              mode === m.value ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="px-5 py-6 sm:px-7">
        <p className="mb-4 text-xs text-muted">{MODES.find((m) => m.value === mode)?.hint}</p>
        {mode === "audio" ? (
          <AudioForm key="audio" projectId={project.id} onBusy={setBusy} onClose={onClose} />
        ) : (
          <TextForm key={mode} mode={mode} project={project} onBusy={setBusy} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

/** Paste text / Upload document — 둘 다 원문 텍스트를 /api/extract 1회로 보낸다 */
function TextForm({
  mode,
  project,
  onBusy,
  onClose,
}: {
  mode: "text" | "document";
  project: Pick<Project, "id" | "metrics_connected">;
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [type, setType] = useState<SourceType>("meeting");
  const [content, setContent] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  async function onFile(file: File | undefined) {
    if (!file) return;
    if (!/\.(md|txt)$/i.test(file.name)) {
      setResult({ kind: "error", text: ".md 또는 .txt 파일만 불러올 수 있습니다." });
      return;
    }
    setResult(null);
    setFileName(file.name);
    setTitle((t) => t || file.name);
    setContent(await file.text());
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim() || extracting) return;
    setExtracting(true);
    onBusy(true);
    setResult(null);
    try {
      const { candidates, warnings = [] } = await extractDecisions({ projectId: project.id, title: title.trim(), type, content });
      setResult({ kind: "extracted", count: candidates.length, notes: extractionNotes(warnings) });
      setTitle("");
      setContent("");
      setFileName(null);
      router.refresh(); // Sources 목록 / Calendar / Candidates 수 갱신
    } catch (err) {
      setResult({ kind: "error", text: `Extract 실패: ${err instanceof Error ? err.message : err}` });
    } finally {
      setExtracting(false);
      onBusy(false);
    }
  }

  if (result?.kind === "extracted") return <ExtractedResult result={result} onClose={onClose} onAgain={() => setResult(null)} />;

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
        <label className="block text-xs text-muted">
          Title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="예: meeting_2026_10_11.md"
            className={cn(inputCls, "mt-1.5 font-mono text-xs")}
            required
          />
        </label>
        <label className="block text-xs text-muted">
          Source type
          <select value={type} onChange={(e) => setType(e.target.value as SourceType)} className={cn(inputCls, "mt-1.5")}>
            {TEXT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {mode === "document" ? (
        <div>
          <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-sm text-muted transition-colors hover:border-ink hover:text-ink">
            <FileUp className="size-5" />
            {fileName ? <span className="font-mono text-xs text-ink">{fileName}</span> : "파일 선택 (.md / .txt)"}
            <input type="file" accept=".md,.txt,text/plain,text/markdown" className="sr-only" onChange={(e) => onFile(e.target.files?.[0])} />
          </label>
          {content && (
            <pre className="mt-3 max-h-40 overflow-auto rounded-lg border border-line bg-paper p-3 font-sans text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
              {content.slice(0, 1200)}
              {content.length > 1200 && "…"}
            </pre>
          )}
        </div>
      ) : (
        <label className="block text-xs text-muted">
          Raw text
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            placeholder={PLACEHOLDER}
            className={cn(inputCls, "mt-1.5 resize-y leading-relaxed")}
            required
          />
        </label>
      )}

      {result?.kind === "error" && <p className="text-xs text-danger">{result.text}</p>}

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[11px] leading-relaxed text-faint">
          추출이 성공하면 Source가 저장되고 결과는 후보로만 저장됩니다.
          {!project.metrics_connected && " 이 프로젝트는 metric 미연결이라 재검토 조건은 '사람이 확인'으로 추출됩니다."}
        </p>
        <Button type="submit" disabled={extracting || !title.trim() || !content.trim()} className="shrink-0">
          {extracting && <Loader2 className="size-4 animate-spin" />}
          {extracting ? "추출 중… (10~30초)" : "Add & Extract"}
        </Button>
      </div>
    </form>
  );
}

/** Upload audio — 기존 Audio Upload와 같은 API. 업로드 즉시 Source 생성 → 로컬 STT → 완료 후 Extract */
function AudioForm({ projectId, onBusy, onClose }: { projectId: number; onBusy: (busy: boolean) => void; onClose: () => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const status = source?.audio?.transcription_status;
  const transcribing = status === "uploaded" || status === "transcribing";

  // 전사 진행 확인 (기존 AudioStatus와 같은 GET /api/sources/{id} polling)
  useEffect(() => {
    if (!source || !transcribing) return;
    const timer = setInterval(async () => {
      try {
        const s = await getSource(source.id);
        if (s.audio?.transcription_status !== status) {
          setSource(s);
          router.refresh();
        }
      } catch {
        // 다음 주기에 다시 확인
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [source, transcribing, status, router]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    onBusy(true);
    setResult(null);
    try {
      setSource(await uploadAudioSource(projectId, title.trim() || file.name, file));
      router.refresh();
    } catch (err) {
      // 업로드 실패 (형식/용량/연결) — 전사·추출 실패와 구분
      setResult({ kind: "error", text: `업로드 실패: ${err instanceof Error ? err.message : err}` });
    } finally {
      setUploading(false);
      onBusy(false);
    }
  }

  async function extract() {
    if (!source) return;
    setExtracting(true);
    onBusy(true);
    try {
      const r = await extractFromSource(projectId, source.id);
      setResult({ kind: "extracted", count: r.candidates.length, notes: extractionNotes(r.warnings), escalated: r.extraction?.escalated });
      router.refresh();
    } catch (err) {
      setResult({ kind: "error", text: `Extraction 실패 (전사는 정상): ${err instanceof Error ? err.message : err}` });
    } finally {
      setExtracting(false);
      onBusy(false);
    }
  }

  if (result?.kind === "extracted") return <ExtractedResult result={result} onClose={onClose} />;

  if (source) {
    return (
      <div className="space-y-4">
        <ol className="space-y-2.5 text-sm">
          <Step done label="Upload · Source 생성" detail={source.title} />
          <Step
            done={status === "ready"}
            active={transcribing}
            failed={status === "failed"}
            label="whisper.cpp 로컬 STT"
            detail={
              status === "failed"
                ? (source.audio?.error ?? "전사 실패")
                : transcribing
                  ? "전사 중… 창을 닫아도 계속 진행되며 All sources에서 상태를 볼 수 있습니다."
                  : "Transcript 준비됨"
            }
          />
          <Step active={extracting} label="Extract → Candidates" detail={status === "ready" ? "결정 후보 추출" : "전사 완료 후 진행"} />
        </ol>
        {result?.kind === "error" && <p className="text-xs text-danger">{result.text}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button onClick={extract} disabled={status !== "ready" || extracting}>
            {extracting && <Loader2 className="size-4 animate-spin" />}
            Extract decisions
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={upload} className="space-y-4">
      <label className="block text-xs text-muted">
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="회의 제목 (비우면 파일명)" className={cn(inputCls, "mt-1.5")} />
      </label>
      <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-sm text-muted transition-colors hover:border-ink hover:text-ink">
        <Mic className="size-5" />
        {file ? <span className="font-mono text-xs text-ink">{file.name}</span> : "오디오 파일 선택 (.wav / .mp3 / .m4a)"}
        <input
          type="file"
          accept=".wav,.mp3,.m4a"
          className="sr-only"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setFile(f);
            if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, ""));
          }}
        />
      </label>
      {result?.kind === "error" && <p className="text-xs text-danger">{result.text}</p>}
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[11px] leading-relaxed text-faint">업로드하면 Source가 바로 생성되고 로컬 STT(whisper.cpp)로 전사합니다. 오디오는 외부로 보내지 않습니다.</p>
        <Button type="submit" disabled={!file || uploading} className="shrink-0">
          {uploading && <Loader2 className="size-4 animate-spin" />}
          Upload &amp; Process
        </Button>
      </div>
    </form>
  );
}

function Step({ label, detail, done, active, failed }: { label: string; detail: string; done?: boolean; active?: boolean; failed?: boolean }) {
  return (
    <li className="flex gap-3">
      <span
        className={cn(
          "mt-1 flex size-4 shrink-0 items-center justify-center rounded-full border",
          done ? "border-ink bg-ink text-white" : failed ? "border-danger" : active ? "border-ink" : "border-line-strong",
        )}
      >
        {done ? <CheckCircle2 className="size-4" /> : active ? <Loader2 className="size-3 animate-spin" /> : null}
      </span>
      <span className="min-w-0">
        <span className={cn("block font-medium", failed ? "text-danger" : done || active ? "text-ink" : "text-muted")}>{label}</span>
        <span className="block truncate text-xs text-muted">{detail}</span>
      </span>
    </li>
  );
}

/** 추출 결과: 후보 N개 → Candidates로 이동 / 0개 → Source만 등록됨 */
function ExtractedResult({
  result,
  onClose,
  onAgain,
}: {
  result: Extract<Result, { kind: "extracted" }>;
  onClose: () => void;
  onAgain?: () => void;
}) {
  return (
    <div className="animate-rise space-y-4" role="status">
      <div className="border-l-2 border-ink pl-4">
        {result.count > 0 ? (
          <p className="text-[15px] font-semibold">
            {result.count} decision candidates extracted.
            {result.escalated && <span className="ml-1 text-xs font-normal text-muted">(OpenRouter로 재처리됨)</span>}
          </p>
        ) : (
          <>
            <p className="text-[15px] font-semibold">Source processed.</p>
            <p className="mt-0.5 text-sm text-muted">No decision candidates were found. 원문은 All sources에 남아 있습니다.</p>
          </>
        )}
        {result.notes.map((n) => (
          <p key={n} className="mt-1 text-xs text-muted">
            {n}
          </p>
        ))}
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {onAgain && (
          <Button variant="ghost" onClick={onAgain}>
            Add another
          </Button>
        )}
        {result.count > 0 ? (
          <Link
            href="/review"
            onClick={onClose}
            className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-ink px-4 text-sm font-medium text-white transition-colors hover:bg-ink-2"
          >
            Review candidates <span aria-hidden>→</span>
          </Link>
        ) : (
          <Button onClick={onClose}>Done</Button>
        )}
      </div>
    </div>
  );
}
