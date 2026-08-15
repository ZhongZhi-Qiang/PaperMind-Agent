"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { RetrievalCard } from "@/components/chat/RetrievalCard";
import { SourcesCard } from "@/components/chat/SourcesCard";
import { ThoughtChain } from "@/components/chat/ThoughtChain";
import type { EvidenceSource, RetrievalResult, ToolCall } from "@/lib/api";

export function ChatMessage({
  role,
  content,
  toolCalls,
  retrievals,
  sources
}: {
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  retrievals: RetrievalResult[];
  sources: EvidenceSource[];
}) {
  const isUser = role === "user";

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
    </article>
  );
}
