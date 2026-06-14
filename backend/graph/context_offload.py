"""Context Offload Middleware: compress verbose tool results before the LLM sees them.

Uses the Symbolic Short-Term Memory pipeline (L1/L1.5/L2/L3) with LLM-based
summarization, task-aware Mermaid generation, and progressive compression.

Two independent operations in before_model:
1. Summary replacement (瘦身): replace old tool results with plain text summaries
2. MMD injection (补视图): insert active Mermaid task diagram as context

Hooks:
- wrap_tool_call: intercept tool execution, collect ToolPair, compress result
- before_model: L3 compression + MMD injection
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from typing_extensions import override

from memory_module_v3.offload.offload_manager import OffloadManager
from memory_module_v3.offload.types import ToolPair, _now_china_iso

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_OFFLOAD_THRESHOLD = 500
DEFAULT_HISTORY_WINDOW = 10


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


class ContextOffloadMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Compresses verbose tool results via the Symbolic Short-Term Memory pipeline.

    Two independent operations:
    1. Summary replacement (瘦身): wrap_tool_call + L3 compress old results to plain text
    2. MMD injection (补视图): before_model inserts active Mermaid task diagram
    """

    def __init__(
        self,
        offload_manager: OffloadManager,
        history_window: int = DEFAULT_HISTORY_WINDOW,
    ) -> None:
        super().__init__()
        self._offload = offload_manager
        self._history_window = history_window

    @override
    def wrap_tool_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """Intercept tool execution: call the tool, compress the result."""
        result = handler(request)

        if isinstance(result, ToolMessage):
            original = _stringify_content(result.content)

            # Collect ToolPair for pipeline
            pair = ToolPair(
                tool_name=getattr(request, "name", "tool"),
                tool_call_id=str(getattr(result, "tool_call_id", "")),
                params=getattr(request, "args", {}),
                result=original,
                timestamp=_now_china_iso(),
            )
            self._offload.add_tool_pair(pair)

            # Get compressed version
            compressed = self._offload.compress(
                tool_name=pair.tool_name,
                output=original,
                call_id=pair.tool_call_id,
            )
            if compressed != original:
                result = ToolMessage(
                    content=compressed,
                    tool_call_id=result.tool_call_id,
                    name=getattr(result, "name", ""),
                    id=getattr(result, "id", None),
                )
        return result

    @override
    async def awrap_tool_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """Async variant of wrap_tool_call."""
        result = await handler(request)

        if isinstance(result, ToolMessage):
            original = _stringify_content(result.content)

            pair = ToolPair(
                tool_name=getattr(request, "name", "tool"),
                tool_call_id=str(getattr(result, "tool_call_id", "")),
                params=getattr(request, "args", {}),
                result=original,
                timestamp=_now_china_iso(),
            )
            self._offload.add_tool_pair(pair)

            compressed = self._offload.compress(
                tool_name=pair.tool_name,
                output=original,
                call_id=pair.tool_call_id,
            )
            if compressed != original:
                result = ToolMessage(
                    content=compressed,
                    tool_call_id=result.tool_call_id,
                    name=getattr(result, "name", ""),
                    id=getattr(result, "id", None),
                )
        return result

    @override
    def before_model(
        self,
        state: AgentState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        """L3 compression + MMD injection.

        Two independent operations:
        1. Summary replacement (瘦身): replace old tool results with plain text summaries
        2. MMD injection (补视图): insert active Mermaid task diagram as context
        """
        messages = state.get("messages") or []
        if len(messages) <= self._history_window * 2:
            return None

        # Update recent messages for pipeline context
        self._offload.set_recent_messages(list(messages))

        # Estimate context utilization (simple heuristic)
        total_chars = sum(len(_stringify_content(getattr(m, "content", ""))) for m in messages)
        # Rough estimate: 1 token ≈ 4 chars for English, 2 chars for Chinese
        estimated_tokens = total_chars // 3
        context_window = 200000  # Default, could be configurable
        context_ratio = estimated_tokens / context_window

        new_messages = list(messages)
        changes = 0

        # Phase 1: L3 compression (summary replacement)
        if context_ratio >= 0.3:
            new_messages, compressed = self._offload.compress_messages_by_score(
                new_messages, context_ratio
            )
            if compressed > 0:
                logger.debug(
                    "ContextOffload L3: compressed %d messages (ratio=%.2f)",
                    compressed, context_ratio,
                )
                changes += compressed

        # Phase 3: MMD injection (insert active task diagram as context)
        mmd_context = self._offload.get_active_mmd_context()
        if mmd_context:
            insert_idx = self._find_mmd_insertion_point(new_messages)
            mmd_msg = HumanMessage(content=mmd_context)
            mmd_msg._mmd_context_message = "active"  # type: ignore[attr-defined]
            new_messages.insert(insert_idx, mmd_msg)
            changes += 1
            logger.debug("ContextOffload: injected MMD context at index %d", insert_idx)

        if changes > 0:
            return {"messages": new_messages}

        return None

    @staticmethod
    def _find_mmd_insertion_point(messages: list) -> int:
        """Find the best insertion point for MMD context message.

        Inserts after the last user message, but avoids splitting
        tool_use and tool_result pairs.
        """
        last_user_idx = 0
        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage) and not getattr(msg, "_mmd_context_message", None):
                last_user_idx = i
        return last_user_idx + 1

    @override
    async def abefore_model(
        self,
        state: AgentState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    async def run_l15(self) -> Any:
        """Run L1.5 task judgment (called from before_agent or similar hook)."""
        return await self._offload.run_l15()

    def flush(self) -> None:
        """Force-trigger L1 for remaining pending pairs."""
        self._offload.flush_pending()


def build_context_offload_middleware(
    llm: BaseChatModel | None = None,
    data_dir: str = "memory_module_v3/offload",
    session_id: str = "default",
    threshold: int = DEFAULT_OFFLOAD_THRESHOLD,
    history_window: int = DEFAULT_HISTORY_WINDOW,
    config: dict[str, Any] | None = None,
) -> ContextOffloadMiddleware:
    """Factory for ContextOffloadMiddleware.

    Args:
        llm: LLM instance for pipeline (None = mechanical fallback only)
        data_dir: Data directory for offload storage
        session_id: Session ID for JSONL isolation
        threshold: Character threshold for compression
        history_window: Number of recent messages to keep intact
        config: Additional pipeline configuration
    """
    manager = OffloadManager(
        llm=llm,
        data_dir=data_dir,
        session_id=session_id,
        threshold=threshold,
        enabled=True,
        config=config,
    )
    return ContextOffloadMiddleware(
        offload_manager=manager,
        history_window=history_window,
    )
