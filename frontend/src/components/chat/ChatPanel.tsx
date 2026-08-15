"use client";

import { useEffect, useRef } from "react";

import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { useAppStore } from "@/lib/store";

export function ChatPanel() {
  const { messages, sendMessage, isStreaming, tokenStats } = useAppStore();
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <section className="flex h-full min-w-0 flex-1 flex-col gap-4">
      <div className="panel flex items-center justify-between rounded-[30px] px-5 py-4">
        <div>
          <p className="text-xs uppercase tracking-[0.28em] text-[var(--color-ink-soft)]">
            Conversation
          </p>
          <h2 className="text-lg font-semibold tracking-[-0.04em]">实时对话与工具回放</h2>
        </div>
        <div className="mono text-sm text-[var(--color-ink-soft)]">
          {tokenStats ? `${tokenStats.total_tokens} tokens` : "No metrics yet"}
        </div>
      </div>

      <div className="panel flex min-h-0 flex-1 flex-col rounded-[32px] p-5">
        <div className="flex-1 space-y-4 overflow-y-auto pr-2">
          {!messages.length && (
            <div className="rounded-[28px] border border-dashed border-[var(--color-line)] bg-white/45 p-8">
              <p className="text-xs uppercase tracking-[0.28em] text-[var(--color-ink-soft)]">
                Ready
              </p>
              <h3 className="mt-2 text-3xl font-semibold tracking-[-0.05em]">
                橘子助手 — AI/Agent 学术研究搭档
              </h3>
              <p className="mt-3 max-w-2xl text-[var(--color-ink-soft)]">
                擅长论文解析、知识结构化、文献交叉引用与研究脉络梳理。
                右侧可编辑 Workspace、Skills 和 Memory 文件，所有操作可审计。
              </p>
            </div>
          )}

          {messages.map((message) => (
            <ChatMessage
              content={message.content}
              key={message.id}
              retrievals={message.retrievals}
              role={message.role}
              sources={message.sources}
              toolCalls={message.toolCalls}
            />
          ))}
          <div ref={endRef} />
        </div>
      </div>

      <ChatInput disabled={isStreaming} onSend={sendMessage} />
    </section>
  );
}
