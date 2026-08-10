"""L1 deduplication: exact-content hash + LLM merge-or-store."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..storage.l1_repo import L1Fact, L1Repo, content_hash
from .prompts import DEDUP_SYSTEM, DEDUP_USER

logger = logging.getLogger(__name__)


class L1Deduplicator:
    """Deduplicates new facts against existing L1 facts.

    Two tiers:
    1. Exact-content hash — deterministic, no LLM, drops literal duplicates.
    2. Semantic merge — only when a near-duplicate candidate is found, one LLM
       call decides: merge into the candidate (keeps its fact_id, so the caller
       updates the row) or store as a new fact.
    """

    def __init__(self, l1_repo: L1Repo, llm_fn=None, search_fn=None):
        self._l1 = l1_repo
        self._llm_fn = llm_fn       # async callable: (system, user) -> str
        self._search_fn = search_fn  # async callable: (query, *, top_k) -> list[dict]

    async def dedup(self, new_facts: list[L1Fact]) -> list[L1Fact]:
        """Deduplicate a batch of new facts.

        Returns facts to write: those with ``fact_id`` set should be updated
        (merge), those without should be inserted (store). Exact duplicates
        are dropped.
        """
        if not new_facts:
            return []

        # Tier 1: exact-content duplicates (deterministic, no LLM)
        existing_hashes = self._l1.content_hash_index()
        seen: set[str] = set()
        keep: list[L1Fact] = []
        for fact in new_facts:
            h = content_hash(fact.content)
            if h in existing_hashes or h in seen:
                logger.debug("Skipping exact duplicate: %s", fact.content[:50])
                continue
            seen.add(h)
            keep.append(fact)

        # Tier 2: semantic merge for facts with a near-duplicate candidate
        results: list[L1Fact] = []
        for fact in keep:
            results.append(await self._merge_if_duplicate(fact))

        logger.info("L1 dedup: %d input → %d output", len(new_facts), len(results))
        return results

    async def _merge_if_duplicate(self, fact: L1Fact) -> L1Fact:
        """Ask the LLM whether to merge into a similar fact (or store as new)."""
        if not self._search_fn or not self._llm_fn:
            return fact

        try:
            candidates = await self._search_fn(fact.content, top_k=3)
        except Exception as exc:
            logger.warning("Dedup search failed: %s", exc)
            return fact

        candidates = [
            c for c in (candidates or [])
            if c.get("fact_id") is not None and c.get("content")
        ]
        if not candidates:
            return fact

        existing_lines = [f"- [fact_id={c['fact_id']}] {c['content']}" for c in candidates]
        user_prompt = DEDUP_USER.format(
            new_memory=fact.content,
            new_type=fact.fact_type,
            new_priority=fact.priority,
            existing_memories="\n".join(existing_lines),
        )

        try:
            raw = await self._llm_fn(DEDUP_SYSTEM, user_prompt)
            decision = self._parse_decision(raw)
        except Exception as exc:
            logger.warning("Dedup LLM call failed: %s", exc)
            return fact

        target_id = decision.get("target_fact_id")
        if decision.get("decision") == "merge" and target_id:
            existing = self._l1.get_by_id(target_id)
            if existing:
                fact.fact_id = target_id
                existing_ids = existing.get("source_msg_ids") or []
                fact.source_msg_ids = list(set(existing_ids + (fact.source_msg_ids or [])))
                logger.debug("Merging into fact %d: %s", target_id, fact.content[:50])
        return fact

    def _parse_decision(self, raw: str) -> dict[str, Any]:
        """Parse the LLM merge/store decision JSON."""
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

        decision = data.get("decision", "store")
        if decision not in ("merge", "store"):
            decision = "store"
        data["decision"] = decision

        target_id = data.get("target_fact_id")
        if target_id is not None:
            try:
                data["target_fact_id"] = int(target_id)
            except (ValueError, TypeError):
                data["target_fact_id"] = None
        else:
            data["target_fact_id"] = None

        return data
