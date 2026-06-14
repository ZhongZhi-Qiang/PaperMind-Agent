"""Offload Manager: facade over the Symbolic Short-Term Memory pipeline.

Delegates to OffloadPipeline for L1/L1.5/L2/L3 processing.
Keeps the same public API for backward compatibility with ContextOffloadMiddleware.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from .pipeline import OffloadPipeline
from .storage import OffloadStorage
from .types import ToolPair, _now_china_iso

logger = logging.getLogger(__name__)


class OffloadManager:
    """Facade over the Symbolic Short-Term Memory pipeline.

    Provides the same interface as before (compress, retrieve_ref) but
    delegates to the LLM-based pipeline internally.
    """

    def __init__(
        self,
        llm: BaseChatModel | None = None,
        data_dir: str | Path = "memory_module_v3/offload",
        session_id: str = "default",
        threshold: int = 500,
        enabled: bool = True,
        config: dict[str, Any] | None = None,
    ):
        self._enabled = enabled
        self._threshold = threshold
        self._stats = {"total": 0, "compressed": 0, "passed": 0}

        if llm is not None:
            storage = OffloadStorage(data_dir, session_id)
            pipeline_config = {"offload_threshold": threshold, **(config or {})}
            self._pipeline = OffloadPipeline(llm, storage, pipeline_config)
        else:
            self._pipeline = None

    @property
    def pipeline(self) -> OffloadPipeline | None:
        return self._pipeline

    def set_recent_messages(self, messages: list[Any]) -> None:
        """Update recent messages for prompt context."""
        if self._pipeline:
            self._pipeline.set_recent_messages(messages)

    def add_tool_pair(self, pair: ToolPair) -> None:
        """Add a tool pair to the pipeline buffer."""
        if self._pipeline:
            self._pipeline.add_tool_pair(pair)

    async def run_l15(self) -> Any:
        """Run L1.5 task judgment."""
        if self._pipeline:
            return await self._pipeline.run_l15()
        return None

    def compress(self, tool_name: str, output: str, call_id: str = "") -> str:
        """Compress a tool result. Short results pass through unchanged.

        Delegates to the pipeline for LLM-based compression when available,
        falls back to mechanical truncation otherwise.
        """
        if not self._enabled:
            return output

        self._stats["total"] += 1

        if len(output) <= self._threshold:
            self._stats["passed"] += 1
            return output

        self._stats["compressed"] += 1

        if self._pipeline:
            return self._pipeline.get_compressed_result(tool_name, output, call_id)

        # Mechanical fallback (no LLM available)
        return self._mechanical_compress(tool_name, output, call_id)

    def _mechanical_compress(self, tool_name: str, output: str, call_id: str) -> str:
        """Simple mechanical compression as fallback."""
        from .graph_builder import generate_ref_id

        ref_id = generate_ref_id(tool_name, output)
        length = len(output)
        ref_path = f"refs/{ref_id}.md"

        if self._pipeline:
            ref_path = self._pipeline.storage.write_ref(ref_id, tool_name, output)

        if length <= 2000:
            lines = output.strip().split("\n")
            preview = "\n".join(lines[:5])
        elif length <= 5000:
            lines = output.strip().split("\n")
            preview = lines[0][:80] + "\n...\n" + lines[-1][:80]
        else:
            preview = output[:200] + f"\n... [{length - 400} chars omitted] ...\n" + output[-200:]

        return (
            f"[Offloaded Tool Result | node: N/A]\n"
            f"Summary: [{tool_name}] {preview[:150]}\n"
            f"result_ref: {ref_path} (read this file for full tool call and raw result)"
        )

    def retrieve_ref(self, ref_id: str) -> str | None:
        """Retrieve the full result for a reference ID."""
        if self._pipeline:
            return self._pipeline.storage.read_ref(ref_id)
        return None

    def get_active_mmd_context(self) -> str | None:
        """Get the active MMD file content wrapped in <current_task_context> tags.

        Returns None if no active MMD file exists.
        """
        if not self._pipeline:
            return None
        state = self._pipeline.storage.read_state()
        if not state.active_mmd_file:
            return None
        mmd_content = self._pipeline.storage.read_mmd(state.active_mmd_file)
        if not mmd_content:
            return None

        import re
        # Extract taskGoal from Mermaid metadata
        task_goal = ""
        match = re.search(r'"taskGoal"\s*:\s*"([^"]*)"', mmd_content)
        if match:
            task_goal = match.group(1)

        # Extract node IDs
        node_ids = re.findall(r'(\w+)\["', mmd_content)

        lines = [
            "<current_task_context>",
            "【当前活跃任务的mermaid流程图】这是你最近正在执行的任务的阶段性记录"
            "（此条下方的tool use未被汇总，进程可能有延迟，仅供参考）。",
        ]
        if task_goal:
            lines.append(f"**任务目标:** {task_goal}")
        lines.append(f"**任务文件:** {state.active_mmd_file}")
        if node_ids:
            lines.append(
                "**节点索引:** 可通过 node_id 在 offload JSONL 中查找对应的工具调用记录。"
                "如需查看某个节点对应的原始工具调用与完整结果，请在 JSONL 中找到对应条目的 result_ref 并读取该文件。"
            )
        lines.append("")
        lines.append("```mermaid")
        lines.append(mmd_content)
        lines.append("```")
        lines.append("")
        lines.append('标记为 "doing" 的节点是近期焦点，"done" 的已完成。')
        lines.append("请参考此保持方向感，避免重复已完成的工作。")
        lines.append("</current_task_context>")

        return "\n".join(lines)

    def compress_messages_by_score(
        self,
        messages: list[Any],
        context_ratio: float,
    ) -> tuple[list[Any], int]:
        """L3: Compress messages using L1 scores."""
        if self._pipeline:
            return self._pipeline.compress_messages_by_score(messages, context_ratio)
        return messages, 0

    def flush_pending(self) -> None:
        """Force-trigger L1 for remaining pending pairs."""
        if self._pipeline:
            self._pipeline.flush_pending()

    def get_stats(self) -> dict[str, int]:
        """Return compression statistics."""
        return dict(self._stats)
