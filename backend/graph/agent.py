from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import logging

from config import get_settings
from graph.context import RequestContext
from graph.agent_factory import build_agent_config, create_agent_from_config
from graph.harness_review import persist_review, review_conversation
from service.session_manager import SessionManager
from tools import get_all_tools
from memory_module_v3.config import get_memory_backend

# v3 memory — lazy imports to avoid overhead when not in use
_v3_initialized = False
_v3_recorder = None
_v3_pipeline = None
_v3_recall_service = None
_v3_offload = None
# Shared dict for cross-module access (tools/__init__.py reads from here)
_v3_services: dict[str, Any] = {}

logger = logging.getLogger(__name__)


# Knowledge-acquiring tools whose calls constitute "evidence" for an answer.
# Their invocations are collected and surfaced to the frontend as answer sources.
_KNOWLEDGE_TOOLS = {
    "query_wiki", "read_wiki_page", "list_wiki_pages", "list_source_files",
    "search_memory_v3", "read_file", "fetch_url",
}


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


class AgentManager:
    def __init__(self) -> None:
        self.base_dir: Path | None = None
        self.session_manager: SessionManager | None = None
        self.tools = []

    def initialize(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.session_manager = SessionManager(base_dir)
        self.tools = get_all_tools(base_dir)

    # 用于generate_title()和summarize_history()
    def _build_chat_model(self):
        from graph.llm import get_fast_llm
        return get_fast_llm(get_settings(), temperature=0.0, streaming=False)

    def _build_agent(self):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")
        config = build_agent_config(
            self.base_dir, self.tools, use_checkpointer=True
        )
        return create_agent_from_config(config)

    def _build_messages(self, history: list[dict[str, Any]]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in history:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            messages.append({"role": role, "content": str(item.get("content", ""))})
        return messages

    async def astream(
        self,
        message: str,
        history: list[dict[str, Any]],
        context: RequestContext | None = None,
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        import time as _time
        _t0 = _time.perf_counter()  # request received
        _t1: float | None = None     # recall done
        _t2: float | None = None     # first assistant token

        settings = get_settings()
        memory_backend = get_memory_backend()
        turn_messages: list[dict[str, str]] = []
        session_id = context.thread_id if context else "default"

        # --- v3 auto-recall ---
        if memory_backend == "v3":
            await _init_v3_once()
            # Register v3 tools if not already present (init may have completed after initialize())
            if _v3_recall_service and not any(t.name == "search_memory_v3" for t in self.tools):
                from memory_module_v3.config import get_memory_v3_config
                cfg = get_memory_v3_config()
                if cfg.inject_mode == "tool":
                    from memory_module_v3.integrations.tools import create_search_memory_v3_tool
                    self.tools.append(create_search_memory_v3_tool(_v3_recall_service))
                    logger.info("Registered search_memory_v3 tool (deferred)")
                if cfg.offload_enabled and _v3_offload:
                    from memory_module_v3.integrations.tools import create_drill_down_tool
                    self.tools.append(create_drill_down_tool(_v3_offload))
                    logger.info("Registered drill_down tool (deferred)")
            if _v3_recall_service:
                try:
                    recall_result = await _v3_recall_service.recall(message)

                    # L2/L3 stable context: only inject when changed (full replacement)
                    if _v3_recall_service.context_changed:
                        stable_ctx = _v3_recall_service.get_stable_context()
                        if stable_ctx:
                            turn_messages.append({"role": "system", "content": stable_ctx})

                    # L1 dynamic context: inject every turn (query-dependent)
                    prepend = recall_result.get("prepend_context", "")
                    if prepend:
                        turn_messages.append({"role": "assistant", "content": prepend})
                except Exception as v3_exc:
                    logger.warning("Memory v3 auto-recall failed: %s", v3_exc)
            _t1 = _time.perf_counter()
            logger.info(
                "latency session=%s recall_ms=%.0f",
                session_id, (_t1 - _t0) * 1000,
            )

        turn_messages.append({"role": "user", "content": message})

        agent = self._build_agent()
        run_config: dict[str, Any] = {"configurable": {"thread_id": (context.thread_id if context else "")}}
        if context and context.callbacks:
            run_config["callbacks"] = context.callbacks
        if not run_config["configurable"]["thread_id"]:
            run_config["configurable"]["thread_id"] = "default"

        final_content_parts: list[str] = []
        last_ai_message = ""
        pending_tools: dict[str, dict[str, str]] = {}
        last_usage: dict[str, Any] | None = None
        evidence_sources: list[dict[str, str]] = []

        async for mode, payload in agent.astream(
            {"messages": turn_messages},
            stream_mode=["messages", "updates"],
            config=run_config,
            # stream_options={"include_usage": True}
        ):
            if mode == "messages":
                chunk, metadata = payload
                # 优先从 metadata 中读取 usage（LangGraph 在 include_usage=True 时会放在这里）
                usage_candidate: Any = None
                if isinstance(metadata, dict):
                    usage_candidate = metadata.get("usage")
                if isinstance(usage_candidate, dict):
                    last_usage = usage_candidate

                # 只转发有实际文本内容的 AI token，跳过 tool 消息（tool 结果走 tool_end 事件）
                if mode == "messages":
                    msg_type = getattr(chunk, "type", "")
                    if msg_type == "tool":
                        continue
                text = _stringify_content(getattr(chunk, "content", ""))
                if text:
                    if _t2 is None:
                        _t2 = _time.perf_counter()
                        logger.info(
                            "latency session=%s first_token_ms=%.0f ttft_ms=%.0f",
                            session_id,
                            (_t2 - _t1) * 1000 if _t1 else (_t2 - _t0) * 1000,
                            (_t2 - _t0) * 1000,
                        )
                    final_content_parts.append(text)
                    yield {"type": "token", "content": text}
                continue

            if mode != "updates":
                continue

            for update in payload.values():
                if not update:
                    continue
                for agent_message in update.get("messages", []):
                    message_type = getattr(agent_message, "type", "")
                    tool_calls = getattr(agent_message, "tool_calls", []) or []

                    if message_type == "ai" and not tool_calls:
                        candidate = _stringify_content(getattr(agent_message, "content", ""))
                        if candidate:
                            last_ai_message = candidate

                    if tool_calls:
                        for tool_call in tool_calls:
                            call_id = str(tool_call.get("id") or tool_call.get("name"))
                            tool_name = str(tool_call.get("name", "tool"))
                            tool_args = tool_call.get("args", "")
                            if not isinstance(tool_args, str):
                                tool_args = json.dumps(tool_args, ensure_ascii=False)
                            pending_tools[call_id] = {
                                "tool": tool_name,
                                "input": str(tool_args),
                            }
                            yield {
                                "type": "tool_start",
                                "tool": tool_name,
                                "input": str(tool_args),
                            }

                    if message_type == "tool":
                        tool_call_id = str(getattr(agent_message, "tool_call_id", ""))
                        tool_name = getattr(agent_message, "name", "tool")
                        pending = pending_tools.pop(
                            tool_call_id,
                            {"tool": tool_name, "input": ""},
                        )
                        output = _stringify_content(getattr(agent_message, "content", ""))

                        yield {
                            "type": "tool_end",
                            "tool": pending["tool"],
                            "output": output,
                        }
                        yield {"type": "new_response"}

                        # Collect knowledge-acquiring tool calls as answer evidence
                        if pending["tool"] in _KNOWLEDGE_TOOLS and output:
                            evidence_sources.append({
                                "tool": pending["tool"],
                                "query": (pending["input"] or "")[:120],
                                "hit": output[:200],
                            })

        final_content = "".join(final_content_parts).strip() or last_ai_message.strip()

        # --- v3 auto-capture + harness review (post-turn, fire-and-forget) ---
        # Review and memory capture are orchestrated together in one background task so they
        # run serially: review first, then capture. The review result gates high-risk replies
        # out of the retrievable memory layers (L1-L3). Both happen after `done` is yielded,
        # so perceived latency is unchanged.
        if memory_backend == "v3" and _v3_recorder and _v3_pipeline:
            post_turn_coro = _post_turn_review_and_capture(
                agent, run_config, session_id, message, final_content,
            )
            if settings.memory_v3_async_capture:
                _spawn_background_task(post_turn_coro)
            else:
                try:
                    await post_turn_coro
                except Exception as v3_cap_exc:
                    logger.warning("Memory v3 auto-capture failed: %s", v3_cap_exc)
        # 若 LLM 返回了 usage，且本次调用启用了 Langfuse，则在结束时补充 usage 信息，方便在 Langfuse 中显示 tokens
        if last_usage and context and context.callbacks:
            try:
                from langfuse import get_client
                from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

                langfuse_handler: Any | None = None
                for cb in context.callbacks:
                    if isinstance(cb, LangfuseCallbackHandler):
                        langfuse_handler = cb
                        break
                trace_id = getattr(langfuse_handler, "last_trace_id", None) if langfuse_handler else None
                if trace_id:
                    client = get_client()
                    client.trace.update(
                        id=trace_id,
                        usage={
                            "input": last_usage.get("prompt_tokens", 0),
                            "output": last_usage.get("completion_tokens", 0),
                            "total": last_usage.get("total_tokens", 0),
                        },
                    )
            except Exception as exc:
                print("[langfuse] 更新 usage 失败：", repr(exc))
        _t3 = _time.perf_counter()
        logger.info(
            "latency session=%s done_ms=%.0f total_ms=%.0f",
            session_id,
            (_t3 - (_t2 or _t1 or _t0)) * 1000,
            (_t3 - _t0) * 1000,
        )
        if evidence_sources:
            yield {"type": "evidence", "sources": evidence_sources}
        yield {"type": "done", "content": final_content}

    async def generate_title(self, first_user_message: str) -> str:
        prompt = (
            "请根据用户的第一条消息生成一个中文会话标题。"
            "要求不超过 10 个汉字，不要带引号，不要解释。"
        )
        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": first_user_message},
                ]
            )
            title = _stringify_content(getattr(response, "content", "")).strip()
            return title[:10] or "新会话"
        except Exception:
            return (first_user_message.strip() or "新会话")[:10]

    async def summarize_history(self, messages: list[dict[str, Any]]) -> str:
        prompt = (
            "请将以下对话压缩成中文摘要，控制在 500 字以内。"
            "重点保留用户目标、已完成步骤、重要结论和未解决事项。"
        )
        lines: list[str] = []
        for item in messages:
            role = item.get("role", "assistant")
            content = str(item.get("content", "") or "")
            if content:
                lines.append(f"{role}: {content}")
        transcript = "\n".join(lines)

        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ]
            )
            summary = _stringify_content(getattr(response, "content", "")).strip()
            return summary[:500]
        except Exception:
            return transcript[:500]


