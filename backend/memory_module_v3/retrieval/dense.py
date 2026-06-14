"""Dense retrieval using pgvector cosine distance search."""

from __future__ import annotations

import logging
from typing import Any

from ..storage.l1_repo import L1Repo

logger = logging.getLogger(__name__)


class DenseRetriever:
    """Retrieves L1 facts via pgvector <=> cosine distance."""

    def __init__(self, l1_repo: L1Repo):
        self._repo = l1_repo

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for facts most similar to the query embedding.

        Returns list of dicts with fact data + 'dense_score' key.
        """
        if not query_embedding:
            return []

        try:
            results = self._repo.dense_search(
                query_embedding,
                top_k=top_k,
                fact_type=fact_type,
                scene_name=scene_name,
            )
            # cosine_sim is already computed by pgvector (1 - distance)
            for r in results:
                r["dense_score"] = float(r.pop("cosine_sim", 0.0))
            return results
        except Exception as exc:
            logger.warning("Dense search failed: %s", exc)
            return []
