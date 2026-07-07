"""Daily digest pipeline — downloads, parses, analyzes, and wiki-ifies arXiv papers."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os

import httpx
from langchain_core.messages import HumanMessage, SystemMessage

from config import get_settings
from service.arxiv_service import ArxivPaper
from tools.pdf_parser_tool import _parse_pdf
from tools.mineru_client import parse_via_mineru
from tools.wiki_engine_tool import (
    SaveWikiPageTool,
    RegisterSourceTool,
    RebuildIndexTool,
    AppendLogTool,
    _sanitize_slug,
    _build_frontmatter,
    _today_str,
    _now_str,
    ENTITY_TYPES,
)

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are an academic research assistant. Analyze the following paper and produce a structured wiki page in Markdown.

Paper title: {title}
Authors: {authors}
Abstract: {abstract}

Full text (excerpt):
{full_text}

Produce a Markdown document with these sections:
1. **Core Problem** — what problem does this paper address?
2. **Key Contribution** — the main novelty (1-2 sentences)
3. **Method** — how does the approach work? Key mechanisms.
4. **Key Concepts** — important terms/concepts introduced or used
5. **Experiments** — datasets used, baselines compared, main results
6. **Limitations** — acknowledged or apparent limitations
7. **Related Work** — key references and how they differ

Rules:
- Be concise. Each section 2-5 sentences max.
- Use factual language. If unsure, mark with [confidence: low].
- Do NOT fabricate details not in the paper.
- Output ONLY the Markdown body (no frontmatter).
"""

ENTITY_EXTRACTION_PROMPT = """You are an academic research assistant. From the following paper analysis, extract key entities that deserve their own wiki pages.

Paper title: {title}
Analysis:
{analysis}

Output a JSON object (no other text) with these fields:
{{
  "concepts": [
    {{"name": "concept name", "slug": "concept-slug", "definition": "1-2 sentence definition", "tags": "comma,separated"}}
  ],
  "methods": [
    {{"name": "method name", "slug": "method-slug", "mechanism": "2-3 sentence description of how it works", "tags": "comma,separated"}}
  ],
  "datasets": [
    {{"name": "dataset name", "slug": "dataset-slug", "description": "1-2 sentence description", "tags": "comma,separated"}}
  ]
}}

Rules:
- Extract 2-5 concepts, 1-3 methods, 0-2 datasets per paper.
- Only extract entities that are genuinely important to understanding the paper.
- Slugs must be lowercase, hyphenated, URL-friendly.
- If unsure whether something qualifies, include it with lower confidence.
- Output ONLY valid JSON, no markdown code fences.
"""


TAG_CLASSIFY_PROMPT = """You are an academic research assistant. Classify the following paper into 1-3 predefined research tags.

Paper title: {title}
Abstract: {abstract}
Analysis excerpt: {analysis}

Available tags (choose 1-3, prefer precision over coverage):
{tag_list}

Output ONLY a JSON array of tag IDs, e.g. ["memory-systems", "agent-architecture"]. No other text."""


def _load_taxonomy(base_dir: Path) -> list[dict]:
    """Load predefined tag taxonomy from wiki/tags.yaml."""
    import yaml
    tags_path = base_dir / "wiki" / "tags.yaml"
    if not tags_path.exists():
        return []
    try:
        data = yaml.safe_load(tags_path.read_text(encoding="utf-8"))
        return data.get("taxonomy", [])
    except Exception:
        return []


