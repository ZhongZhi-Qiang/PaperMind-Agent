"use client";

import { Paperclip, SendHorizonal, X } from "lucide-react";
import { useRef, useState } from "react";

export function ChatInput({
  disabled,
  onSend
}: {
  disabled: boolean;
  onSend: (value: string, file?: File) => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleSend() {
    const nextValue = value.trim();
    if (!nextValue && !file) return;
    void onSend(nextValue, file ?? undefined);
    setValue("");
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (selected) setFile(selected);
  }

  function removeFile() {
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div className="panel rounded-[28px] p-3">
      {file && (
        <div className="mb-2 flex items-center gap-2 rounded-full bg-ocean/10 px-3 py-1.5 text-sm">
          <Paperclip size={14} className="text-ocean" />
          <span className="truncate text-[var(--color-ink)]">{file.name}</span>
          <button
            type="button"
            onClick={removeFile}
            className="ml-auto rounded-full p-0.5 hover:bg-ocean/20"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <textarea
        className="min-h-28 w-full resize-none rounded-[22px] border border-[var(--color-line)] bg-white/70 px-4 py-3 outline-none"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            handleSend();
          }
        }}
        placeholder="输入你的问题，Cmd/Ctrl + Enter 发送"
        value={value}
      />
      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            type="button"
            className="rounded-full p-2 text-[var(--color-ink-soft)] hover:bg-[var(--color-line)] hover:text-[var(--color-ink)]"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            title="上传 PDF"
          >
            <Paperclip size={18} />
          </button>
          <p className="text-sm text-[var(--color-ink-soft)]">
            支持工具调用、Memory 检索和多段响应。
          </p>
        </div>
        <button
          className="flex items-center gap-2 rounded-full bg-ocean px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:bg-[rgba(15,139,141,0.45)]"
          disabled={disabled || (!value.trim() && !file)}
          onClick={handleSend}
          type="button"
        >
          <SendHorizonal size={16} />
          发送
        </button>
      </div>
    </div>
  );
}
