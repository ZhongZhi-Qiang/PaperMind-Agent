"""Symbolic Short-Term Memory pipeline orchestrator.

Implements the four-stage pipeline:
- L1: LLM-based tool result summarization with replaceability scores
- L1.5: Task lifecycle judgment (complete/continuing/new)
- L2: LLM-generated semantic Mermaid flowcharts
- L3: Progressive context compression by score

Ported from TencentDB-Agent-Memory with adaptations for LangChain/LangGraph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .types import (
    DEFAULTS,
    L2Response,
    MmdReplaceBlock,
    OffloadEntry,
    PluginState,
    TaskJudgment,
    ToolPair,
    _now_china_iso,
)
from .storage import OffloadStorage
from .graph_builder import generate_ref_id

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response (strip markdown fences, etc.)."""
    text = text.strip()
    # Remove ```json ... ``` fences
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try to find raw JSON array or object
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start >= 0 and end > start:
            return text[start:end + 1]
    return text


def _parse_json_safe(text: str) -> Any | None:
    """Parse JSON from LLM response, with tolerance."""
    extracted = _extract_json(text)
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        # Try fixing common issues
        try:
            # Remove trailing commas before } or ]
            fixed = re.sub(r",\s*([\]}])", r"\1", extracted)
            return json.loads(fixed)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from LLM response: %s", extracted[:200])
            return None


def _format_recent_messages(messages: list[Any], max_count: int = 6) -> str:
    """Format recent messages for prompt context."""
    recent = messages[-max_count:] if len(messages) > max_count else messages
    parts = []
    for msg in recent:
        role = getattr(msg, "type", None) or getattr(msg, "role", "unknown")
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        if isinstance(content, str) and len(content) > 500:
            content = content[:500] + "..."
        parts.append(f"[{role}]: {content}")
    return "\n".join(parts)