agent_manager = AgentManager()


# Keep strong refs to fire-and-forget background tasks so they aren't GC'd mid-run.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _post_turn_review_and_capture(
    agent: Any,
    run_config: dict[str, Any],
    session_id: str,
    message: str,
    final_content: str,
) -> None:
    """Post-turn background task: review → persist review → capture memory.

    Runs after the SSE `done` event is yielded, so it never adds perceived latency.
    Review runs first (single LLM call, see ``harness_review.review_conversation``);
    the result is persisted to ``reviews/{session_id}.jsonl`` and used to gate whether
    the assistant reply is allowed into the retrievable memory layers:

    - ``hallucination_risk == "high"`` → only the user message is distilled. L0 keeps
      the raw reply for audit, but it doesn't enter L1-L3.
    - otherwise → both messages distill as before.

    Review failure/timeout → review=None → capture as usual (fail-open, matching the
    "宁存重复不丢事实" philosophy).
    """
    if not _v3_recorder or not _v3_pipeline:
        return

    review = None
    try:
        settings = get_settings()
        if settings.harness_review_enabled:
            state = await agent.aget_state(run_config)
            messages = (state.values or {}).get("messages", [])
            if messages:
                review = await asyncio.wait_for(
                    review_conversation(messages),
                    timeout=settings.harness_review_timeout_ms / 1000,
                )
                if review:
                    reviews_dir = settings.backend_dir / "reviews"
                    persist_review(reviews_dir, session_id, review, final_content)
    except Exception as exc:
        logger.warning("Post-turn review failed (capture continues): %s", exc)
        review = None

    try:
        user_id, asst_id = await _v3_recorder.capture(session_id, message, final_content)
        block_assistant = (
            review is not None
            and review.get("hallucination_risk") == "high"
            and getattr(get_settings(), "harness_review_block_memory", True)
        )
        msg_ids = [user_id] if block_assistant else [user_id, asst_id]
        await _v3_pipeline.notify_conversation(session_id, msg_ids)
    except Exception as exc:
        logger.warning("Memory v3 auto-capture failed: %s", exc)


