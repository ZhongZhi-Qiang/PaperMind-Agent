"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { RetrievalCard } from "@/components/chat/RetrievalCard";
import { SourcesCard } from "@/components/chat/SourcesCard";
import { ThoughtChain } from "@/components/chat/ThoughtChain";
import type { EvidenceSource, RetrievalResult, ToolCall } from "@/lib/api";
import { useAppStore } from "@/lib/store";

export function ChatMessage({
  role,
  content,
  toolCalls,
  retrievals,
  sources,
  interrupted
}: {
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  retrievals: RetrievalResult[];
  sources: EvidenceSource[];
  interrupted?: boolean;
}) {
  const isUser = role === "user";
  const { resumeGeneration } = useAppStore();

  // Strip any tool output that leaked into the displayed content
  let displayContent = content;
  if (!isUser && toolCalls.length > 0 && displayContent) {
    for (const tc of toolCalls) {
      const out = (tc.output ?? "").trim();
      if (out && displayContent.trim().startsWith(out)) {
        displayContent = displayContent.trim().slice(out.length).trim();
        break;
      }
    }
  }

  // If content is only tool output, don't show it as chat text
  const isPureToolOutput = !isUser && toolCalls.length > 0
    && toolCalls.some(tc => (tc.output ?? "").trim() === content.trim());

  return (
    <article
      className={`max-w-[90%] rounded-[28px] px-5 py-4 ${
        isUser
          ? "ml-auto bg-[rgba(13,37,48,0.92)] text-white"
          : "panel mr-auto text-[var(--color-ink)]"
      }`}
    >
      {!isUser && <RetrievalCard results={retrievals} />}
      {!isUser && <SourcesCard sources={sources} />}
      {!isUser && <ThoughtChain toolCalls={toolCalls} />}
      {!isPureToolOutput && displayContent && displayContent.trim() !== "" && (
        <div className={isUser ? "whitespace-pre-wrap leading-7" : "markdown"}>
          {isUser ? (
            displayContent
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {displayContent}
            </ReactMarkdown>
          )}
        </div>
      )}
      {!isUser && (!displayContent || displayContent.trim() === "") && !toolCalls.length && (
        <div className="text-[var(--color-ink-soft)]">正在思考...</div>
      )}
      {!isUser && interrupted && (
        <div className="mt-3 flex items-center gap-3">
          <span className="text-sm font-medium text-amber-600">⚠ 回答被中断</span>
          <button
            onClick={() => void resumeGeneration()}
            className="rounded-full border border-[var(--color-line)] px-3 py-1 text-sm text-ocean transition-colors hover:bg-[rgba(15,139,141,0.1)]"
          >
            继续生成
          </button>
        </div>
      )}
    </article>
  );
}
