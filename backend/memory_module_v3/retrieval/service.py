"""Unified recall service: hybrid retrieval across L1/L2/L3."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from ..config import MemoryV3Config, get_memory_v3_config
from ..storage.l1_repo import L1Repo
from ..storage.l2_file_repo import L2FileRepo
from ..storage.l3_file_repo import L3FileRepo
from .dense import DenseRetriever
from .keyword import KeywordRetriever
from .fusion import rrf_fusion

logger = logging.getLogger(__name__)

EmbeddingFn = Callable[[str], Awaitable[list[float]]]


class RecallService:
    """Unified memory recall: L1 hybrid search + L2 scene navigation + L3 persona.

    L2/L3 use dirty-flag caching: data is re-read from files only when
    the pipeline signals a change (invalidate_cache).
    """

    def __init__(
        self,
        l1_repo: L1Repo,
        l2_repo: L2FileRepo,
        l3_repo: L3FileRepo,
        embedding_fn: EmbeddingFn,
        config: MemoryV3Config | None = None,
    ):
        self._config = config or get_memory_v3_config()
        self._dense = DenseRetriever(l1_repo)
        self._keyword = KeywordRetriever(l1_repo)
        self._l2 = l2_repo
        self._l3 = l3_repo
        self._embedding_fn = embedding_fn

        # Dirty-flag cache for L2/L3
        self._l2_cache: list[dict[str, Any]] | None = None
        self._l3_cache: str | None = None
        self._cache_dirty: bool = True
        self._context_changed: bool = False
        self._stable_context_str: str = ""

    def invalidate_cache(self) -> None:
        """Mark L2/L3 cache as stale. Called by PipelineManager after L2/L3 runs."""
        self._cache_dirty = True

    @property
    def context_changed(self) -> bool:
        """Whether L2/L3 context was refreshed on the last recall() call."""
        return self._context_changed

    def get_stable_context(self) -> str:
        """Return formatted L2/L3 system context (cached)."""
        return self._stable_context_str

    async def recall(
        self,
        query: str,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> dict[str, Any]:
        """Full recall: L1 facts + L2 scene navigation + L3 persona.

        L1 is searched fresh every turn (query-dependent).
        L2/L3 are cached and only refreshed when dirty.

        Returns:
            {
                "l1_facts": [...],        # relevant atomic facts
                "l2_scenes": [...],       # scene navigation links (from cache)
                "l3_persona": str | None, # user persona markdown (from cache)
            }
        """
        from ..integrations.middleware import build_recall_context

        # L1: search every turn (query-dependent)
        l1_task = self._recall_l1(query, fact_type=fact_type, scene_name=scene_name)

        # L2/L3: only re-read when cache is dirty, then replace stable context
        self._context_changed = False
        if self._cache_dirty:
            self._l2_cache = self._l2.get_all()
            self._l3_cache = self._l3.get()
            self._cache_dirty = False
            self._context_changed = True

            # Rebuild stable context string (full replacement)
            ctx = build_recall_context({
                "l2_scenes": self._l2_cache or [],
                "l3_persona": self._l3_cache,
            })
            self._stable_context_str = ctx.get("append_system_context", "")
            logger.debug("L2/L3 cache refreshed, stable context replaced")

        l1_facts = await l1_task

        # Build dynamic context (L1 only, changes every turn)
        ctx = build_recall_context({"l1_facts": l1_facts})

        return {
            "l1_facts": l1_facts,
            "l2_scenes": self._l2_cache or [],
            "l3_persona": self._l3_cache,
            "prepend_context": ctx.get("prepend_context", ""),
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
