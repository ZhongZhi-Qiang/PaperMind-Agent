"""L1 deduplication: LLM-powered dedup against existing facts."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..storage.l1_repo import L1Fact, L1Repo
from .prompts import DEDUP_SYSTEM, DEDUP_USER

logger = logging.getLogger(__name__)


class L1Deduplicator:
    """Deduplicates new facts against existing L1 facts.

    Uses vector/keyword search to find candidates, then LLM to decide:
    store / update / merge / skip.
    """

    def __init__(self, l1_repo: L1Repo, llm_fn=None, search_fn=None):
        self._l1 = l1_repo
        self._llm_fn = llm_fn       # async callable: (system, user) -> str
        self._search_fn = search_fn  # async callable: (query, top_k) -> list[dict]

    async def dedup(self, new_facts: list[L1Fact]) -> list[L1Fact]:
        """Deduplicate a batch of new facts. Returns facts to store/update.

        For each new fact:
        1. Search for similar existing facts
        2. Ask LLM to decide: store/update/merge/skip
        3. Execute the decision

        Returns:
            List of facts that should be written (new + updated + merged).
        """
        if not new_facts:
            return []

        results: list[L1Fact] = []

        for fact in new_facts:
            decision = await self._decide(fact)
            action = decision.get("decision", "store")

            if action == "skip":
                logger.debug("Skipping duplicate: %s", fact.content[:50])
                continue

            elif action == "store":
                results.append(fact)

            elif action == "update":
                target_id = decision.get("target_fact_id")
                new_content = decision.get("content", fact.content)
                if target_id:
                    existing = self._l1.get_by_id(target_id)
                    if existing:
                        fact.fact_id = target_id
                        fact.content = new_content
                        fact.updated_at = None  # will be set by repo
                        results.append(fact)
                        logger.debug("Updating fact %d: %s", target_id, new_content[:50])
                        continue
                # Fallback: store as new
                results.append(fact)

            elif action == "merge":
                target_id = decision.get("target_fact_id")
                merged_content = decision.get("content", fact.content)
                if target_id:
                    existing = self._l1.get_by_id(target_id)
                    if existing:
                        fact.fact_id = target_id
                        fact.content = merged_content
                        # Merge source message IDs
                        existing_ids = existing.get("source_msg_ids") or []
                        fact.source_msg_ids = list(set(existing_ids + fact.source_msg_ids))
                        results.append(fact)
                        logger.debug("Merging into fact %d: %s", target_id, merged_content[:50])
                        continue
                results.append(fact)

            else:
                results.append(fact)

        logger.info("L1 dedup: %d input → %d output", len(new_facts), len(results))
        return results

    async def _decide(self, fact: L1Fact) -> dict[str, Any]:
        """Search for similar facts and ask LLM for dedup decision."""
        if not self._search_fn or not self._llm_fn:
            return {"decision": "store"}

        # Find similar existing facts
        try:
            candidates = await self._search_fn(fact.content, top_k=5)
        except Exception as exc:
            logger.warning("Dedup search failed: %s", exc)
            return {"decision": "store"}

        if not candidates:
            return {"decision": "store"}

        # Format candidates for LLM
        existing_lines = []
        for c in candidates:
            existing_lines.append(
                f"- [fact_id={c.get('fact_id')}] {c.get('content', '')}"
            )
        existing_text = "\n".join(existing_lines)

        user_prompt = DEDUP_USER.format(
            new_memory=fact.content,
            new_type=fact.fact_type,
            new_priority=fact.priority,
            existing_memories=existing_text,
        )

        try:
            raw = await self._llm_fn(DEDUP_SYSTEM, user_prompt)
            return self._parse_decision(raw)
        except Exception as exc:
            logger.warning("Dedup LLM call failed: %s", exc)
            return {"decision": "store"}

    def _parse_decision(self, raw: str) -> dict[str, Any]:
        """Parse LLM dedup decision JSON."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return {"decision": "store"}
            else:
                return {"decision": "store"}

        if not isinstance(data, dict):
            return {"decision": "store"}

        # Normalize
        decision = data.get("decision", "store")
        if decision not in ("store", "update", "merge", "skip"):
            decision = "store"
        data["decision"] = decision

        # Parse target_fact_id
        target_id = data.get("target_fact_id")
        if target_id is not None:
            try:
                data["target_fact_id"] = int(target_id)
            except (ValueError, TypeError):
                data["target_fact_id"] = None

        return data
