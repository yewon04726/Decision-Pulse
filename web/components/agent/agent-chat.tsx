"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertCircle, Bot, Database, Loader2, SendHorizontal, User, Wrench } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SourceTypeBadge } from "@/components/decision/status-badge";
import { askAgent } from "@/lib/client-api";
import { cn } from "@/lib/utils";
import type { AgentResponse, Project, Source } from "@/lib/types";

type Message =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "agent"; response: AgentResponse }
  | { id: number; role: "error"; text: string };

const SUGGESTIONS = ["현재 DB 결정을 다시 검토해야 해?", "검색 API 캐시 결정은 아직 유효해?", "Kafka 도입하기로 했었나?"];

export function AgentChat({
  project,
  sources,
}: {
  project: Project;
  sources: Pick<Source, "id" | "title" | "type">[];
}) {
  const searchParams = useSearchParams();
  const [input, setInput] = useState(searchParams.get("q") ?? "");
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);
  const sourceById = new Map(sources.map((s) => [s.id, s]));

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, loading]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || loading || !project.agent_connected) return;
    setInput("");
    setMessages((m) => [...m, { id: nextId.current++, role: "user", text: q }]);
    setLoading(true);
    const id = nextId.current++;
    try {
      const response = await askAgent(project.id, q);
      setMessages((m) => [...m, { id, role: "agent", response }]);
      setSelectedId(id);
    } catch (e) {
      setMessages((m) => [...m, { id, role: "error", text: e instanceof Error ? e.message : String(e) }]);
    } finally {
      setLoading(false);
    }
  }

  const agentMessages = messages.filter((m) => m.role === "agent");
  const selected = agentMessages.find((m) => m.id === selectedId) ?? agentMessages.at(-1);
  const response = selected?.response ?? null;

  return (
    <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
      {/* Chat */}
      <Card className="flex min-h-[560px] flex-col overflow-hidden">
        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          {!project.agent_connected && (
            <div className="flex h-full flex-col items-center justify-center py-12 text-center">
              <span className="flex size-12 items-center justify-center rounded-full bg-soft text-muted">
                <Bot className="size-6" />
              </span>
              <p className="mt-3 text-sm font-medium text-ink">
                이 프로젝트에는 아직 Knowledge/Agent가 연결되지 않았습니다.
              </p>
              <p className="mt-1 max-w-md text-xs text-muted">
                현재 Dify Knowledge v4와 Agent는 Decision Debt MVP 프로젝트에만 연결되어 있습니다. 다른 프로젝트의 문서를 근거로
                답하지 않도록 이 프로젝트에서는 Agent를 호출하지 않습니다.
              </p>
            </div>
          )}

          {project.agent_connected && messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center py-12 text-center">
              <span className="flex size-12 items-center justify-center rounded-full bg-soft text-ink">
                <Bot className="size-6" />
              </span>
              <p className="mt-3 text-sm font-medium text-ink">과거 결정과 현재 지표를 함께 확인합니다</p>
              <p className="mt-1 text-xs text-muted">RAG로 결정 근거를 찾고, check_metric으로 현재 상태를 조회합니다.</p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-xs text-ink-2 transition-colors hover:border-ink hover:text-ink"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === "error" ? (
              <div key={m.id} className="flex gap-2">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-danger-soft text-danger">
                  <AlertCircle className="size-3.5" />
                </span>
                <div className="max-w-[85%] rounded-2xl rounded-tl-sm border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">
                  Agent 호출 실패: {m.text}
                </div>
              </div>
            ) : m.role === "user" ? (
              <div key={m.id} className="flex justify-end gap-2">
                <div className="max-w-[80%] rounded-2xl rounded-tr-md bg-ink px-4 py-2.5 text-sm text-white">{m.text}</div>
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-line text-ink-2">
                  <User className="size-3.5" />
                </span>
              </div>
            ) : (
              <div key={m.id} className="flex gap-2">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-soft text-ink">
                  <Bot className="size-3.5" />
                </span>
                <button
                  onClick={() => setSelectedId(m.id)}
                  className={cn(
                    "max-w-[85%] rounded-2xl rounded-tl-md border px-4 py-3 text-left text-sm leading-relaxed whitespace-pre-wrap text-ink transition-colors",
                    m.id === selected?.id ? "border-ink bg-surface" : "border-line bg-surface hover:border-line-strong",
                  )}
                >
                  {m.response.answer}
                  {m.response.guard === "no_evidence" && (
                    <span className="mt-2 block text-[11px] text-faint">
                      검색된 근거 문서가 없어 모델 생성 답변 대신 표준 응답을 표시합니다.
                    </span>
                  )}
                  {m.response.guard === "non_korean" && (
                    <span className="mt-2 block text-[11px] text-faint">
                      모델이 두 번 모두 중국어로 답해 답변을 표시하지 않습니다. (Evidence / Tool Activity는 실제 실행 결과)
                    </span>
                  )}
                  <span className="mt-2 flex gap-3 text-[11px] text-faint">
                    <span>근거 {m.response.retrieval.length}건</span>
                    <span>tool call {m.response.tool_calls.length}회</span>
                    <span>{(m.response.latency_ms / 1000).toFixed(1)}s</span>
                  </span>
                </button>
              </div>
            ),
          )}

          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" />
              Dify Agent 실행 중 (Knowledge 검색 → check_metric → Qwen2.5-7B)… 10~30초 걸릴 수 있습니다
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="flex gap-2 border-t border-line p-3"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!project.agent_connected}
            placeholder={project.agent_connected ? "예: 현재 DB 결정을 다시 검토해야 해?" : "Knowledge/Agent가 연결되지 않은 프로젝트입니다"}
            className="h-10 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-sm outline-none transition-colors placeholder:text-faint focus:border-ink disabled:bg-soft"
          />
          <Button type="submit" disabled={loading || !input.trim() || !project.agent_connected} className="h-10">
            <SendHorizontal className="size-4" />
          </Button>
        </form>
      </Card>

      {/* Evidence & Tool Activity */}
      <div className="space-y-6">
        <Card>
          <CardHeader
            title="Evidence"
            description="Knowledge Retrieval · Top K 5 · threshold 0.50"
            action={<Database className="size-4 text-faint" />}
          />
          {response ? (
            response.retrieval.length > 0 ? (
              <ul className="divide-y divide-line">
                {response.retrieval.map((r, i) => {
                  const src = sourceById.get(r.source_id);
                  return (
                    <li key={`${r.source_id}-${i}`} className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {src && <SourceTypeBadge type={src.type} />}
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{r.title}</span>
                        <span className="font-mono text-[11px] tabular-nums text-muted">{r.score.toFixed(2)}</span>
                      </div>
                      <p className="mt-1.5 line-clamp-4 text-xs text-ink-2">{r.snippet}</p>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="px-5 py-4 text-sm text-muted">threshold 이상인 근거 문서가 없습니다.</p>
            )
          ) : (
            <p className="px-5 py-4 text-sm text-faint">질문하면 검색된 근거가 표시됩니다.</p>
          )}
        </Card>

        <Card>
          <CardHeader title="Tool Activity" description="Function Calling 로그" action={<Wrench className="size-4 text-faint" />} />
          {response && response.tool_calls.length > 0 ? (
            <ol className="space-y-3 p-5">
              {response.tool_calls.map((t, i) => (
                <li key={i} className="rounded-lg border border-line font-mono text-xs">
                  <div className="flex items-center gap-2 border-b border-line bg-soft px-3 py-2">
                    <span className="text-faint">ROUND {t.round}</span>
                    <span className="font-semibold text-ink">CALL {t.name}</span>
                    <span
                      className={cn(
                        "ml-auto rounded px-1.5 py-0.5 text-[10px] font-semibold",
                        t.status === "success" ? "bg-surface text-ink-2" : "bg-danger-soft text-danger",
                      )}
                    >
                      {t.status}
                    </span>
                  </div>
                  <div className="space-y-2 px-3 py-2.5">
                    <div>
                      <div className="text-[10px] uppercase text-faint">input</div>
                      <pre className="mt-0.5 whitespace-pre-wrap text-ink-2">{JSON.stringify(t.arguments, null, 2)}</pre>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase text-faint">output</div>
                      <pre className="mt-0.5 whitespace-pre-wrap text-ink-2">{JSON.stringify(t.result, null, 2)}</pre>
                    </div>
                  </div>
                </li>
              ))}
              <li className="px-1 text-[11px] text-faint">
                ROUND {Math.max(...response.tool_calls.map((t) => t.round)) + 1} → RAG 근거 + 도구 결과로 최종 답변 생성
              </li>
            </ol>
          ) : (
            <p className="px-5 py-4 text-sm text-faint">{response ? "호출된 도구가 없습니다." : "도구 호출 내역이 표시됩니다."}</p>
          )}
        </Card>
      </div>
    </div>
  );
}
