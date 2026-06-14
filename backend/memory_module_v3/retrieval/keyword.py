"""Keyword retrieval using PostgreSQL tsvector full-text search."""

from __future__ import annotations

import logging
from typing import Any

from ..storage.l1_repo import L1Repo

logger = logging.getLogger(__name__)


class KeywordRetriever:
    """Retrieves L1 facts via tsvector GIN index BM25 search."""

    def __init__(self, l1_repo: L1Repo):
        self._repo = l1_repo

    async def search(
        self,
        query: str,
        top_k: int = 20,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for facts matching the query keywords.

        Returns list of dicts with fact data + 'keyword_score' key.
        """
        if not query or not query.strip():
            return []

        try:
            results = self._repo.keyword_search(
                query,
                top_k=top_k,
                fact_type=fact_type,
                scene_name=scene_name,
            )
            for r in results:
                r["keyword_score"] = float(r.pop("rank_score", 0.0))
            return results
        except Exception as exc:
            logger.warning("Keyword search failed: %s", exc)
            return []