def _spawn_background_task(coro: Any) -> asyncio.Task:
    """Schedule a fire-and-forget task and track it to prevent GC."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def _init_v3_once():
    """Lazy initialization of v3 memory system (called once on first v3 request)."""
    global _v3_initialized, _v3_recorder, _v3_pipeline, _v3_recall_service, _v3_offload
    if _v3_initialized:
        return

    try:
        from memory_module_v3.storage.pg import ensure_schema
        from memory_module_v3.storage.l0_file_repo import L0FileRepo
        from memory_module_v3.storage.l1_repo import L1Repo
        from memory_module_v3.storage.l2_file_repo import L2FileRepo
        from memory_module_v3.storage.l3_file_repo import L3FileRepo
        from memory_module_v3.storage.pipeline_repo import PipelineStateRepo
        from memory_module_v3.capture.l0_recorder import L0Recorder
        from memory_module_v3.pipeline.manager import PipelineManager
        from memory_module_v3.retrieval.service import RecallService
        from memory_module_v3.config import get_memory_v3_config
        from pathlib import Path

        # Initialize schema (L1 facts + pipeline state in PostgreSQL; L0/L2/L3 are file-based)
        ensure_schema()

        # Create repos
        settings = get_settings()
        data_dir = Path(settings.backend_dir) / "memory_module_v3"
        l0_repo = L0FileRepo(data_dir / "l0")
        l1_repo = L1Repo()
        l2_repo = L2FileRepo(data_dir / "scenes")
        l3_repo = L3FileRepo(data_dir)
        pipeline_repo = PipelineStateRepo()

        # Build embedding function
        from graph.llm import build_embedding_config_from_settings, get_embedding_model
        emb_config = build_embedding_config_from_settings(settings)
        emb_model = get_embedding_model(emb_config)

        async def embedding_fn(text: str) -> list[float]:
            import asyncio
            return await asyncio.to_thread(emb_model.embed_query, text)

        # Build LLM function for extraction/dedup — prefer fast LLM to save tokens/latency
        from graph.llm import get_fast_llm as _get_fast_llm
        distill_llm = _get_fast_llm(settings, temperature=0.0, streaming=False)

        async def llm_fn(system: str, user: str) -> str:
            import asyncio
            response = await asyncio.to_thread(
                distill_llm.invoke,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            content = getattr(response, "content", "")
            return content if isinstance(content, str) else str(content)

        config = get_memory_v3_config()

        _v3_recorder = L0Recorder(l0_repo)
        _v3_recall_service = RecallService(l1_repo, l2_repo, l3_repo, embedding_fn, config)
        _v3_pipeline = PipelineManager(
            l0_repo, l1_repo, l2_repo, l3_repo, pipeline_repo,
            llm_fn, embedding_fn, config,
            on_change=_v3_recall_service.invalidate_cache,
            search_fn=_v3_recall_service.search_facts,
        )

        # Symbolic offload (context compression) — reuse distill/fast LLM
        if config.offload_enabled:
            from memory_module_v3.offload.offload_manager import OffloadManager
            _v3_offload = OffloadManager(
                llm=distill_llm,
                data_dir=Path(settings.backend_dir) / "memory_module_v3" / "offload",
                threshold=config.offload_threshold,
                enabled=True,
            )

        # Store in shared dict for cross-module access (tools/__init__.py)
        _v3_services["recall_service"] = _v3_recall_service
        _v3_services["l2_repo"] = l2_repo
        _v3_services["offload"] = _v3_offload

        _v3_initialized = True
        logger.info("memory_module_v3 initialized successfully")

    except Exception as exc:
        logger.error("Failed to initialize memory_module_v3: %s", exc)
        _v3_initialized = True  # Don't retry on every call