def _classify_paper_tags(
    llm: Any,
    title: str,
    abstract: str,
    analysis: str,
    taxonomy: list[dict],
) -> list[str]:
    """Use LLM to classify paper into predefined tags."""
    if not taxonomy:
        return []
    tag_list = "\n".join(f"- {t['id']}: {t['label']} (keywords: {', '.join(t['keywords'])})" for t in taxonomy)
    prompt = TAG_CLASSIFY_PROMPT.format(
        title=title,
        abstract=abstract[:500],
        analysis=analysis[:1500],
        tag_list=tag_list,
    )
    try:
        response = llm.invoke([
            {"role": "system", "content": "You are a precise classifier. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ])
        raw = response.content if isinstance(response.content, str) else str(response.content)
        import re
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            tags = json.loads(match.group())
            valid_ids = {t["id"] for t in taxonomy}
            return [t for t in tags if t in valid_ids][:3]
    except Exception as e:
        logger.warning("Tag classification failed: %s", e)
    return []


def _download_pdf(url: str, dest_dir: Path) -> Path | None:
    """Download a PDF from arXiv to dest_dir. Returns the local path or None on failure."""
    filename = url.rsplit("/", 1)[-1]
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    dest = dest_dir / filename

    try:
        proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or None
        with httpx.Client(timeout=60.0, follow_redirects=True, proxy=proxy) as client:
            resp = client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest
    except Exception as e:
        logger.error("Failed to download PDF %s: %s", url, e)
        return None


def _get_llm():
    """Get a cached LLM instance for the digest pipeline."""
    from graph.llm import get_fast_llm
    return get_fast_llm(get_settings(), temperature=0.3, streaming=False)


def _analyze_with_llm(paper: ArxivPaper, full_text: str) -> str:
    """Call LLM to produce structured analysis of the paper."""
    llm = _get_llm()

    prompt = ANALYSIS_PROMPT.format(
        title=paper.title,
        authors=", ".join(paper.authors),
        abstract=paper.abstract,
        full_text=full_text[:30000],
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are an academic wiki writer. Be precise and concise."),
            HumanMessage(content=prompt),
        ])
        return response.content
    except Exception as e:
        logger.error("LLM analysis failed for %s: %s", paper.arxiv_id, e)
        return f"## Abstract\n\n{paper.abstract}\n\n> Agent judgment (confidence: low) — LLM analysis failed, using raw abstract."


def _extract_entities_with_llm(paper: ArxivPaper, analysis: str) -> dict:
    """Extract key concepts, methods, datasets from the analysis."""
    llm = _get_llm()

    prompt = ENTITY_EXTRACTION_PROMPT.format(
        title=paper.title,
        analysis=analysis[:8000],
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are an academic entity extractor. Output only valid JSON."),
            HumanMessage(content=prompt),
        ])
        raw = response.content.strip()
        # strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning("Entity extraction failed for %s: %s", paper.arxiv_id, e)
        return {"concepts": [], "methods": [], "datasets": []}


def _create_wiki_pages(
    paper: ArxivPaper,
    analysis: str,
    pdf_path: Path,
    base_dir: Path,
    entities: dict | None = None,
) -> tuple[str, list[str]]:
    """Create wiki pages for a paper and its related entities.

    Returns:
        (paper_slug, created_entity_slugs) — slugs of all created pages.
    """
    slug = _sanitize_slug(paper.title)
    if not slug:
        slug = _sanitize_slug(paper.arxiv_id.replace(".", "-"))

    created_slugs: list[str] = []

    # register source
    register_tool = RegisterSourceTool(root_dir=base_dir)
    register_result = register_tool._run(
        file_path=str(pdf_path),
        title=paper.title,
        authors=", ".join(paper.authors),
        year=int(paper.published[:4]) if paper.published else 0,
        arxiv_id=paper.arxiv_id,
    )
    reg_data = json.loads(register_result)
    source_hash = reg_data.get("hash", "")

    # build paper content
    sections = [
        f"# {paper.title}\n",
        f"**arXiv**: [{paper.arxiv_id}](https://arxiv.org/abs/{paper.arxiv_id})",
        f"**Published**: {paper.published}",
        f"**Categories**: {', '.join(paper.categories)}\n",
        analysis,
    ]
    content = "\n".join(sections)

    # collect related page links for the paper
    related_page_slugs: list[str] = []

    # create concept pages
    if entities and entities.get("concepts"):
        for concept in entities["concepts"]:
            c_slug = _sanitize_slug(concept.get("slug", concept.get("name", "")))
            if not c_slug:
                continue
            c_path = base_dir / "wiki" / "concepts" / f"{c_slug}.md"
            if c_path.exists():
                # append this paper as a reference
                existing = c_path.read_text(encoding="utf-8")
                if slug not in existing:
                    updated = existing.rstrip() + f"\n- [[{slug}]]\n"
                    c_path.write_text(updated, encoding="utf-8")
                related_page_slugs.append(f"concepts/{c_slug}")
                continue
            # create new concept page
            c_path.parent.mkdir(parents=True, exist_ok=True)
            c_content = (
                f"## Definition\n\n{concept.get('definition', '')}\n\n"
                f"## Key Papers\n\n- [[{slug}]]\n"
            )
            c_tags = concept.get("tags", "")
            c_fm = _build_frontmatter({
                "slug": c_slug,
                "title": concept.get("name", c_slug).replace("-", " ").title(),
                "type": "concept",
                "created": _today_str(),
                "updated": _today_str(),
                "status": "stub",
                "confidence": "medium",
                "source_count": 1,
                "tags": [t.strip() for t in c_tags.split(",") if t.strip()] if c_tags else [],
                "related_pages": [slug],
            })
            c_path.write_text(f"{c_fm}\n\n{c_content}\n", encoding="utf-8")
            related_page_slugs.append(f"concepts/{c_slug}")
            created_slugs.append(c_slug)
            logger.info("Created concept page: %s", c_slug)

    # create method pages
    if entities and entities.get("methods"):
        for method in entities["methods"]:
            m_slug = _sanitize_slug(method.get("slug", method.get("name", "")))
            if not m_slug:
                continue
            m_path = base_dir / "wiki" / "methods" / f"{m_slug}.md"
            if m_path.exists():
                existing = m_path.read_text(encoding="utf-8")
                if slug not in existing:
                    updated = existing.rstrip() + f"\n- [[{slug}]]\n"
                    m_path.write_text(updated, encoding="utf-8")
                related_page_slugs.append(f"methods/{m_slug}")
                continue
            m_path.parent.mkdir(parents=True, exist_ok=True)
            m_content = (
                f"## Mechanism\n\n{method.get('mechanism', '')}\n\n"
                f"## Evaluated By\n\n- [[{slug}]]\n"
            )
            m_tags = method.get("tags", "")
            m_fm = _build_frontmatter({
                "slug": m_slug,
                "title": method.get("name", m_slug).replace("-", " ").title(),
                "type": "method",
                "created": _today_str(),
                "updated": _today_str(),
                "status": "stub",
                "confidence": "medium",
                "source_count": 1,
                "tags": [t.strip() for t in m_tags.split(",") if t.strip()] if m_tags else [],
                "related_pages": [slug],
            })
            m_path.write_text(f"{m_fm}\n\n{m_content}\n", encoding="utf-8")
            related_page_slugs.append(f"methods/{m_slug}")
            created_slugs.append(m_slug)
            logger.info("Created method page: %s", m_slug)

    # create dataset pages
    if entities and entities.get("datasets"):
        for ds in entities["datasets"]:
            d_slug = _sanitize_slug(ds.get("slug", ds.get("name", "")))
            if not d_slug:
                continue
            d_path = base_dir / "wiki" / "datasets" / f"{d_slug}.md"
            if d_path.exists():
                existing = d_path.read_text(encoding="utf-8")
                if slug not in existing:
                    updated = existing.rstrip() + f"\n- [[{slug}]]\n"
                    d_path.write_text(updated, encoding="utf-8")
                related_page_slugs.append(f"datasets/{d_slug}")
                continue
            d_path.parent.mkdir(parents=True, exist_ok=True)
            d_content = (
                f"## Overview\n\n{ds.get('description', '')}\n\n"
                f"## Used In\n\n- [[{slug}]]\n"
            )
            d_tags = ds.get("tags", "")
            d_fm = _build_frontmatter({
                "slug": d_slug,
                "title": ds.get("name", d_slug).replace("-", " ").title(),
                "type": "dataset",
                "created": _today_str(),
                "updated": _today_str(),
                "status": "stub",
                "confidence": "medium",
                "source_count": 1,
                "tags": [t.strip() for t in d_tags.split(",") if t.strip()] if d_tags else [],
                "related_pages": [slug],
            })
            d_path.write_text(f"{d_fm}\n\n{d_content}\n", encoding="utf-8")
            related_page_slugs.append(f"datasets/{d_slug}")
            created_slugs.append(d_slug)
            logger.info("Created dataset page: %s", d_slug)

    # save paper page (with links to created entity pages)
    related_str = ",".join(related_page_slugs) if related_page_slugs else ""

    # Classify paper into predefined semantic tags
    taxonomy = _load_taxonomy(base_dir)
    llm = _get_llm()
    paper_tags = _classify_paper_tags(llm, paper.title, paper.abstract, content, taxonomy)
    tags_str = ", ".join(paper_tags) if paper_tags else ", ".join(paper.categories[:3])

    save_tool = SaveWikiPageTool(root_dir=base_dir)
    save_tool._run(
        slug=slug,
        title=paper.title,
        entity_type="paper",
        content=content,
        authors=", ".join(paper.authors),
        year=int(paper.published[:4]) if paper.published else 0,
        arxiv_id=paper.arxiv_id,
        tags=tags_str,
        related_pages=related_str,
        source_hash=source_hash,
        status="complete",
        confidence="medium",
    )

    # create/update author pages
    for author_name in paper.authors[:5]:
        author_slug = _sanitize_slug(author_name)
        if not author_slug:
            continue
        author_dir = base_dir / "wiki" / "authors"
        author_dir.mkdir(parents=True, exist_ok=True)
        author_path = author_dir / f"{author_slug}.md"

        if author_path.exists():
            existing = author_path.read_text(encoding="utf-8")
            if slug not in existing:
                updated = existing.rstrip() + f"\n- [[{slug}]]\n"
                author_path.write_text(updated, encoding="utf-8")
        else:
            author_content = (
                f"# {author_name}\n\n"
                f"## Papers\n\n"
                f"- [[{slug}]]\n"
            )
            author_fm = _build_frontmatter({
                "slug": author_slug,
                "title": author_name,
                "type": "author",
                "created": _today_str(),
                "updated": _today_str(),
                "status": "stub",
                "confidence": "medium",
            })
            author_path.write_text(f"{author_fm}\n\n{author_content}\n", encoding="utf-8")

    return slug, created_slugs


def _check_and_create_surveys(base_dir: Path) -> list[str]:
    """Check if any topic has ≥3 papers and create survey pages if missing."""
    from tools.wiki_engine_tool import ListWikiPagesTool, SaveWikiPageTool

    created: list[str] = []
    list_tool = ListWikiPagesTool(root_dir=base_dir)
    save_tool = SaveWikiPageTool(root_dir=base_dir)

    result = list_tool._run(entity_type="paper")
    try:
        papers = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return created

    # count tags across papers
    tag_counts: dict[str, list[str]] = {}
    for p in papers:
        for tag in p.get("tags", []):
            tag_counts.setdefault(tag, []).append(p.get("slug", ""))

    for tag, paper_slugs in tag_counts.items():
        if len(paper_slugs) < 3:
            continue
        survey_slug = _sanitize_slug(f"{tag}-survey")
        survey_path = base_dir / "wiki" / "surveys" / f"{survey_slug}.md"
        if survey_path.exists():
            continue

        # build timeline from papers
        timeline_rows = []
        taxonomy_entries = []
        for ps in paper_slugs:
            p_path = base_dir / "wiki" / "papers" / f"{ps}.md"
            if not p_path.exists():
                continue
            from tools.wiki_engine_tool import _parse_frontmatter
            meta = _parse_frontmatter(p_path.read_text(encoding="utf-8"))
            title = meta.get("title", ps)
            year = meta.get("year", "")
            timeline_rows.append(f"| {year} | [{title}](../papers/{ps}.md) | |")
            taxonomy_entries.append(f"- [[{ps}]]")

        survey_content = (
            f"## Overview\n\n"
            f"本综述整理了 Wiki 中关于 {tag} 的所有论文，归纳研究脉络和关键技术演进。\n\n"
            f"## Taxonomy\n\n" + "\n".join(taxonomy_entries) + "\n\n"
            f"## Timeline\n\n| 年份 | 论文 | 核心贡献 |\n|------|------|----------|\n"
            + "\n".join(timeline_rows) + "\n\n"
            f"## References\n\n" + "\n".join(f"- [[{ps}]]" for ps in paper_slugs) + "\n"
        )

        survey_path.parent.mkdir(parents=True, exist_ok=True)
        save_tool._run(
            slug=survey_slug,
            title=f"{tag.title()} Survey",
            entity_type="survey",
            content=survey_content,
            tags=f"{tag},survey",
            related_pages=",".join(paper_slugs[:10]),
            source_count=len(paper_slugs),
            status="in_progress",
            confidence="medium",
        )
        created.append(survey_slug)
        logger.info("Auto-created survey: %s (%d papers)", survey_slug, len(paper_slugs))

    return created


def _parse_via_mineru(pdf_url: str, settings) -> dict:
    """Parse a document URL via MinerU API. Returns _parse_pdf-compatible dict or {"error": "..."}."""
    return parse_via_mineru(
        pdf_url,
        base_url=settings.mineru_base_url,
        token=settings.mineru_token,
        model_version=settings.mineru_default_model,
        poll_interval=settings.mineru_poll_interval,
        max_wait=settings.mineru_max_wait,
    )


def run_digest(papers: list[ArxivPaper], base_dir: Path) -> tuple[list[str], list[dict]]:
    """Process a batch of arXiv papers: download, parse, analyze, create wiki pages.

    Returns:
        (created_slugs, paper_dicts) — slugs of created wiki pages and paper dicts for notification.
    """
    created_slugs: list[str] = []
    paper_dicts: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="arxiv_digest_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        for paper in papers:
            logger.info("Processing: %s — %s", paper.arxiv_id, paper.title)

            settings = get_settings()
            full_text = paper.abstract  # fallback default

            # ── primary: MinerU via arXiv URL ──
            if settings.mineru_token:
                try:
                    parsed = _parse_via_mineru(paper.pdf_url, settings)
                    if "error" not in parsed:
                        full_text = parsed.get("full_text", paper.abstract)
                        logger.info("MinerU parsed %s: %d chars", paper.arxiv_id, len(full_text))
                    else:
                        logger.warning("MinerU failed for %s: %s", paper.arxiv_id, parsed["error"])
                except Exception as e:
                    logger.warning("MinerU exception for %s: %s", paper.arxiv_id, e)

            # download PDF for source registration (always needed for SHA-256)
            pdf_path = _download_pdf(paper.pdf_url, tmp_path)
            if pdf_path is None:
                logger.warning("Skipping %s — PDF download failed", paper.arxiv_id)
                d = paper.to_dict()
                paper_dicts.append(d)
                continue

            # ── fallback: PyMuPDF if MinerU did not provide full_text ──
            if full_text == paper.abstract:
                parsed_local = _parse_pdf(str(pdf_path))
                full_text = parsed_local.get("full_text", paper.abstract)

            # LLM analysis
            analysis = _analyze_with_llm(paper, full_text)

            # extract entities (concepts, methods, datasets)
            entities = _extract_entities_with_llm(paper, analysis)

            # create wiki pages (paper + entity pages)
            try:
                slug, entity_slugs = _create_wiki_pages(
                    paper, analysis, pdf_path, base_dir, entities,
                )
                created_slugs.append(slug)
                created_slugs.extend(entity_slugs)
                logger.info(
                    "Wiki pages created: %s + %d entities",
                    slug, len(entity_slugs),
                )
            except Exception as e:
                logger.error("Failed to create wiki page for %s: %s", paper.arxiv_id, e)

            d = paper.to_dict()
            paper_dicts.append(d)

    # auto-create survey pages for topics with ≥3 papers
    try:
        survey_slugs = _check_and_create_surveys(base_dir)
        created_slugs.extend(survey_slugs)
    except Exception as e:
        logger.error("Survey auto-creation failed: %s", e)

    # rebuild index and log
    if created_slugs:
        try:
            rebuild_tool = RebuildIndexTool(root_dir=base_dir)
            rebuild_tool._run()

            log_tool = AppendLogTool(root_dir=base_dir)
            log_tool._run(
                operation="ingest",
                details=f"Daily digest: ingested {len(created_slugs)} wiki pages from arXiv",
                pages_affected=", ".join(f"wiki/{s}.md" for s in created_slugs[:20]),
            )
        except Exception as e:
            logger.error("Failed to rebuild index/log: %s", e)

    return created_slugs, paper_dicts
