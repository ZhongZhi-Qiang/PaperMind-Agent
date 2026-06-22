"""L1 memory extraction: LLM-powered extraction of atomic facts from conversation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..storage.l0_file_repo import L0FileRepo
from ..storage.l1_repo import L1Fact, L1Repo
from .prompts import EXTRACT_SYSTEM, EXTRACT_USER

logger = logging.getLogger(__name__)


@dataclass
class SceneSegment:
    scene_name: str = ""
    message_ids: list[int] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)


class L1Extractor:
    """Extracts structured memories from buffered conversation messages.

    Reads L0 messages, calls LLM to extract SceneSegment[], and writes L1 facts.
    """

    def __init__(self, l0_repo: L0FileRepo, l1_repo: L1Repo, llm_fn=None):
        self._l0 = l0_repo
        self._l1 = l1_repo
        self._llm_fn = llm_fn  # async callable: (system, user) -> str

    async def extract(
        self,
        session_id: str,
        message_ids: list[int],
        *,
        background_ids: list[int] | None = None,
    ) -> list[L1Fact]:
        """Extract memories from the given message IDs.

        Args:
            session_id: Current session identifier
            message_ids: IDs of new messages to extract from
            background_ids: Optional older message IDs for context

        Returns:
            List of extracted L1Fact objects (not yet deduplicated)
        """
        if not message_ids:
            return []

        # Fetch new messages
        new_msgs = self._l0.get_by_ids(message_ids, session_id=session_id)
        if not new_msgs:
            return []

        # Fetch background context if available
        background_section = ""
        if background_ids:
            bg_msgs = self._l0.get_by_ids(background_ids, session_id=session_id)
            if bg_msgs:
                bg_lines = [f"[{m['role']}] {m['content'][:200]}" for m in bg_msgs[-10:]]
                background_section = "Previous context:\n" + "\n".join(bg_lines)

        # Format new messages
        new_lines = []
        for m in new_msgs:
            new_lines.append(f"[{m['role']}] {m['content']}")
        new_messages = "\n".join(new_lines)

        # Call LLM
        if not self._llm_fn:
            logger.warning("No LLM function provided, skipping L1 extraction")
            return []

        user_prompt = EXTRACT_USER.format(
            background_section=background_section,
            new_messages=new_messages,
        )

        try:
            raw_response = await self._llm_fn(EXTRACT_SYSTEM, user_prompt)
        except Exception as exc:
            logger.error("L1 extraction LLM call failed: %s", exc)
            return []

        # Parse response
        segments = self._parse_segments(raw_response)
        if not segments:
            logger.debug("No segments extracted from session %s", session_id)
            return []

        # Convert to L1Fact objects
        facts: list[L1Fact] = []
        for seg in segments:
            for mem in seg.memories:
                fact = L1Fact(
                    content=mem.get("content", ""),
                    fact_type=mem.get("type", "episodic"),
                    priority=mem.get("priority", 0),
                    scene_name=seg.scene_name,
                    source_msg_ids=seg.message_ids,
                    session_id=session_id,
                )
                if fact.content:
                    facts.append(fact)

        logger.info("L1 extracted %d facts from %d segments (session=%s)", len(facts), len(segments), session_id)
        return facts

    def _parse_segments(self, raw: str) -> list[SceneSegment]:
        """Parse LLM JSON response into SceneSegment objects."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON array in the text
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    logger.warning("Failed to parse L1 extraction response as JSON")
                    return []
            else:
                logger.warning("No JSON array found in L1 extraction response")
                return []

        if not isinstance(data, list):
            return []

        segments = []
        for item in data:
            if not isinstance(item, dict):
                continue
            seg = SceneSegment(
                scene_name=item.get("scene_name", "general"),
                message_ids=item.get("message_ids", []),
                memories=item.get("memories", []),
            )
            segments.append(seg)

        return segments
