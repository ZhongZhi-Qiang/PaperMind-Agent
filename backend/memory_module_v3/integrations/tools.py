"""Agent tools for memory_module_v3."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_search_memory_v3_tool(recall_service):
    """Create a LangChain tool for searching v3 memory.

    The agent can call this tool to search L1 facts, browse L2 scenes,
    or retrieve the L3 persona.
    """
    from langchain_core.tools import tool

    @tool
    async def search_memory_v3(
        query: str,
        fact_type: str = "",
        scene_name: str = "",
        top_k: int = 10,
    ) -> str:
        """Search the long-term memory system for relevant facts, scenes, and user persona.

        Args:
            query: Search query (keywords or natural language)
            fact_type: Optional filter by fact type (persona/episodic/instruction)
            scene_name: Optional filter by scene name
            top_k: Number of results to return (default 10)
        """
        try:
            facts = await recall_service.search_facts(
                query,
                top_k=top_k,
                fact_type=fact_type or None,
                scene_name=scene_name or None,
            )

            if not facts:
                return "No relevant memories found."

            lines = [f"Found {len(facts)} relevant memories:\n"]
            for f in facts:
                score = f.get("fused_score", f.get("dense_score", f.get("keyword_score", 0)))
                lines.append(
                    f"- [{f.get('fact_type', '?')}] {f.get('content', '')} "
                    f"(score: {score:.3f})"
                )

            return "\n".join(lines)

        except Exception as exc:
            logger.error("search_memory_v3 tool error: %s", exc)
            return f"Memory search failed: {exc}"

    return search_memory_v3


def create_drill_down_tool(offload_manager):
    """Create a LangChain tool for retrieving full tool results by ref_id.

    Used with the symbolic offload layer — when a tool result was compressed,
    the agent can call this to retrieve the full content.
    """
    from langchain_core.tools import tool

    @tool
    async def drill_down(ref_id: str) -> str:
        """Retrieve the full untruncated result of a previous tool call.

        Use this when you need to see the complete output of a tool that was
        summarized. The ref_id is shown in the compressed output.

        Args:
            ref_id: The reference ID (e.g., ref_abc123def456)
        """
        try:
            result = offload_manager.retrieve_ref(ref_id)
            if result:
                return result
            return f"Reference '{ref_id}' not found. It may have been cleaned up."
        except Exception as exc:
            logger.error("drill_down tool error: %s", exc)
            return f"Failed to retrieve reference: {exc}"

    return drill_down
