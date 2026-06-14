from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

from tools.fetch_url_tool import FetchURLTool
from tools.python_repl_tool import PythonReplTool
from tools.read_file_tool import ReadFileTool
from tools.terminal_tool import TerminalTool

# Paper Wiki Agent tools
from tools.pdf_parser_tool import PDFParserTool
from tools.wiki_engine_tool import (
    RegisterSourceTool,
    SaveWikiPageTool,
    ReadWikiPageTool,
    ListWikiPagesTool,
    RebuildIndexTool,
    AppendLogTool,
    LintWikiTool,
    QueryWikiTool,
)


def get_all_tools(base_dir: Path) -> list[BaseTool]:
    tools: list[BaseTool] = [
        TerminalTool(root_dir=base_dir),
        PythonReplTool(root_dir=base_dir),
        FetchURLTool(),
        ReadFileTool(root_dir=base_dir),
        # Academic Paper Wiki Agent tools
        PDFParserTool(root_dir=base_dir),
        RegisterSourceTool(root_dir=base_dir),
        SaveWikiPageTool(root_dir=base_dir),
        ReadWikiPageTool(root_dir=base_dir),
        ListWikiPagesTool(root_dir=base_dir),
        RebuildIndexTool(root_dir=base_dir),
        AppendLogTool(root_dir=base_dir),
        LintWikiTool(root_dir=base_dir),
        QueryWikiTool(root_dir=base_dir),
    ]

    from memory_module_v3.config import get_memory_backend

    # v3 memory tool registration
    # Note: _init_v3_once() is awaited in agent.py's astream() before this
    # function is called for tool registration. The services are stored in
    # agent._v3_services dict for cross-module access.
    if get_memory_backend() == "v3":
        try:
            from memory_module_v3.config import get_memory_v3_config
            from graph.agent import _v3_services
            import asyncio

            # Try to init if not yet done (safe to call multiple times)
            from graph.agent import _init_v3_once
            try:
                loop = asyncio.get_running_loop()
                # We're inside a running loop (e.g. FastAPI lifespan).
                # Schedule init as a task and skip tool registration for now.
                # Tools will be re-registered on next call when services are ready.
                if not _v3_services:
                    loop.create_task(_init_v3_once())
                    logger.debug("v3 init scheduled (running loop); tools deferred")
            except RuntimeError:
                # No running loop — safe to run synchronously
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_init_v3_once())
                finally:
                    loop.close()

            # Memory search tool (when inject_mode=tool)
            cfg = get_memory_v3_config()
            recall_service = _v3_services.get("recall_service")
            offload = _v3_services.get("offload")
            if cfg.inject_mode == "tool" and recall_service:
                from memory_module_v3.integrations.tools import create_search_memory_v3_tool
                tools.append(create_search_memory_v3_tool(recall_service))

            # Drill-down tool (when offload enabled)
            if cfg.offload_enabled and offload:
                from memory_module_v3.integrations.tools import create_drill_down_tool
                tools.append(create_drill_down_tool(offload))
        except Exception as exc:
            logger.warning("v3 tool registration failed (non-fatal): %s", exc)

    return tools