class OffloadPipeline:
    """Core pipeline orchestrator for the Symbolic Short-Term Memory.

    Manages the L1/L1.5/L2/L3 stages and coordinates storage.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        storage: OffloadStorage,
        config: dict[str, Any] | None = None,
    ):
        self._llm = llm
        self._storage = storage
        self._config = {**DEFAULTS, **(config or {})}

        # In-memory buffer for pending tool pairs
        self._pending_pairs: list[ToolPair] = []
        # Offload entries not yet assigned to a node
        self._null_entries: list[OffloadEntry] = []
        # Task label for current MMD
        self._current_task_label: str | None = None
        # Cached recent messages for prompt context
        self._recent_messages: list[Any] = []
        # L1.5 settled flag
        self._l15_settled: bool = False
        # Background task tracking
        self._l1_task: asyncio.Task | None = None
        self._l2_task: asyncio.Task | None = None

        # Load persisted state
        self._state = self._storage.read_state()
        self._storage.ensure_dirs()

    @property
    def storage(self) -> OffloadStorage:
        return self._storage

    @property
    def state(self) -> PluginState:
        return self._state

    def set_recent_messages(self, messages: list[Any]) -> None:
        """Update the cached recent messages for prompt context."""
        self._recent_messages = messages

    # ─── L1: Summarization ────────────────────────────────────────────────

    def add_tool_pair(self, pair: ToolPair) -> None:
        """Add a tool pair to the pending buffer."""
        self._pending_pairs.append(pair)

        # Size-based trigger: single huge output → L1 immediately
        size_threshold = self._config.get("l1_size_threshold", 3000)
        result_len = len(str(pair.result)) if pair.result else 0
        if result_len >= size_threshold:
            self._trigger_l1()
            return

        # Count-based trigger: buffer full → L1
        threshold = self._config.get("force_trigger_threshold", 4)
        if len(self._pending_pairs) >= threshold:
            self._trigger_l1()

    def _trigger_l1(self) -> None:
        """Trigger L1 summarization (runs in background)."""
        if self._l1_task and not self._l1_task.done():
            logger.debug("L1 already running, skipping")
            return

        pairs = self._pending_pairs[:self._config.get("max_pairs_per_batch", 20)]
        self._pending_pairs = self._pending_pairs[len(pairs):]

        if not pairs:
            return

        self._l1_task = asyncio.create_task(self._run_l1(pairs))

    async def _run_l1(self, pairs: list[ToolPair]) -> list[OffloadEntry]:
        """Execute L1 summarization via LLM."""
        from .prompts.l1_prompt import L1_SYSTEM_PROMPT, build_l1_user_prompt

        pair_dicts = [
            {
                "tool_name": p.tool_name,
                "tool_call_id": p.tool_call_id,
                "params": p.params,
                "result": str(p.result)[:2000] if p.result else "",
                "timestamp": p.timestamp,
            }
            for p in pairs
        ]

        recent = _format_recent_messages(self._recent_messages)
        user_prompt = build_l1_user_prompt(recent, pair_dicts)

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=L1_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content if isinstance(response.content, str) else str(response.content)
            parsed = _parse_json_safe(raw)

            if not isinstance(parsed, list):
                logger.warning("L1: expected JSON array, got %s", type(parsed).__name__)
                return self._fallback_l1(pairs)

            entries = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                entry = OffloadEntry(
                    timestamp=item.get("timestamp", _now_china_iso()),
                    tool_call=item.get("tool_call", ""),
                    summary=item.get("summary", ""),
                    tool_call_id=item.get("tool_call_id", ""),
                    score=item.get("score"),
                )
                # Store full result as ref
                for p in pairs:
                    if p.tool_call_id == entry.tool_call_id:
                        ref_id = generate_ref_id(p.tool_name, str(p.result))
                        entry.result_ref = self._storage.write_ref(
                            ref_id, p.tool_name, str(p.result)
                        )
                        break
                entries.append(entry)

            # Persist to JSONL
            self._storage.append_entries(entries)
            self._null_entries.extend(entries)
            logger.info("L1: produced %d entries from %d pairs", len(entries), len(pairs))

            # Check if L2 should be triggered
            self._check_l2_trigger()

            return entries

        except Exception as exc:
            logger.error("L1 LLM call failed: %s", exc)
            return self._fallback_l1(pairs)

    def _fallback_l1(self, pairs: list[ToolPair]) -> list[OffloadEntry]:
        """Fallback: create entries with mechanical summaries when LLM fails."""
        entries = []
        for p in pairs:
            result_str = str(p.result) if p.result else ""
            summary = result_str[:150] + "..." if len(result_str) > 150 else result_str
            ref_id = generate_ref_id(p.tool_name, result_str)
            ref_path = self._storage.write_ref(ref_id, p.tool_name, result_str)

            entries.append(OffloadEntry(
                timestamp=p.timestamp,
                tool_call=f"{p.tool_name}({json.dumps(p.params, ensure_ascii=False)[:80]})",
                summary=summary,
                tool_call_id=p.tool_call_id,
                result_ref=ref_path,
                score=5,
            ))

        self._storage.append_entries(entries)
        self._null_entries.extend(entries)
        logger.info("L1 fallback: produced %d entries", len(entries))
        return entries

    def flush_pending(self) -> None:
        """Force-trigger L1 for any remaining pending pairs."""
        if self._pending_pairs:
            self._trigger_l1()

    # ─── L1.5: Task Judgment ──────────────────────────────────────────────

    async def run_l15(self) -> TaskJudgment:
        """Execute L1.5 task lifecycle judgment."""
        from .prompts.l15_prompt import L15_SYSTEM_PROMPT, build_l15_user_prompt

        # Gather current MMD
        current_mmd = None
        if self._state.active_mmd_file:
            content = self._storage.read_mmd(self._state.active_mmd_file)
            if content:
                current_mmd = {
                    "filename": self._state.active_mmd_file,
                    "content": content,
                    "path": f"mmds/{self._state.active_mmd_file}",
                }

        # Gather available MMDs metadata
        available_mmds = self._gather_mmd_metas()

        recent = _format_recent_messages(self._recent_messages)
        user_prompt = build_l15_user_prompt(recent, current_mmd, available_mmds)

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=L15_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content if isinstance(response.content, str) else str(response.content)
            parsed = _parse_json_safe(raw)

            if not isinstance(parsed, dict):
                logger.warning("L1.5: expected JSON object, got %s", type(parsed).__name__)
                return TaskJudgment()

            judgment = TaskJudgment(
                task_completed=parsed.get("taskCompleted", False),
                is_continuation=parsed.get("isContinuation", False),
                continuation_mmd_file=parsed.get("continuationMmdFile"),
                new_task_label=parsed.get("newTaskLabel"),
                is_long_task=parsed.get("isLongTask", False),
            )

            # Apply judgment to MMD lifecycle
            self._apply_task_judgment(judgment)
            self._l15_settled = True
            logger.info(
                "L1.5: completed=%s, long=%s, continuation=%s, label=%s",
                judgment.task_completed, judgment.is_long_task,
                judgment.is_continuation, judgment.new_task_label,
            )
            return judgment

        except Exception as exc:
            logger.error("L1.5 LLM call failed: %s", exc)
            return TaskJudgment()

    def _apply_task_judgment(self, judgment: TaskJudgment) -> None:
        """Apply L1.5 judgment to manage MMD file lifecycle."""
        if judgment.task_completed and not judgment.is_long_task:
            # Task completed, no new long task — clear active MMD
            self._state.active_mmd_file = None
            self._state.active_mmd_id = None
            self._l15_settled = False
        elif judgment.task_completed and judgment.is_continuation and judgment.continuation_mmd_file:
            # Reactivate historical MMD
            self._state.active_mmd_file = judgment.continuation_mmd_file
            self._state.active_mmd_id = judgment.continuation_mmd_file.replace(".mmd", "")
        elif judgment.task_completed and judgment.is_long_task and judgment.new_task_label:
            # Create new MMD file
            self._state.mmd_counter += 1
            filename = f"{judgment.new_task_label}-{self._state.mmd_counter:03d}.mmd"
            self._state.active_mmd_file = filename
            self._state.active_mmd_id = judgment.new_task_label
            self._current_task_label = judgment.new_task_label
        elif not judgment.task_completed:
            # Continue current task
            pass

        self._storage.write_state(self._state)

    def _gather_mmd_metas(self) -> list[dict[str, Any]]:
        """Gather metadata from all available MMD files."""
        metas = []
        for filename in self._storage.list_mmds():
            if filename == self._state.active_mmd_file:
                continue
            content = self._storage.read_mmd(filename)
            if not content:
                continue

            meta: dict[str, Any] = {"filename": filename, "path": f"mmds/{filename}"}

            # Parse metadata from %%{ ... }%%
            m = re.search(r'%%\{\s*(.*?)\s*\}%%', content, re.DOTALL)
            if m:
                try:
                    meta_json = json.loads("{" + m.group(1) + "}")
                    meta["taskGoal"] = meta_json.get("taskGoal", "")
                    meta["updatedTime"] = meta_json.get("updatedTime")
                except json.JSONDecodeError:
                    meta["taskGoal"] = ""
            else:
                meta["taskGoal"] = ""

            # Count node statuses
            meta["doneCount"] = len(re.findall(r"status:\s*done", content))
            meta["doingCount"] = len(re.findall(r"status:\s*doing", content))
            meta["todoCount"] = len(re.findall(r"status:\s*(?:todo|paused)", content))

            metas.append(meta)

        return metas

    # ─── L2: Mermaid Generation ───────────────────────────────────────────

    def _check_l2_trigger(self) -> None:
        """Check if L2 should be triggered."""
        null_threshold = self._config.get("l2_null_threshold", 4)
        if len(self._null_entries) >= null_threshold:
            self._trigger_l2()

    def _trigger_l2(self) -> None:
        """Trigger L2 Mermaid generation (runs in background)."""
        if self._l2_task and not self._l2_task.done():
            logger.debug("L2 already running, skipping")
            return

        threshold = self._config.get("l2_null_threshold", 4)
        entries = self._null_entries[:threshold]
        self._null_entries = self._null_entries[len(entries):]

        if not entries:
            return

        self._l2_task = asyncio.create_task(self._run_l2(entries))

    async def _run_l2(self, entries: list[OffloadEntry]) -> None:
        """Execute L2 Mermaid generation via LLM."""
        from .prompts.l2_prompt import L2_SYSTEM_PROMPT, build_l2_user_prompt

        if not self._state.active_mmd_file:
            logger.debug("L2: no active MMD file, skipping")
            return

        existing_mmd = self._storage.read_mmd(self._state.active_mmd_file)
        char_count = len(existing_mmd) if existing_mmd else 0

        entry_dicts = [
            {
                "toolCallId": e.tool_call_id,
                "toolCall": e.tool_call,
                "summary": e.summary,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

        recent = _format_recent_messages(self._recent_messages, max_count=4)
        current_turn = ""
        if self._recent_messages:
            last = self._recent_messages[-1]
            content = getattr(last, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            current_turn = str(content)[:500]

        task_label = self._current_task_label or "task"
        mmd_prefix = self._state.active_mmd_id or task_label

        user_prompt = build_l2_user_prompt(
            existing_mmd=existing_mmd,
            entries=entry_dicts,
            recent_history=recent,
            current_turn=current_turn,
            task_label=task_label,
            mmd_prefix=mmd_prefix,
            char_count=char_count,
        )

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=L2_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content if isinstance(response.content, str) else str(response.content)
            parsed = _parse_json_safe(raw)

            if not isinstance(parsed, dict):
                logger.warning("L2: expected JSON object, got %s", type(parsed).__name__)
                return

            l2_resp = L2Response(
                file_action=parsed.get("file_action", "write"),
                mmd_content=parsed.get("mmd_content"),
                replace_blocks=[
                    MmdReplaceBlock(
                        start_line=b.get("start_line", 0),
                        end_line=b.get("end_line", 0),
                        content=b.get("content", ""),
                    )
                    for b in parsed.get("replace_blocks", [])
                ],
                node_mapping=parsed.get("node_mapping", {}),
            )

            # Apply L2 result
            if l2_resp.file_action == "write" and l2_resp.mmd_content:
                # Strip markdown fences if present
                mmd_text = l2_resp.mmd_content
                if mmd_text.startswith("```mermaid"):
                    mmd_text = mmd_text[len("```mermaid"):].strip()
                if mmd_text.endswith("```"):
                    mmd_text = mmd_text[:-3].strip()
                self._storage.write_mmd(self._state.active_mmd_file, mmd_text)
            elif l2_resp.file_action == "replace" and l2_resp.replace_blocks:
                blocks = [
                    {"start_line": b.start_line, "end_line": b.end_line, "content": b.content}
                    for b in l2_resp.replace_blocks
                ]
                self._storage.patch_mmd(self._state.active_mmd_file, blocks)

            # Update node_ids in JSONL
            if l2_resp.node_mapping:
                self._storage.update_node_ids(l2_resp.node_mapping)

            # Update state
            self._state.last_l2_trigger_time = _now_china_iso()
            self._storage.write_state(self._state)

            logger.info(
                "L2: action=%s, mapped %d entries to %d nodes",
                l2_resp.file_action, len(entries), len(l2_resp.node_mapping),
            )

        except Exception as exc:
            logger.error("L2 LLM call failed: %s", exc)
            # Re-add entries to null list for retry
            self._null_entries = entries + self._null_entries

    # ─── L3: Progressive Compression ─────────────────────────────────────

    def get_compressed_result(
        self,
        tool_name: str,
        output: str,
        call_id: str,
    ) -> str:
        """Get a compressed representation of a tool result.

        Used by wrap_tool_call to provide immediate compression.
        Checks if we have an L1 summary for this call_id; if so, returns
        a plain text summary. Otherwise, falls back to mechanical truncation.

        Args:
            tool_name: Name of the tool
            output: Full tool output
            call_id: Tool call ID

        Returns:
            Compressed string (plain text summary + result_ref)
        """
        if len(output) <= self._config.get("offload_threshold", 500):
            return output

        # Store full result
        ref_id = generate_ref_id(tool_name, output)
        ref_path = self._storage.write_ref(ref_id, tool_name, output)

        # Check if we have an L1 summary
        summary = self._find_summary_for_call(call_id)

        if summary:
            # Use L1 summary with node_id from L2 mapping
            node_id = self._find_node_id_for_call(call_id)
            compressed = (
                f"[Offloaded Tool Result | node: {node_id or 'N/A'}]\n"
                f"Summary: {summary}\n"
                f"result_ref: {ref_path} (read this file for full tool call and raw result)"
            )
        else:
            # Mechanical fallback
            length = len(output)
            if length <= 2000:
                lines = output.strip().split("\n")
                key_lines = lines[:3] + (["..."] if len(lines) > 3 else []) + lines[-2:]
                preview = "\n".join(key_lines[:5])
            elif length <= 5000:
                lines = output.strip().split("\n")
                preview = lines[0][:80] + "\n...\n" + lines[-1][:80]
            else:
                preview = output[:200] + f"\n... [{length - 400} chars omitted] ...\n" + output[-200:]

            compressed = (
                f"[Offloaded Tool Result | node: N/A]\n"
                f"Summary: [{tool_name}] {preview[:150]}\n"
                f"result_ref: {ref_path} (read this file for full tool call and raw result)"
            )

        return compressed

    def _find_summary_for_call(self, call_id: str) -> str | None:
        """Find L1 summary for a given tool_call_id."""
        entries = self._storage.read_entries()
        for e in entries:
            if e.tool_call_id == call_id and e.summary:
                return e.summary
        return None

    def _find_node_id_for_call(self, call_id: str) -> str | None:
        """Find L2 node_id for a given tool_call_id."""
        entries = self._storage.read_entries()
        for e in entries:
            if e.tool_call_id == call_id and e.node_id:
                return e.node_id
        return None

    def compress_messages_by_score(
        self,
        messages: list[Any],
        context_ratio: float,
    ) -> tuple[list[Any], int]:
        """L3: 2D-progressive compression — never deletes or truncates.

        Both score (replaceability) and length (token savings) slide with
        pressure. A long result with a moderate score saves more tokens
        than a short result with a perfect score — both matter.

        At 50%:  score ≥ 8, length > 300  (conservative: only sure wins)
        At 70%:  score ≥ 5, length > 175
        At 90%:  score ≥ 2, length > 50   (aggressive: grab any win)

        Messages are processed in priority order: score * log10(length)
        desc — biggest wins first. No message is ever deleted.

        Args:
            messages: Current message list
            context_ratio: Current context utilization ratio (0-1)

        Returns:
            (modified_messages, compressed_count)
        """
        import math as _math

        mild_start = self._config.get("mild_offload_ratio", 0.5)
        max_ratio = self._config.get("max_pressure_ratio", 0.90)

        if context_ratio < mild_start:
            return messages, 0

        entries = self._storage.read_entries()
        score_map = {e.tool_call_id: e for e in entries if e.score is not None}

        # ── 2D progressive thresholds ─────────────────────────────────
        # Both axes slide linearly with pressure.
        t = min(1.0, max(0.0, (context_ratio - mild_start) / (max_ratio - mild_start)))
        min_score = max(2, int(8 - t * 6))       # t=0→8, t=1→2
        min_length = max(50, int(300 - t * 250))  # t=0→300, t=1→50

        compressed = 0
        new_messages = list(messages)

        from langchain_core.messages import ToolMessage

        # ── Build candidate list with combined priority ───────────────
        # Priority = score * log10(length) — rewards both replaceability
        # and token savings in one score.
        candidates: list[tuple[float, Any, int]] = []  # (priority, entry, msg_idx)
        for entry in score_map.values():
            if entry.score is None or entry.score < min_score:
                continue
            for i, msg in enumerate(new_messages):
                if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", "") == entry.tool_call_id:
                    content = _stringify_content(msg.content)
                    L = len(content)
                    if L > min_length and "[Offloaded Tool Result" not in content:
                        priority = entry.score * _math.log10(max(L, 1))
                        candidates.append((priority, entry, i))
                    break

        # Sort by priority descending — biggest wins first
        candidates.sort(key=lambda c: c[0], reverse=True)

        for _priority, entry, idx in candidates:
            content = _stringify_content(new_messages[idx].content)
            new_messages[idx] = ToolMessage(
                content=(
                    f"[Offloaded Tool Result | node: {entry.node_id or 'N/A'}]\n"
                    f"Summary: {entry.summary}\n"
                    f"result_ref: {entry.result_ref} (read this file for full tool call and raw result)"
                ),
                tool_call_id=getattr(new_messages[idx], "tool_call_id", ""),
                name=getattr(new_messages[idx], "name", ""),
                id=getattr(new_messages[idx], "id", None),
            )
            compressed += 1

        if compressed > 0:
            logger.info(
                "L3: compressed %d tool results (ratio=%.2f, t=%.2f, "
                "min_score=%d, min_length=%d)",
                compressed, context_ratio, t, min_score, min_length,
            )

        return new_messages, compressed


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
