"use client";

import { BookMarked } from "lucide-react";

import type { EvidenceSource } from "@/lib/api";

const TOOL_LABELS: Record<string, string> = {
  query_wiki: "Wiki 检索",
  read_wiki_page: "Wiki 页面",
  list_wiki_pages: "Wiki 列表",
  list_source_files: "源文件",
  search_memory_v3: "记忆检索",
  read_file: "文件读取",
  fetch_url: "网页抓取"
};

export function SourcesCard({ sources }: { sources: EvidenceSource[] }) {
  if (!sources.length) {
    return null;
  }

  return (
    <details className="mb-4 rounded-3xl border border-[rgba(15,139,141,0.18)] bg-[rgba(15,139,141,0.08)] p-4">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-ocean">
        <BookMarked size={16} />
        回答依据 · {sources.length} 个来源
      </summary>
      <div className="mt-3 space-y-3">
        {sources.map((source, index) => (
          <div className="rounded-2xl bg-white/70 p-3" key={`${source.tool}-${index}`}>
            <div className="mb-1 flex items-center justify-between text-xs text-[var(--color-ink-soft)]">
              <span className="rounded-full bg-[rgba(15,139,141,0.12)] px-2 py-0.5 font-medium text-ocean">
                {TOOL_LABELS[source.tool] ?? source.tool}
              </span>
              <span className="font-mono">{source.tool}</span>
            </div>
            {source.query && (
              <p className="mb-1 truncate font-mono text-xs text-[var(--color-ink-soft)]">
                {source.query}
              </p>
            )}
            <p className="text-sm leading-6 text-[var(--color-ink)]">{source.hit}</p>
          </div>
        ))}
      </div>
    </details>
  );
}
