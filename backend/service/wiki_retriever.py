"""Wiki hybrid retriever — BM25 + embedding semantic search with RRF fusion."""

from __future__ import annotations

import hashlib
import logging
import math
import os
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

# RRF constant — typical value from the literature
RRF_K = 60

_wiki_retrievers: dict[str, WikiRetriever] = {}


def get_wiki_retriever(wiki_dir: Path) -> WikiRetriever:
    """Get or create a singleton retriever for a wiki directory."""
    key = str(wiki_dir.resolve())
    if key not in _wiki_retrievers:
        _wiki_retrievers[key] = WikiRetriever(wiki_dir)
    return _wiki_retrievers[key]


class WikiRetriever:
    """In-memory hybrid retriever for wiki pages (BM25 + embedding semantic search)."""

    def __init__(self, wiki_dir: Path) -> None:
        self._wiki_dir = wiki_dir.resolve()
        self._pages: list[dict] = []
        self._bm25: BM25Okapi | None = None
        self._tokenized_corpus: list[list[str]] = []
        self._embeddings: list[list[float]] = []
        self._fingerprint: str = ""

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def _scan_pages(self) -> list[dict]:
        """Scan all wiki .md files, parse frontmatter, extract body."""
        from tools.wiki_engine_tool import ENTITY_TYPES, _parse_frontmatter

        pages: list[dict] = []
        for etype, subdir in ENTITY_TYPES.items():
            type_dir = self._wiki_dir / subdir
            if not type_dir.exists():
                continue
            for md_file in sorted(type_dir.glob("*.md")):
                text = md_file.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)

                # extract body (after second ---)
                body = text
                if text.startswith("---"):
                    end = text.find("\n---", 3)
                    if end != -1:
                        body = text[end + 4:].strip()

                pages.append({
                    "slug": md_file.stem,
                    "title": meta.get("title", md_file.stem),
                    "type": etype,
                    "tags": meta.get("tags", []),
                    "text": text,
                    "body": body,
                    "path": str(md_file.relative_to(self._wiki_dir)).replace("\\", "/"),
                })
        return pages

    def _compute_fingerprint(self) -> str:
        """Compute a fingerprint of the wiki directory for change detection."""
        h = hashlib.md5()
        for subdir in sorted(self._wiki_dir.iterdir()):
            if not subdir.is_dir():
                continue
            for md_file in sorted(subdir.glob("*.md")):
                h.update(md_file.name.encode())
                h.update(str(md_file.stat().st_mtime_ns).encode())
        return h.hexdigest()

    def _build_bm25(self) -> None:
        """Build BM25 index using jieba tokenization."""
        self._tokenized_corpus = []
        for page in self._pages:
            tokens = list(jieba.cut(page["title"] + " " + page["body"]))
            tokens = [t.strip().lower() for t in tokens if len(t.strip()) > 1]
            self._tokenized_corpus.append(tokens)

        if self._tokenized_corpus:
            self._bm25 = BM25Okapi(self._tokenized_corpus)
        else:
            self._bm25 = None

    def _build_embeddings(self) -> None:
        """Build embedding index for semantic search."""
        if not self._pages:
            self._embeddings = []
            return

        texts = [p["title"] + ". " + p["body"] for p in self._pages]

        try:
            from service.arxiv_service import _get_embeddings
            self._embeddings = _get_embeddings(texts)
        except Exception as e:
            logger.warning("Embedding build failed, semantic search disabled: %s", e)
            self._embeddings = []

    def rebuild(self) -> None:
        """Full rebuild of all indexes."""
        logger.info("Rebuilding wiki retriever index for %s", self._wiki_dir)
        self._pages = self._scan_pages()
        self._fingerprint = self._compute_fingerprint()
        self._build_bm25()
        self._build_embeddings()
        logger.info("Wiki retriever ready: %d pages indexed", len(self._pages))

    def rebuild_if_needed(self) -> None:
        """Rebuild only if wiki files have changed."""
        current = self._compute_fingerprint()
        if current != self._fingerprint or not self._pages:
            self.rebuild()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _bm25_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """BM25 keyword search. Returns [(page_index, score), ...]."""
        if self._bm25 is None or not self._tokenized_corpus:
            return []

        tokens = list(jieba.cut(query))
        tokens = [t.strip().lower() for t in tokens if len(t.strip()) > 1]
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, score) for idx, score in ranked[:top_k] if score > 0]

    def _semantic_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Embedding cosine similarity search. Returns [(page_index, score), ...]."""
        if not self._embeddings:
            return []

        try:
            from service.arxiv_service import _get_embeddings, _cosine_similarity
            query_emb = _get_embeddings([query])[0]
        except Exception as e:
            logger.warning("Semantic search failed: %s", e)
            return []

        scored = []
        for i, page_emb in enumerate(self._embeddings):
            sim = float(_cosine_similarity(query_emb, page_emb))
            scored.append((i, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [(idx, score) for idx, score in scored[:top_k] if score > 0.1]

    def _rrf_fusion(
        self,
        bm25_results: list[tuple[int, float]],
        semantic_results: list[tuple[int, float]],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Reciprocal Rank Fusion of BM25 and semantic results."""
        scores: dict[int, float] = {}

        for rank, (idx, _) in enumerate(bm25_results):
            scores[idx] = scores.get(idx, 0) + 1.0 / (RRF_K + rank)

        for rank, (idx, _) in enumerate(semantic_results):
            scores[idx] = scores.get(idx, 0) + 1.0 / (RRF_K + rank)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]

    def _extract_snippet(self, body: str, max_len: int = 300) -> str:
        """Extract a clean snippet from the page body."""
        snippet = body[:max_len].strip()
        # remove markdown headers
        lines = snippet.split("\n")
        clean = []
        for line in lines:
            if line.strip().startswith("#"):
                continue
            clean.append(line)
        result = "\n".join(clean).strip()
        if len(body) > max_len:
            result += "..."
        return result

    def search(self, query: str, top_k: int = 10, mode: str = "hybrid") -> list[dict]:
        """Search wiki pages.

        Args:
            query: natural language query
            top_k: max results
            mode: "bm25", "semantic", or "hybrid" (default)

        Returns:
            list of {slug, title, type, score, snippet, path, tags}
        """
        self.rebuild_if_needed()

        if not self._pages:
            return []

        if mode == "bm25":
            ranked = self._bm25_search(query, top_k)
        elif mode == "semantic":
            ranked = self._semantic_search(query, top_k)
        else:  # hybrid
            bm25_results = self._bm25_search(query, top_k * 2)
            semantic_results = self._semantic_search(query, top_k * 2)
            ranked = self._rrf_fusion(bm25_results, semantic_results, top_k)

        results = []
        for idx, score in ranked:
            page = self._pages[idx]
            results.append({
                "slug": page["slug"],
                "title": page["title"],
                "type": page["type"],
                "score": round(score, 4),
                "snippet": self._extract_snippet(page["body"]),
                "path": page["path"],
                "tags": page["tags"],
            })

        return results
