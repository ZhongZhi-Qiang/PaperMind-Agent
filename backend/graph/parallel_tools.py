"""Parallel Tool Execution for LangGraph agents.

When the LLM returns multiple tool_calls in one response, this module
executes them with asyncio.gather instead of serially.

Integration: patches the tools node in the compiled graph after create_agent().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

logger = logging.getLogger(__name__)


async def _execute_one_safe(tool: BaseTool, tc: dict, security_enabled: bool) -> ToolMessage:
    """Execute a single tool call, with optional HarnessSecurity checks."""
    tool_name = tc.get("name", "unknown")
    args = tc.get("args", {})
    if isinstance(args, str):
        import json
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = {}

    # Apply HarnessSecurity checks when enabled
    if security_enabled:
        try:
            from graph.harness_security import (
                check_protected_path,
                check_dangerous_command,
                check_custom_rules,
                _get_rules,
            )
            for path in _extract_file_paths(args):
                msg = check_protected_path(path)
                if msg:
                    return ToolMessage(
                        content=f"[Harness 安全拦截] {msg}",
                        tool_call_id=tc.get("id", ""),
                        name=tool_name,
                    )
            command = _extract_command(args)
            if command:
                msg = check_dangerous_command(command)
                if msg:
                    return ToolMessage(
                        content=f"[Harness 安全拦截] {msg}",
                        tool_call_id=tc.get("id", ""),
                        name=tool_name,
                    )
                rules_cache = _get_rules()
                msg = check_custom_rules(tool_name, args, rules_cache.custom_rules)
                if msg:
                    return ToolMessage(
                        content=f"[Harness 安全拦截] {msg}",
                        tool_call_id=tc.get("id", ""),
                        name=tool_name,
                    )
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("HarnessSecurity check in parallel tools failed: %s", exc)

    # Execute the tool
    try:
        result = await tool.ainvoke(args)
        content = str(result) if not isinstance(result, str) else result
    except Exception as exc:
        content = f"工具执行错误: {exc}"
        logger.warning("Parallel tool %s failed: %s", tool_name, exc)

    return ToolMessage(content=content, tool_call_id=tc.get("id", ""), name=tool_name)


def _extract_file_paths(args: dict) -> list[str]:
    """Extract file path strings from tool arguments."""
    paths: list[str] = []
    for _key, val in args.items():
        if isinstance(val, str):
            if "/" in val or "\\" in val or val.endswith((".py", ".txt", ".md", ".json", ".yaml", ".yml", ".env", ".key", ".pem")):
                paths.append(val)
    return paths


def _extract_command(args: dict) -> str:
    """Extract command string from tool arguments."""
    for key in ("command", "cmd", "code", "script", "commands"):
        val = args.get(key, "")
        if isinstance(val, str) and val.strip():
            return val
    return ""


class ParallelToolNode(ToolNode):
    """ToolNode subclass that executes multiple tool_calls with asyncio.gather."""

    def __init__(self, tools: list[BaseTool], *, security_enabled: bool = True):
        super().__init__(tools)
        self._security_enabled = security_enabled

    async def ainvoke(self, state: dict | list, config: dict | None = None) -> dict:
        """Execute tool calls — parallel when 2+, serial otherwise.

        Handles both dict state (graph state with 'messages' key) and
        list state (message list or direct tool call list).
        """
        from config import get_settings

        if not get_settings().parallel_tool_calls_enabled:
            return await super().ainvoke(state, config)

        # Normalize input to extract tool_calls from the last message
        if isinstance(state, list):
            if not state:
                return {}
            # Could be [AIMessage(...)] or [{"name": ..., ...}]
            last = state[-1]
            if isinstance(last, dict) and "messages" in last:
                messages = last["messages"]
            elif isinstance(last, dict) and "name" in last:
                # Direct tool call list: delegate to super
                return await super().ainvoke(state, config)
            else:
                messages = state
            if messages:
                last_msg = messages[-1]
                tool_calls = getattr(last_msg, "tool_calls", None) or []
            else:
                tool_calls = []
        else:
            messages = state.get("messages", [])
            if not messages:
                return {}
            last_msg = messages[-1]
            tool_calls = getattr(last_msg, "tool_calls", None) or []

        if len(tool_calls) <= 1:
            return await super().ainvoke(state, config)

        # Execute all tool calls in parallel with security checks
        tools_by_name = {t.name: t for t in self.tools}
        tasks = []
        for tc in tool_calls:
            tool = tools_by_name.get(tc.get("name", ""))
            if tool is None:
                tasks.append(self._missing_tool_result(tc))
            else:
                tasks.append(_execute_one_safe(tool, tc, self._security_enabled))

        tool_messages = await asyncio.gather(*tasks)
        logger.debug("Parallel tools: executed %d tool calls in parallel", len(tool_calls))
        return {"messages": list(tool_messages)}

    async def _missing_tool_result(self, tc: dict) -> ToolMessage:
        return ToolMessage(
            content=f"未知工具: {tc.get('name', '?')}",
            tool_call_id=tc.get("id", ""),
            name=tc.get("name", "unknown"),
        )


def patch_agent_for_parallel_tools(agent_graph: Any, tools: list[BaseTool]) -> Any:
    """Replace the tools node with ParallelToolNode.

    Returns the modified agent_graph, or the original if patching fails.
    """
    try:
        from config import get_settings
        if not get_settings().parallel_tool_calls_enabled:
            return agent_graph

        # Access the compiled graph's nodes
        nodes = getattr(agent_graph, "nodes", None)
        if nodes is None:
            logger.warning("Cannot access graph nodes; parallel tools disabled")
            return agent_graph

        if "tools" not in nodes:
            logger.debug("No 'tools' node found in graph; parallel tools skipped")
            return agent_graph

        security_enabled = getattr(get_settings(), "harness_security_enabled", True)
        parallel_node = ParallelToolNode(tools, security_enabled=security_enabled)

        # nodes["tools"] is a PregelNode wrapper with .node (the runnable) and .bound
        pregel_node = nodes["tools"]
        pregel_node.bound = parallel_node
        pregel_node.node = parallel_node  # parallel_node IS a RunnableCallable (via ToolNode)
        logger.info("ParallelToolNode installed (security=%s)", security_enabled)

        return agent_graph
    except Exception as exc:
        logger.warning("Failed to patch tools node for parallel execution: %s", exc)
        return agent_graph
