"""arXiv paper fetcher — fetches recent papers via RSS and ranks by semantic similarity."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "http://rss.arxiv.org/rss/cs.AI",
    "http://rss.arxiv.org/rss/cs.CL",
    "http://rss.arxiv.org/rss/cs.MA",
    "http://rss.arxiv.org/rss/cs.IR",
    "http://rss.arxiv.org/rss/cs.RO",
]

RSS_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# User research interests — used for semantic matching
INTEREST_TOPICS = [
    "agent memory and knowledge augmentation, long-term memory for LLM agents, knowledge-enhanced agent",
    "multi-agent collaboration and coordination, multi-agent systems, cooperative agents",
    "agent tool use and embodied intelligence, tool-augmented LLM, function calling, robotic agents",
    "agent perception and reasoning architecture, planning, chain of thought, world model",
    "agent evaluation benchmarks and safety, agent alignment, red teaming, trustworthy agents",
]

# Fallback keywords for initial broad filtering before semantic ranking
BROAD_KEYWORDS = [
    "agent", "llm", "language model", "memory", "tool", "planning",
    "reasoning", "multi-agent", "embodied", "benchmark", "safety",
    "knowledge", "retrieval", "world model",
]


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    pdf_url: str
    categories: list[str]
    published: str  # YYYY-MM-DD
    similarity: float = 0.0  # semantic similarity score

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_arxiv_id(text: str) -> str:
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", text)
    return match.group(1) if match else ""


def _extract_abstract(description: str) -> str:
    match = re.search(r"Abstract:\s*(.*)", description, re.DOTALL)
    if match:
        return match.group(1).strip().replace("\n", " ")[:2000]
    return description.strip()[:2000]


def _parse_pub_date(pub_date_str: str) -> str:
    try:
        dt = parsedate_to_datetime(pub_date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _broad_keyword_filter(title: str, abstract: str) -> bool:
    """Quick keyword filter to reduce the candidate set before semantic ranking."""
    text = (title + " " + abstract).lower()
    return any(kw in text for kw in BROAD_KEYWORDS)


def _parse_rss_item(item: ET.Element) -> ArxivPaper | None:
    try:
        title_el = item.find("title")
        title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""

        link_el = item.find("link")
        link = link_el.text.strip() if link_el is not None and link_el.text else ""

        desc_el = item.find("description")
        description = desc_el.text or "" if desc_el is not None else ""
        abstract = _extract_abstract(description)

        guid_el = item.find("guid")
        guid = guid_el.text.strip() if guid_el is not None and guid_el.text else ""

        arxiv_id = _extract_arxiv_id(link) or _extract_arxiv_id(guid)
        if not arxiv_id:
            return None

        creator_el = item.find("dc:creator", RSS_NS)
        authors_raw = creator_el.text.strip() if creator_el is not None and creator_el.text else ""
        authors = [a.strip() for a in authors_raw.split(",") if a.strip()]

        categories = []
        for cat_el in item.findall("category"):
            if cat_el.text:
                categories.append(cat_el.text.strip())

        pub_date_el = item.find("pubDate")
        published = _parse_pub_date(pub_date_el.text) if pub_date_el is not None and pub_date_el.text else ""

        announce_el = item.find("arxiv:announce_type", RSS_NS)
        if announce_el is not None and announce_el.text and "cross" in announce_el.text.lower():
            return None

        if not title or not arxiv_id:
            return None

        return ArxivPaper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract=abstract,
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            categories=categories,
            published=published,
        )
    except Exception as e:
        logger.warning("Failed to parse RSS item: %s", e)
        return None


# ---------------------------------------------------------------------------
# Embedding-based semantic search
# ---------------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _get_embeddings(texts: list[str]) -> list[list[float]]:
    """Get embeddings using the project's configured embedding model."""
    from config import get_settings
    from graph.llm import build_embedding_config_from_settings, get_embedding_model

    settings = get_settings()
    emb_config = build_embedding_config_from_settings(settings)
    model = get_embedding_model(emb_config)
    return model.embed_documents(texts)


def _rank_by_similarity(
    papers: list[ArxivPaper],
    min_score: float = 0.3,
    max_results: int = 20,
) -> list[ArxivPaper]:
    """Rank papers by semantic similarity to interest topics using embeddings."""
    if not papers:
        return []

    # Build texts for embedding: paper title + abstract
    paper_texts = [f"{p.title}. {p.abstract[:500]}" for p in papers]

    try:
        # Get embeddings for topics and papers in batches
        topic_embeddings = _get_embeddings(INTEREST_TOPICS)
        paper_embeddings = _get_embeddings(paper_texts)
    except Exception as e:
        logger.error("Embedding failed, falling back to keyword order: %s", e)
        return papers[:max_results]

    # For each paper, max similarity across all topics
    for i, paper in enumerate(papers):
        best_sim = 0.0
        for topic_emb in topic_embeddings:
            sim = _cosine_similarity(paper_embeddings[i], topic_emb)
            if sim > best_sim:
                best_sim = sim
        paper.similarity = round(best_sim, 4)

    # Filter by threshold and sort
    ranked = [p for p in papers if p.similarity >= min_score]
    ranked.sort(key=lambda p: p.similarity, reverse=True)

    logger.info(
        "Semantic ranking: %d/%d papers above threshold %.2f",
        len(ranked), len(papers), min_score,
    )
    return ranked[:max_results]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_arxiv_papers(
    max_results: int = 20,
    target_date: str | None = None,
    min_similarity: float = 0.3,
) -> list[ArxivPaper]:
    """Fetch papers from RSS, rank by semantic similarity to research interests.

    1. Fetch from all RSS feeds
    2. Broad keyword filter (cheap)
    3. Embedding-based semantic ranking (expensive, on filtered set)
    """
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or None
    all_papers: dict[str, ArxivPaper] = {}

    for feed_url in RSS_FEEDS:
        logger.info("Fetching RSS: %s", feed_url)
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True, proxy=proxy) as client:
                resp = client.get(feed_url)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("RSS %s failed: %s", feed_url, e)
            continue

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.warning("XML parse error %s: %s", feed_url, e)
            continue

        channel = root.find("channel")
        if channel is None:
            continue

        for item in channel.findall("item"):
            paper = _parse_rss_item(item)
            if paper is None:
                continue
            if paper.arxiv_id in all_papers:
                existing = all_papers[paper.arxiv_id]
                existing.categories = list(set(existing.categories + paper.categories))
                continue
            all_papers[paper.arxiv_id] = paper

    # Broad keyword filter to reduce embedding calls
    candidates = [
        p for p in all_papers.values()
        if _broad_keyword_filter(p.title, p.abstract)
    ]
    logger.info("Broad filter: %d / %d papers", len(candidates), len(all_papers))

    # Semantic ranking
    ranked = _rank_by_similarity(candidates, min_score=min_similarity, max_results=max_results)

    return ranked


def load_registered_arxiv_ids(base_dir: Path) -> set[str]:
    manifest_path = base_dir / "raw" / "sources" / "manifest.jsonl"
    ids: set[str] = set()
    if not manifest_path.exists():
        return ids
    for line in manifest_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            aid = entry.get("arxiv_id", "")
            if aid:
                ids.add(aid)
        except json.JSONDecodeError:
            continue
    return ids


def filter_new_papers(papers: list[ArxivPaper], registered: set[str]) -> list[ArxivPaper]:
    return [p for p in papers if p.arxiv_id not in registered]
