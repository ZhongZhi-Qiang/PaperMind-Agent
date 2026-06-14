"""Unified recall service: hybrid retrieval across L1/L2/L3."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from ..config import MemoryV3Config, get_memory_v3_config
from ..storage.l1_repo import L1Repo
from ..storage.l2_repo import L2Repo
from ..storage.l2_repo import KVRepo
from .dense import DenseRetriever
from .keyword import KeywordRetriever
from .fusion import rrf_fusion, weighted_sum_fusion

logger = logging.getLogger(__name__)

EmbeddingFn = Callable[[str], Awaitable[list[float]]]


class RecallService:
    """Unified memory recall: L1 hybrid search + L2 scene navigation + L3 persona."""

    def __init__(
        self,
        l1_repo: L1Repo,
        l2_repo: L2Repo,
        kv_repo: KVRepo,
        embedding_fn: EmbeddingFn,
        config: MemoryV3Config | None = None,
    ):
        self._config = config or get_memory_v3_config()
        self._dense = DenseRetriever(l1_repo)
        self._keyword = KeywordRetriever(l1_repo)
        self._l2 = l2_repo
        self._kv = kv_repo
        self._embedding_fn = embedding_fn

    async def recall(
        self,
        query: str,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> dict[str, Any]:
        """Full recall: L1 facts + L2 scene navigation + L3 persona.

        Returns:
            {
                "l1_facts": [...],        # relevant atomic facts
                "l2_scenes": [...],       # scene navigation links
                "l3_persona": str | None, # user persona markdown
            }
        """
        cfg = self._config

        # Run L1 search and L2/L3 fetch in parallel
        l1_task = self._recall_l1(query, fact_type=fact_type, scene_name=scene_name)
        l2_task = self._recall_l2()
        l3_task = self._recall_l3()

        l1_facts, l2_scenes, l3_persona = await asyncio.gather(
            l1_task, l2_task, l3_task
        )

        return {
            "l1_facts": l1_facts,
            "l2_scenes": l2_scenes,
            "l3_persona": l3_persona,
        }

    async def _recall_l1(
        self,
        query: str,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """L1 hybrid search with configurable strategy."""
        cfg = self._config
        strategy = cfg.recall_strategy

        # Compute query embedding if needed
        query_embedding: list[float] | None = None
        if strategy in ("hybrid", "embedding"):
            try:
                query_embedding = await self._embedding_fn(query)
            except Exception as exc:
                logger.warning("Embedding computation failed: %s", exc)
                if strategy == "embedding":
                    return []

        # Execute searches based on strategy
        if strategy == "hybrid":
            if query_embedding:
                dense_task = self._dense.search(
                    query_embedding, top_k=cfg.dense_top_k,
                    fact_type=fact_type, scene_name=scene_name,
                )
                keyword_task = self._keyword.search(
                    query, top_k=cfg.keyword_top_k,
                    fact_type=fact_type, scene_name=scene_name,
                )
                dense_results, keyword_results = await asyncio.gather(dense_task, keyword_task)

                # Fuse results
                results = rrf_fusion(
                    dense_results, keyword_results,
                    k=cfg.rrf_k, top_k=cfg.final_top_k,
                )
            else:
                # Embedding unavailable, fall back to keyword-only
                logger.info("Hybrid recall: embedding unavailable, falling back to keyword")
                results = await self._keyword.search(
                    query, top_k=cfg.final_top_k,
                    fact_type=fact_type, scene_name=scene_name,
                )

        elif strategy == "embedding":
            if query_embedding:
                results = await self._dense.search(
                    query_embedding, top_k=cfg.final_top_k,
                    fact_type=fact_type, scene_name=scene_name,
                )
            else:
                logger.warning("Embedding recall requested but embedding unavailable")
                results = []

        elif strategy == "keyword":
            results = await self._keyword.search(
                query, top_k=cfg.final_top_k,
                fact_type=fact_type, scene_name=scene_name,
            )

        else:
            logger.warning("Unknown recall strategy: %s", strategy)
            results = []

        # Truncate content to max_chars_per_memory
        max_chars = cfg.max_chars_per_memory
        for r in results:
            content = r.get("content", "")
            if len(content) > max_chars:
                r["content"] = content[:max_chars] + "..."

        return results[:cfg.inject_top_k]

    async def _recall_l2(self) -> list[dict[str, Any]]:
        """L2 scene navigation: return scene list for agent to browse."""
        try:
            scenes = self._l2.get_all()
            # Return lightweight navigation info
            return [
                {
                    "scene_name": s["scene_name"],
                    "fact_count": len(s.get("fact_ids") or []),
                }
                for s in scenes
            ]
        except Exception as exc:
            logger.warning("L2 scene fetch failed: %s", exc)
            return []

    async def _recall_l3(self) -> str | None:
        """L3 persona: return the user persona markdown."""
        try:
            return self._kv.get("persona")
        except Exception as exc:
            logger.warning("L3 persona fetch failed: %s", exc)
            return None

    async def search_facts(
        self,
        query: str,
        *,
        top_k: int = 10,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Direct L1 search (used by agent tool)."""
        cfg = self._config
        strategy = cfg.recall_strategy

        query_embedding: list[float] | None = None
        if strategy in ("hybrid", "embedding"):
            try:
                query_embedding = await self._embedding_fn(query)
            except Exception as exc:
                logger.warning("Embedding failed: %s", exc)

        if strategy == "hybrid" and query_embedding:
            dense_task = self._dense.search(query_embedding, top_k=top_k, fact_type=fact_type, scene_name=scene_name)
            keyword_task = self._keyword.search(query, top_k=top_k, fact_type=fact_type, scene_name=scene_name)
            d, k = await asyncio.gather(dense_task, keyword_task)
            return rrf_fusion(d, k, k=cfg.rrf_k, top_k=top_k)
        elif strategy == "embedding" and query_embedding:
            return await self._dense.search(query_embedding, top_k=top_k, fact_type=fact_type, scene_name=scene_name)
        else:
            return await self._keyword.search(query, top_k=top_k, fact_type=fact_type, scene_name=scene_name)
