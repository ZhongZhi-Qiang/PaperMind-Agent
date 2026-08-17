from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from graph.context import build_request_context
from graph.agent import agent_manager
from graph.checkpointer import reconnect_checkpointer_async
from api._sse_utils import sse_event

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = ""
    session_id: str
    stream: bool = True
    resume: bool = False


def _new_segment() -> dict[str, Any]:
    return {"content": "", "tool_calls": []}


def _is_recoverable_checkpointer_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        exc.__class__.__name__ == "OperationalError"
        or "could not receive data from server" in text
        or "software caused connection abort" in text
    )


def _friendly_agent_error(exc: Exception) -> str | None:
    """Map agent-loop limit exceptions to friendly Chinese messages; None = keep default."""
    from langgraph.errors import GraphRecursionError
    from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError

    if isinstance(exc, GraphRecursionError):
        return "任务执行步骤过多，可能是 Agent 陷入重复循环。请把任务拆分成更小的步骤重试。"
    if isinstance(exc, ToolCallLimitExceededError):
        return "工具调用次数达到上限，Agent 可能在做无效尝试。请重新表述需求后重试。"
    return None


@router.post("/chat")
async def chat(payload: ChatRequest):
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    if not payload.resume and not payload.message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")

    history_record = session_manager.load_session_record(payload.session_id)
    is_first_user_message = not any(
        message.get("role") == "user"
        for message in history_record.get("messages", [])
    )
    request_context = build_request_context(thread_id=payload.session_id)
    # 对话历史由 checkpointer 管理，不再从 SessionManager 传入
    history_for_agent: list[dict[str, Any]] = []

    async def event_generator():
        retried = False            # checkpointer 断连重试
        resume_fallback = False    # resume → 带上下文重试
        # 局部可变状态（resume 兜底会改这两个值；不重绑 `payload` 闭包变量，避免 UnboundLocalError）
        is_resume = payload.resume
        current_message = payload.message
        while True:
            segments: list[dict[str, Any]] = []
            current_segment = _new_segment()
            emitted_any_event = False
            try:
                # 中途落盘：用户消息在调用 agent 前保存，任何失败都不丢用户输入。
                # resume / 兜底重试时消息已存在或为空，不重复落盘。
                if not is_resume and not resume_fallback:
                    session_manager.save_message(payload.session_id, "user", current_message)

                async for event in agent_manager.astream(
                    current_message, history_for_agent, context=request_context,
                    resume=is_resume,
                ):
                    emitted_any_event = True
                    event_type = event["type"]

                    if event_type == "token":
                        current_segment["content"] += event.get("content", "")
                    elif event_type == "tool_start":
                        current_segment["tool_calls"].append(
                            {
                                "tool": event.get("tool", "tool"),
                                "input": event.get("input", ""),
                                "output": "",
                            }
                        )
                    elif event_type == "tool_end":
                        if current_segment["tool_calls"]:
                            current_segment["tool_calls"][-1]["output"] = event.get("output", "")
                    elif event_type == "new_response":
                        if current_segment["content"].strip() or current_segment["tool_calls"]:
                            segments.append(current_segment)
                        current_segment = _new_segment()
                    elif event_type == "done":
                        if not current_segment["content"].strip() and event.get("content"):
                            current_segment["content"] = event["content"]
                        if current_segment["content"].strip() or current_segment["tool_calls"]:
                            segments.append(current_segment)

                        # 用户消息已在请求开头落盘，这里只落盘 assistant 回复
                        for segment in segments:
                            session_manager.save_message(
                                payload.session_id,
                                "assistant",
                                segment["content"],
                                tool_calls=segment["tool_calls"] or None,
                            )

                    data = {key: value for key, value in event.items() if key != "type"}
                    yield sse_event(event_type, data)

                    if event_type == "done":
                        if is_first_user_message and current_message:
                            title = await agent_manager.generate_title(current_message)
                            session_manager.set_title(payload.session_id, title)
                            yield sse_event(
                                "title",
                                {"session_id": payload.session_id, "title": title},
                            )
                return
            except Exception as exc:
                # 中途失败：落盘已生成的部分回复（标记 interrupted），便于中断后恢复
                if current_segment["content"].strip() or current_segment["tool_calls"]:
                    session_manager.save_message(
                        payload.session_id,
                        "assistant",
                        current_segment["content"],
                        tool_calls=current_segment["tool_calls"] or None,
                        interrupted=True,
                    )
                # resume 失败 → 回退为带上下文重试（agent 基于已保存的 checkpoint 状态继续）
                if is_resume and not resume_fallback:
                    resume_fallback = True
                    logger.warning("resume failed, falling back to context retry: %s", exc)
                    is_resume = False
                    current_message = ""   # 兜底重试：无新消息，agent 基于 checkpoint 状态继续
                    continue
                if (
                    not retried
                    and not emitted_any_event
                    and _is_recoverable_checkpointer_error(exc)
                ):
                    retried = True
                    logger.warning(
                        "checkpointer connection dropped, reconnect and retry once: %s", exc
                    )
                    await reconnect_checkpointer_async()
                    continue

                friendly = _friendly_agent_error(exc)
                if friendly is not None:
                    yield sse_event("error", {"error": friendly})
                    return
                print("[chat] error in event_generator", repr(exc))
                traceback.print_exc()
                yield sse_event("error", {"error": str(exc)})
                return

    if payload.stream:
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    final_text = ""
    async for raw_event in event_generator():
        if raw_event.startswith("event: done"):
            final_text = raw_event
    return JSONResponse({"content": final_text})
