"""PDF ingest service — parse, analyze, and wiki-ify a single uploaded PDF.

Reuses digest_pipeline functions: _analyze_with_llm, _extract_entities_with_llm,
_create_wiki_pages. Orchestrates the full flow and yields progress events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from config import get_settings
from service.arxiv_service import ArxivPaper

logger = logging.getLogger(__name__)


async def process_pdf_upload(
    *,
    file_path: str | None = None,
    pdf_url: str | None = None,
    user_title: str | None = None,
    base_dir: Path,
) -> AsyncGenerator[dict[str, Any], None]:
    """Process an uploaded PDF: parse, analyze, extract entities, create wiki pages.

    Yields progress events: {event: "progress"|"done"|"error", ...}.
    """
    from tools.pdf_parser_tool import _parse_pdf

    settings = get_settings()

    # ── Stage 1: Parse PDF ──
    yield {"event": "progress", "stage": "parsing", "message": "正在解析 PDF..."}

    parsed = None
    source = "pymupdf"

    # Try MinerU if URL provided and token configured
    if pdf_url and settings.mineru_token:
        try:
            from tools.mineru_client import parse_via_mineru

            parsed = parse_via_mineru(
                pdf_url,
                base_url=settings.mineru_base_url,
                token=settings.mineru_token,
                model_version=settings.mineru_default_model,
                poll_interval=settings.mineru_poll_interval,
                max_wait=settings.mineru_max_wait,
            )
            if "error" in parsed:
                logger.warning("MinerU failed, falling back: %s", parsed["error"])
                parsed = None
            else:
                source = "mineru"
        except Exception as e:
            logger.warning("MinerU exception, falling back: %s", e)

    # Fallback to PyMuPDF
    if parsed is None and file_path:
        parsed = _parse_pdf(str(file_path))
        if "error" in parsed:
            yield {"event": "error", "error": f"PDF 解析失败: {parsed['error']}"}
            return
    elif parsed is None:
        yield {"event": "error", "error": "没有可解析的文件（请提供 PDF 文件或 URL）"}
        return

    full_text = parsed.get("full_text", "")
    if not full_text:
        yield {"event": "error", "error": "PDF 解析结果为空，请检查文件是否有效"}
        return

    yield {
        "event": "progress",
        "stage": "parsing_done",
        "message": f"PDF 解析完成 ({source})，共 {len(full_text)} 字符",
        "text_length": len(full_text),
        "source": source,
    }

    # ── Stage 2: Build synthetic ArxivPaper ──
    from service.digest_pipeline import _analyze_with_llm, _extract_entities_with_llm, _create_wiki_pages

    paper = ArxivPaper(
        arxiv_id="",
        title=user_title or parsed.get("title") or "User Uploaded Paper",
        authors=_split_authors(parsed.get("authors", "")),
        abstract=parsed.get("abstract", "")[:2000],
        pdf_url=pdf_url or "",
        categories=[],
        published=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )

    # ── Stage 3: LLM analysis ──
    yield {"event": "progress", "stage": "analysis", "message": "正在进行结构化分析..."}

    try:
        analysis = _analyze_with_llm(paper, full_text)
    except Exception as e:
        logger.error("LLM analysis failed: %s", e)
        yield {"event": "error", "error": f"论文分析失败: {e}"}
        return

    # ── Stage 4: Entity extraction ──
    yield {"event": "progress", "stage": "entities", "message": "正在提取关键实体..."}

    try:
        entities = _extract_entities_with_llm(paper, analysis)
    except Exception as e:
        logger.warning("Entity extraction failed, continuing without entities: %s", e)
        entities = {"concepts": [], "methods": [], "datasets": []}

    # ── Stage 5: Create wiki pages ──
    yield {"event": "progress", "stage": "wiki_pages", "message": "正在创建 wiki 页面..."}

    # Resolve pdf_path for source registration
    pdf_path_for_reg = Path(file_path) if file_path else None
    if pdf_path_for_reg is None:
        # For URL-only ingestion without a local file, _create_wiki_pages may
        # fail at RegisterSourceTool. Create an empty placeholder for the flow.
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4 placeholder for URL-only ingest\n")
        tmp.close()
        pdf_path_for_reg = Path(tmp.name)

    try:
        slug, entity_slugs = _create_wiki_pages(
            paper, analysis, pdf_path_for_reg, base_dir, entities,
        )
    except Exception as e:
        logger.error("Wiki page creation failed: %s", e)
        yield {"event": "error", "error": f"Wiki 页面创建失败: {e}"}
        return

    # ── Stage 6: Rebuild index + append log ──
    yield {"event": "progress", "stage": "index", "message": "正在重建索引..."}

    try:
        from tools.wiki_engine_tool import RebuildIndexTool, AppendLogTool

        rebuild = RebuildIndexTool(root_dir=base_dir)
        rebuild._run()

        log_tool = AppendLogTool(root_dir=base_dir)
        log_tool._run(
            operation="ingest",
            detail=f"User uploaded paper: {paper.title} → {slug}",
        )
    except Exception as e:
        logger.warning("Index rebuild / log append failed: %s", e)

    # ── Done ──
    yield {
        "event": "done",
        "paper_slug": slug,
        "entity_slugs": entity_slugs,
        "title": paper.title,
        "wiki_path": f"wiki/papers/{slug}.md",
        "entity_count": len(entity_slugs),
    }


def _split_authors(authors_str: str) -> list[str]:
    """Split author string into a list, handling various separators."""
    if not authors_str:
        return ["Unknown"]
    # Common separators: comma, "and", semicolon
    import re
    parts = re.split(r",|\band\b|;", authors_str)
    return [p.strip() for p in parts if p.strip()] or ["Unknown"]
