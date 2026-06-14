"""Fusion strategies for combining dense and keyword search results."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def rrf_fusion(
    dense_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    k: int = 60,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion (RRF).

    score(item) = sum(1 / (K + rank + 1)) across all lists where item appears.

    Items appearing in both lists get higher scores naturally.
    """
    # Build rank maps (0-indexed)
    scores: dict[int, float] = {}
    fact_map: dict[int, dict[str, Any]] = {}

    for rank, item in enumerate(dense_results):
        fid = item.get("fact_id")
        if fid is None:
            continue
        scores[fid] = scores.get(fid, 0.0) + 1.0 / (k + rank + 1)
        fact_map[fid] = item

    for rank, item in enumerate(keyword_results):
        fid = item.get("fact_id")
        if fid is None:
            continue
        scores[fid] = scores.get(fid, 0.0) + 1.0 / (k + rank + 1)
        if fid not in fact_map:
            fact_map[fid] = item

    # Sort by fused score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    results = []
    for fid, score in ranked[:top_k]:
        item = dict(fact_map[fid])
        item["fused_score"] = score
        results.append(item)

    return results


def weighted_sum_fusion(
    dense_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    dense_weight: float = 0.5,
    keyword_weight: float = 0.5,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Weighted sum fusion with min-max normalization.

    Normalizes each score list to [0, 1], then computes:
    fused = dense_weight * norm_dense + keyword_weight * norm_keyword
    """
    scores: dict[int, dict[str, float]] = {}
    fact_map: dict[int, dict[str, Any]] = {}

    # Collect dense scores
    for item in dense_results:
        fid = item.get("fact_id")
        if fid is None:
            continue
        scores.setdefault(fid, {})["dense"] = item.get("dense_score", 0.0)
        fact_map[fid] = item

    # Collect keyword scores
    for item in keyword_results:
        fid = item.get("fact_id")
        if fid is None:
            continue
        scores.setdefault(fid, {})["keyword"] = item.get("keyword_score", 0.0)
        if fid not in fact_map:
            fact_map[fid] = item

    # Normalize dense scores
    dense_scores = [s.get("dense", 0.0) for s in scores.values()]
    d_min, d_max = _min_max(dense_scores)

    # Normalize keyword scores
    kw_scores = [s.get("keyword", 0.0) for s in scores.values()]
    k_min, k_max = _min_max(kw_scores)

    # Compute fused scores
    fused: list[tuple[int, float]] = []
    for fid, sc in scores.items():
        d_norm = _normalize(sc.get("dense", 0.0), d_min, d_max)
        k_norm = _normalize(sc.get("keyword", 0.0), k_min, k_max)
        fused_score = dense_weight * d_norm + keyword_weight * k_norm
        fused.append((fid, fused_score))

    fused.sort(key=lambda x: x[1], reverse=True)

    results = []
    for fid, score in fused[:top_k]:
        item = dict(fact_map[fid])
        item["fused_score"] = score
        results.append(item)

    return results


def _min_max(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    return min(values), max(values)


def _normalize(value: float, min_v: float, max_v: float) -> float:
    if max_v <= min_v:
        return 0.5
    return (value - min_v) / (max_v - min_v)
