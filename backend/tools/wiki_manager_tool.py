"""Wiki page manager — save, list, and read paper wiki pages.

Provides file I/O for the Paper Wiki Agent to persist structured wiki content
as Markdown files with YAML frontmatter, following the AutoSci-main template pattern.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


# ---------------------------------------------------------------------------
# Save wiki page
# ---------------------------------------------------------------------------

class SaveWikiPageInput(BaseModel):
    slug: str = Field(
        ...,
        description=(
            "URL-friendly identifier for the wiki page, lowercase hyphen-separated. "
            "E.g. 'attention-is-all-you-need', 'transformer-architecture'."
        ),
    )
    title: str = Field(..., description="Display title of the wiki page.")
    content: str = Field(
        ...,
        description=(
            "Full Markdown body of the wiki page (without YAML frontmatter). "
            "Should follow the structured template: Overview, Background, Core Problem, "
            "Method, Key Concepts, Experiments, Contributions, Limitations, Related Work."
        ),
    )
    authors: str = Field(default="", description="Paper authors, comma-separated.")
    tags: str = Field(
        default="",
        description="Comma-separated tags for categorization (e.g. 'NLP,transformer,attention').",
    )
    source_pdf: str = Field(
        default="",
        description="Original PDF file path for traceability.",
    )


def _build_frontmatter(
    slug: str,
    title: str,
    authors: str,
    tags: str,
    source_pdf: str,
) -> str:
    """Build YAML frontmatter for a wiki page."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "---",
        f"slug: {slug}",
        f'title: "{title}"',
        f"date_created: {now}",
        f"last_updated: {now}",
    ]
    if authors:
        lines.append(f'authors: "{authors}"')
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        lines.append("tags:")
        for tag in tag_list:
            lines.append(f"  - {tag}")
    if source_pdf:
        lines.append(f"source_pdf: {source_pdf}")
    lines.append("type: paper_wiki")
    lines.append("---")
    return "\n".join(lines)


class SaveWikiPageTool(BaseTool):
    """Save a structured wiki page to the wiki directory."""

    name: str = "save_wiki_page"
    description: str = (
        "Save a paper wiki page as a structured Markdown file with YAML frontmatter. "
        "The page is stored under wiki/papers/<slug>.md. Use this after generating "
        "the full wiki content for a paper."
    )
    args_schema: Type[BaseModel] = SaveWikiPageInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(
        self,
        slug: str,
        title: str,
        content: str,
        authors: str = "",
        tags: str = "",
        source_pdf: str = "",
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        # sanitize slug
        slug = slug.lower().strip().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")[:100]
        if not slug:
            return "Error: invalid slug."

        wiki_dir = self._root_dir / "wiki" / "papers"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        frontmatter = _build_frontmatter(slug, title, authors, tags, source_pdf)
        full_content = f"{frontmatter}\n\n{content}\n"

        out_path = wiki_dir / f"{slug}.md"
        out_path.write_text(full_content, encoding="utf-8")

        # update the wiki index
        self._update_index(wiki_dir.parent, slug, title)

        return f"Wiki page saved: {out_path.relative_to(self._root_dir)}"

    def _update_index(self, wiki_dir: Path, slug: str, title: str) -> None:
        """Append entry to wiki/index.md if not already present."""
        index_path = wiki_dir / "index.md"
        entry = f"- [{title}](papers/{slug}.md)"

        if index_path.exists():
            existing = index_path.read_text(encoding="utf-8")
            if slug in existing:
                return
            if not existing.endswith("\n"):
                existing += "\n"
            index_path.write_text(existing + entry + "\n", encoding="utf-8")
        else:
            header = "# Paper Wiki Index\n\n"
            index_path.write_text(header + entry + "\n", encoding="utf-8")

    async def _arun(
        self,
        slug: str,
        title: str,
        content: str,
        authors: str = "",
        tags: str = "",
        source_pdf: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._run, slug, title, content, authors, tags, source_pdf, None
        )


# ---------------------------------------------------------------------------
# List wiki pages
# ---------------------------------------------------------------------------

class ListWikiPagesInput(BaseModel):
    keyword: str = Field(
        default="",
        description="Optional keyword to filter pages by title or tags.",
    )


class ListWikiPagesTool(BaseTool):
    """List existing paper wiki pages, optionally filtered by keyword."""

    name: str = "list_wiki_pages"
    description: str = (
        "List all paper wiki pages. Optionally filter by keyword (matches title or tags). "
        "Returns a JSON array of page summaries."
    )
    args_schema: Type[BaseModel] = ListWikiPagesInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(
        self,
        keyword: str = "",
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        wiki_dir = self._root_dir / "wiki" / "papers"
        if not wiki_dir.exists():
            return json.dumps([], ensure_ascii=False)

        pages: list[dict] = []
        for md_file in sorted(wiki_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            title = ""
            tags: list[str] = []
            for line in text.split("\n")[:20]:
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line.strip().startswith("- ") and "tags:" in text[:text.index(line)]:
                    tags.append(line.strip().lstrip("- "))

            if keyword:
                kw = keyword.lower()
                if kw not in title.lower() and not any(kw in t.lower() for t in tags):
                    continue

            pages.append(
                {
                    "slug": md_file.stem,
                    "title": title,
                    "tags": tags,
                    "path": str(md_file.relative_to(self._root_dir)).replace("\\", "/"),
                }
            )

        return json.dumps(pages, ensure_ascii=False, indent=2)

    async def _arun(
        self,
        keyword: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        return await asyncio.to_thread(self._run, keyword, None)


# ---------------------------------------------------------------------------
# Read wiki page
# ---------------------------------------------------------------------------

class ReadWikiPageInput(BaseModel):
    slug: str = Field(..., description="Slug of the wiki page to read.")


class ReadWikiPageTool(BaseTool):
    """Read an existing paper wiki page by slug."""

    name: str = "read_wiki_page"
    description: str = (
        "Read the full content of a paper wiki page by its slug. "
        "Returns the complete Markdown including YAML frontmatter."
    )
    args_schema: Type[BaseModel] = ReadWikiPageInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(
        self,
        slug: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        slug = slug.lower().strip().replace(" ", "-")
        path = self._root_dir / "wiki" / "papers" / f"{slug}.md"
        if not path.exists():
            return f"Wiki page not found: {slug}"
        return path.read_text(encoding="utf-8")[:15000]

    async def _arun(
        self,
        slug: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        return await asyncio.to_thread(self._run, slug, None)
