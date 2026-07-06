"""Harness Review Middleware: auto-review conversations after agent completes.

Implements after_agent to:
1. Collect conversation data
2. Call LLM for quality review
3. Write review to session metadata
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field
from typing_extensions import override

logger = logging.getLogger(__name__)


def _extract_text(content: Any) -> str:
    """Extract plain text from a content field that may be a string or list-of-dicts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "".join(parts)
    return str(content) if content else ""


REVIEW_SYSTEM_PROMPT = """You are a conversation quality reviewer. Analyze the conversation and evaluate:

1. quality_score (1-10): accuracy and completeness of the answer
2. hallucination_risk (low/medium/high): unsupported assertions
3. tool_audit: whether tool choices and parameters were appropriate
4. issues: specific problems found (type: tool_misuse/inaccuracy/hallucination/omission, severity: low/medium/high)
5. summary: one-sentence summary

Respond with JSON only, using exactly these field names:
{
  "quality_score": <int 1-10>,
  "hallucination_risk": "low"|"medium"|"high",
  "issues": [{"type": "...", "description": "...", "severity": "..."}],
  "tool_audit": {"total_calls": <int>, "appropriate": <int>, "flagged": <int>, "details": [{"tool": "...", "appropriate": true/false, "reason": "..."}]},
  "summary": "..."
}"""


class ReviewIssue(BaseModel):
    """审查发现的问题。"""
    type: Literal["tool_misuse", "inaccuracy", "hallucination", "omission"] = Field(description="问题类型")
    description: str = Field(description="问题描述")
    severity: Literal["low", "medium", "high"] = Field(description="严重程度")


class ToolAuditDetail(BaseModel):
    """单个工具调用的审计结果。"""
    tool: str = Field(description="工具名称")
    appropriate: bool = Field(description="调用是否合理")
    reason: str = Field(default="", description="判定理由")


class ToolAudit(BaseModel):
    """工具使用审计汇总。"""
    total_calls: int = Field(default=0, description="工具调用总次数")
    appropriate: int = Field(default=0, description="合理调用次数")
    flagged: int = Field(default=0, description="问题调用次数")
    details: list[ToolAuditDetail] = Field(default_factory=list, description="每次调用的审计详情")


class ReviewOutput(BaseModel):
    """审查报告结构化输出 — enforced via Function Calling."""
    quality_score: int = Field(ge=1, le=10, description="回答质量评分，1-10")
    hallucination_risk: Literal["low", "medium", "high"] = Field(description="幻觉风险等级")
    issues: list[ReviewIssue] = Field(default_factory=list, description="发现的问题列表")
    tool_audit: ToolAudit = Field(default_factory=ToolAudit, description="工具使用审计")
    summary: str = Field(default="", description="一句话总结")


def _to_int(val: Any, default: int = 0) -> int:
    """Coerce to int, returning default on failure."""
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_review_json(raw: Any) -> dict[str, Any]:
    """Parse LLM output into a review dict, tolerant of schema deviations."""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"Review LLM returned non-JSON text: {text[:200]}")
    if not isinstance(raw, dict):
        raise ValueError(f"Review LLM returned unexpected type: {type(raw)}")

    # quality_score: extract from various field names, coerce to int
    quality_score = _to_int(
        raw.get("quality_score")
        or raw.get("quality")
        or raw.get("score")
        or raw.get("qualityScore"),
        default=5,
    )
    quality_score = max(1, min(10, quality_score))

    # hallucination_risk: normalize
    risk = str(
        raw.get("hallucination_risk")
        or raw.get("hallucination")
        or raw.get("risk")
        or "medium"
    ).strip().lower()
    if risk not in ("low", "medium", "high"):
        risk = "medium"

    # issues: ensure list
    issues_raw = raw.get("issues") or raw.get("problems") or []
    if isinstance(issues_raw, str):
        issues_raw = [{"type": "inaccuracy", "description": issues_raw, "severity": "medium"}]
    issues = []
    for item in issues_raw if isinstance(issues_raw, list) else []:
        if isinstance(item, dict):
            issues.append({
                "type": item.get("type", "inaccuracy"),
                "description": item.get("description", str(item)),
                "severity": item.get("severity", "medium"),
            })

    # tool_audit: ensure dict with correct structure
    audit_raw = raw.get("tool_audit") or raw.get("tool_use") or {}
    if isinstance(audit_raw, str):
        audit_raw = {"total_calls": 0, "appropriate": 0, "flagged": 0, "details": [
            {"tool": "unknown", "appropriate": True, "reason": audit_raw}
        ]}
    if not isinstance(audit_raw, dict):
        audit_raw = {}

    details_raw = audit_raw.get("details") or []
    if isinstance(details_raw, str):
        details_raw = [{"tool": "unknown", "appropriate": True, "reason": details_raw}]
    details = []
    for d in details_raw if isinstance(details_raw, list) else []:
        if isinstance(d, dict):
            details.append({
                "tool": d.get("tool", "unknown"),
                "appropriate": bool(d.get("appropriate", True)),
                "reason": d.get("reason", ""),
            })

    tool_audit = {
        "total_calls": _to_int(audit_raw.get("total_calls"), 0),
        "appropriate": _to_int(audit_raw.get("appropriate"), 0),
        "flagged": _to_int(audit_raw.get("flagged"), 0),
        "details": details,
    }

    summary = str(raw.get("summary") or raw.get("conclusion") or "")

    return {
        "quality_score": quality_score,
        "hallucination_risk": risk,
        "issues": issues,
        "tool_audit": tool_audit,
        "summary": summary,
    }


