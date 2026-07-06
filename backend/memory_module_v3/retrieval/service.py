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

    # ── embedding cache (quantized key → Redis, O(1), no DB) ──

    async def _emb_cache_get(self, emb: list[float]) -> list[float] | None:
        try:
            from storage.redis_client import quantized_emb_key, cache_get_float_list
            return await cache_get_float_list(quantized_emb_key("emb", emb))
        except ImportError:
            return None
        except Exception:
            return None

    async def _emb_cache_set(self, emb: list[float]) -> None:
        try:
            from storage.redis_client import quantized_emb_key, cache_set_float_list
            from config import get_settings
            await cache_set_float_list(
                quantized_emb_key("emb", emb), emb,
                get_settings().redis_embed_cache_ttl,
            )
        except ImportError:
            pass
        except Exception:
            pass

    async def _compute_embedding(self, query: str) -> list[float]:
        """Compute query embedding, deduplicated by quantized-key cache."""
        emb = await self._embedding_fn(query)
        cached = await self._emb_cache_get(emb)
        if cached is not None:
            return cached
        await self._emb_cache_set(emb)
        return emb

    # ── recall result cache (quantized embedding hash → Redis, O(1), no DB) ──

    async def _recall_cache_get(self, emb: list[float]) -> dict[str, Any] | None:
        try:
            from storage.redis_client import quantized_emb_key, cache_get_json
            return await cache_get_json(quantized_emb_key("recall", emb))
        except ImportError:
            return None
        except Exception as exc:
            logger.debug("Recall cache read failed: %s", exc)
            return None

    async def _recall_cache_set(self, emb: list[float], result: dict[str, Any]) -> None:
        try:
            from storage.redis_client import quantized_emb_key, cache_set_json
            from config import get_settings
            await cache_set_json(
                quantized_emb_key("recall", emb), result,
                get_settings().redis_recall_cache_ttl,
            )
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Recall cache write failed: %s", exc)

    async def recall(
        self,
        query: str,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> dict[str, Any]:
        """Full recall with quantized-embedding-key cache.

        1. Compute embedding (deduped by quantize-key cache)
        2. Quantize → Redis GET for recall-result cache
        3. Hit → return cached context (skip DB retrieval)
        4. Miss → L1 hybrid search → cache result
        """
        from ..integrations.middleware import build_recall_context

        # ── Step 1: compute embedding (quantized-key cache dedupes similar queries) ──
        emb: list[float] | None = None
        cfg = self._config
        if cfg.recall_strategy in ("hybrid", "embedding"):
            try:
                emb = await self._compute_embedding(query)
            except Exception as exc:
                logger.warning("Embedding failed: %s", exc)
                if cfg.recall_strategy == "embedding":
                    return {"l1_facts": [], "l2_scenes": [], "l3_persona": None, "prepend_context": ""}

        # ── Step 2: recall-result cache (quantized embedding key → Redis) ──
        if emb is not None:
            cached_result = await self._recall_cache_get(emb)
            if cached_result is not None:
                self._context_changed = False
                if self._cache_dirty:
                    self._l2_cache = self._l2.get_all()
                    self._l3_cache = self._l3.get()
                    self._cache_dirty = False
                    self._context_changed = True
                    ctx = build_recall_context({
                        "l2_scenes": self._l2_cache or [],
                        "l3_persona": self._l3_cache,
                    })
                    self._stable_context_str = ctx.get("append_system_context", "")
                return cached_result

        # ── Step 3: L1 hybrid search (pass pre-computed embedding) ──
        l1_task = self._recall_l1(query, query_embedding=emb,
                                  fact_type=fact_type, scene_name=scene_name)

        # L2/L3
        self._context_changed = False
        if self._cache_dirty:
            self._l2_cache = self._l2.get_all()
            self._l3_cache = self._l3.get()
            self._cache_dirty = False
            self._context_changed = True
            ctx = build_recall_context({
                "l2_scenes": self._l2_cache or [],
                "l3_persona": self._l3_cache,
            })
            self._stable_context_str = ctx.get("append_system_context", "")

        l1_facts = await l1_task
        ctx = build_recall_context({"l1_facts": l1_facts})

        result = {
            "l1_facts": l1_facts,
            "l2_scenes": self._l2_cache or [],
            "l3_persona": self._l3_cache,
            "prepend_context": ctx.get("prepend_context", ""),
        }

        # ── Step 4: cache result for future similar queries ──
        if emb is not None:
            await self._recall_cache_set(emb, result)

        return result

    async def _recall_l1(
        self,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """L1 hybrid search with configurable strategy.

        When query_embedding is pre-computed (from recall()), it is reused directly.
        Otherwise computed on demand via _compute_embedding (quantized-key dedup).
        """
        cfg = self._config
        strategy = cfg.recall_strategy

        # Compute query embedding if needed
        if query_embedding is None and strategy in ("hybrid", "embedding"):
            try:
                query_embedding = await self._compute_embedding(query)
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
                query_embedding = await self._compute_embedding(query)
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