def build_review_prompt(messages: list[dict[str, Any]]) -> str:
    """Build the review prompt from conversation messages."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                lines.append(
                    f"[工具调用] {tc.get('name', 'unknown')}: "
                    f"{json.dumps(tc.get('args', {}), ensure_ascii=False)}"
                )
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


class HarnessReviewMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Reviews conversation quality after agent completes."""

    def __init__(self, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def _get_llm(self) -> Any:
        if self._llm:
            return self._llm
        from config import get_settings
        from graph.llm import build_llm_config_from_settings, get_llm
        settings = get_settings()
        config = build_llm_config_from_settings(settings, temperature=0.0, streaming=False)
        return get_llm(config)

    def _do_review(self, state: AgentState[ResponseT]) -> dict[str, Any] | None:
        from config import get_settings
        if not get_settings().harness_review_enabled:
            return None

        messages = state.get("messages") or []
        if len(messages) < 2:
            return None

        msg_dicts = []
        for m in messages:
            msg_dict: dict[str, Any] = {"role": getattr(m, "type", "unknown")}
            content = getattr(m, "content", "")
            msg_dict["content"] = _extract_text(content)
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                msg_dict["tool_calls"] = tool_calls
            msg_dicts.append(msg_dict)

        prompt_text = build_review_prompt(msg_dicts)

        try:
            llm = self._get_llm()
            response = llm.invoke([
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_text},
            ])
            raw_content = response.content if hasattr(response, "content") else str(response)
            review = _parse_review_json(raw_content)
            logger.info("Harness review completed: quality=%s", review.get("quality_score", "N/A"))
            return review
        except Exception as e:
            logger.warning("Harness review failed: %s", e, exc_info=True)
            return None

    @override
    def after_agent(self, state: AgentState[ResponseT], runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        # Sync path — only used when HARNESS_REVIEW_SYNC=true (debugging) or when LLM is fast/local
        result = self._do_review(state)
        if result:
            return {"harness_review": result}
        return None

    @override
    async def aafter_agent(self, state: AgentState[ResponseT], runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        from config import get_settings
        if get_settings().harness_review_sync:
            # Force sync: block until review finishes (debugging only)
            result = await asyncio.to_thread(self._do_review, state)
            return {"harness_review": result} if result else None

        # Default: fire-and-forget — don't block the `done` event on review LLM call.
        # Review result is logged but no longer written back to agent state (it was not consumed
        # downstream anyway). This shaves one full LLM round-trip off perceived latency.
        asyncio.create_task(asyncio.to_thread(self._do_review, state))
        return None


def build_harness_review_middleware(llm: Any = None) -> HarnessReviewMiddleware:
    """Factory for HarnessReviewMiddleware."""
    return HarnessReviewMiddleware(llm=llm)
