"""Academic Wiki Engine — unified tool for all wiki operations.

Supports the full Karpathy LLM Wiki three-layer architecture:
- Layer 1: Raw source registration with SHA-256 hashing
- Layer 2: Multi-type wiki page CRUD (papers, concepts, methods, datasets, authors, surveys, comparisons)
- Layer 3: Index management, operation logging, lint/health checks

Replaces wiki_manager_tool.py with a single comprehensive tool.
"""

from __future__ import annotations

import asyncio
import hashlib
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

# Valid entity types and their subdirectory names
ENTITY_TYPES = {
    "paper": "papers",
    "concept": "concepts",
    "method": "methods",
    "dataset": "datasets",
    "author": "authors",
    "survey": "surveys",
    "comparison": "comparisons",
    "idea": "ideas",
}

VALID_STATUSES = {"complete", "in_progress", "stub", "proposed", "accepted", "rejected", "abandoned"}
VALID_CONFIDENCES = {"high", "medium", "low"}

# Required frontmatter fields per entity type
REQUIRED_FIELDS = {
    "paper": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "concept": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "method": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "dataset": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "author": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "survey": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "comparison": ["slug", "title", "type", "created", "updated", "status", "confidence"],
    "idea": ["slug", "title", "type", "created", "updated", "status", "confidence"],
}


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sha256_file(file_path: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _strip_related_pages_section(text: str) -> str:
    """Remove existing ## Related Pages section from body text (avoid duplicates)."""
    import re
    pattern = r"\n## Related Pages\n.*?(?=\n## |\Z)"
    return re.sub(pattern, "", text, flags=re.DOTALL).rstrip()


def _build_related_pages_section(related_pages: list[str]) -> str:
    """Build a ## Related Pages section with [[wikilink]] links."""
    lines = ["\n## Related Pages\n"]
    for ref in related_pages:
        bare_slug = ref.split("/")[-1]  # "concepts/foo" → "foo"
        lines.append(f"- [[{bare_slug}]]")
    lines.append("")
    return "\n".join(lines)


def _sanitize_slug(slug: str) -> str:
    """Sanitize a slug to be URL-friendly."""
    slug = slug.lower().strip().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")[:100]
    return slug.strip("-")


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from markdown text."""
    import re
    match = re.match(r"^---\n(.*?)\n---\n?", text, re.DOTALL)
    if not match:
        return {}
    try:
        import yaml
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        # fallback: parse key: value lines
        result = {}
        for line in match.group(1).split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip().strip('"')
        return result


def _build_frontmatter(fields: dict) -> str:
    """Build YAML frontmatter string from fields dict."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            # quote strings that might be parsed as non-strings
            s = str(value)
            if ":" in s or "#" in s or s.startswith('"'):
                lines.append(f'{key}: "{s}"')
            else:
                lines.append(f"{key}: {s}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Register Source Tool
# ---------------------------------------------------------------------------

class RegisterSourceInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Path to the source file (PDF, MD, BibTeX). Absolute or relative to project root.",
    )
    title: str = Field(default="", description="Paper/source title.")
    authors: str = Field(default="", description="Comma-separated authors.")
    year: int = Field(default=0, description="Publication year.")
    venue: str = Field(default="", description="Conference or journal name.")
    arxiv_id: str = Field(default="", description="arXiv ID (e.g. 2301.00001).")
    doi: str = Field(default="", description="DOI identifier.")


class RegisterSourceTool(BaseTool):
    """Register a raw source file with metadata and SHA-256 hash. Copies file to raw/sources/."""

    name: str = "register_source"
    description: str = (
        "Register a raw source file (PDF, Markdown, BibTeX) into the wiki. "
        "Computes SHA-256 hash, copies to raw/sources/, and appends to manifest.jsonl. "
        "Returns source metadata including hash. Skips if hash already exists."
    )
    args_schema: Type[BaseModel] = RegisterSourceInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(
        self,
        file_path: str,
        title: str = "",
        authors: str = "",
        year: int = 0,
        venue: str = "",
        arxiv_id: str = "",
        doi: str = "",
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        # resolve path
        p = Path(file_path)
        if not p.is_absolute():
            p = self._root_dir / file_path
        p = p.resolve()

        if not p.exists():
            return json.dumps({"error": f"File not found: {file_path}"})

        # compute hash
        file_hash = _sha256_file(str(p))

        # check manifest for duplicate
        manifest_dir = self._root_dir / "raw" / "sources"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "manifest.jsonl"

        if manifest_path.exists():
            for line in manifest_path.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("hash") == file_hash:
                        return json.dumps({
                            "status": "already_registered",
                            "hash": file_hash,
                            "message": f"Source already registered: {entry.get('slug', 'unknown')}",
                        }, ensure_ascii=False)
                except json.JSONDecodeError:
                    continue

        # copy file to raw/sources/
        import shutil
        dest = manifest_dir / p.name
        if not dest.exists():
            shutil.copy2(str(p), str(dest))

        # create manifest entry
        slug = _sanitize_slug(title) if title else _sanitize_slug(p.stem)
        entry = {
            "slug": slug,
            "title": title or p.stem,
            "authors": [a.strip() for a in authors.split(",") if a.strip()] if authors else [],
            "year": year,
            "venue": venue,
            "arxiv_id": arxiv_id,
            "doi": doi,
            "hash": file_hash,
            "original_path": file_path,
            "stored_path": str(dest.relative_to(self._root_dir)).replace("\\", "/"),
            "ingest_time": _now_str(),
            "file_size_bytes": p.stat().st_size,
        }

        # append to manifest
        with open(manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return json.dumps(entry, ensure_ascii=False, indent=2)

    async def _arun(self, file_path: str, title: str = "", authors: str = "", year: int = 0, venue: str = "", arxiv_id: str = "", doi: str = "", run_manager: AsyncCallbackManagerForToolRun | None = None) -> str:
        return await asyncio.to_thread(self._run, file_path, title, authors, year, venue, arxiv_id, doi, None)


# ---------------------------------------------------------------------------
# Save Wiki Page Tool (extended for all entity types)
# ---------------------------------------------------------------------------

class SaveWikiPageInput(BaseModel):
    slug: str = Field(..., description="URL-friendly identifier, lowercase-hyphenated.")
    title: str = Field(..., description="Display title.")
    entity_type: str = Field(
        default="paper",
        description="Entity type: paper, concept, method, dataset, author, survey, comparison.",
    )
    content: str = Field(..., description="Full Markdown body (without frontmatter).")
    authors: str = Field(default="", description="Comma-separated authors (for paper type).")
    year: int = Field(default=0, description="Publication year (for paper type).")
    venue: str = Field(default="", description="Conference/journal (for paper type).")
    arxiv_id: str = Field(default="", description="arXiv ID.")
    tags: str = Field(default="", description="Comma-separated tags.")
    related_pages: str = Field(default="", description="Comma-separated paths to related wiki pages.")
    source_hash: str = Field(default="", description="SHA-256 hash of the source file.")
    status: str = Field(default="complete", description="Status: complete, in_progress, stub.")
    confidence: str = Field(default="high", description="Confidence: high, medium, low.")
    source_count: int = Field(default=1, description="Number of contributing sources.")
    origin_paper: str = Field(default="", description="Slug of the paper that inspired this idea (for idea type).")
    addresses_gap: str = Field(default="", description="The research gap this idea addresses (for idea type).")
    priority: str = Field(default="", description="Priority level: high, medium, low (for idea type).")
    generation_path: str = Field(default="", description="Ideation path: gap_driven, incremental, combination, cross_pollination (for idea type).")


class SaveWikiPageTool(BaseTool):
    """Save a wiki page of any entity type with full frontmatter."""

    name: str = "save_wiki_page"
    description: str = (
        "Save a wiki page (paper, concept, method, dataset, author, survey, comparison, or idea) "
        "with structured YAML frontmatter. Automatically updates wiki/index.md."
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
        entity_type: str = "paper",
        content: str = "",
        authors: str = "",
        year: int = 0,
        venue: str = "",
        arxiv_id: str = "",
        tags: str = "",
        related_pages: str = "",
        source_hash: str = "",
        status: str = "complete",
        confidence: str = "high",
        source_count: int = 1,
        origin_paper: str = "",
        addresses_gap: str = "",
        priority: str = "",
        generation_path: str = "",
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        slug = _sanitize_slug(slug)
        if not slug:
            return json.dumps({"error": "Invalid slug"})

        if entity_type not in ENTITY_TYPES:
            return json.dumps({"error": f"Unknown entity type: {entity_type}. Valid: {list(ENTITY_TYPES.keys())}"})

        subdir = ENTITY_TYPES[entity_type]
        wiki_dir = self._root_dir / "wiki" / subdir
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # check if page already exists (for update detection)
        page_path = wiki_dir / f"{slug}.md"
        is_update = page_path.exists()

        # build frontmatter
        now = _today_str()
        fields: dict = {
            "slug": slug,
            "title": title,
            "type": entity_type,
            "created": now,
            "updated": now,
            "source_count": source_count,
            "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else [],
            "related_pages": [r.strip() for r in related_pages.split(",") if r.strip()] if related_pages else [],
            "status": status,
            "confidence": confidence,
        }

        if entity_type == "paper":
            if authors:
                fields["authors"] = [a.strip() for a in authors.split(",") if a.strip()]
            if year:
                fields["year"] = year
            if venue:
                fields["venue"] = venue
            if arxiv_id:
                fields["arxiv_id"] = arxiv_id
        if entity_type == "idea":
            if origin_paper:
                fields["origin_paper"] = origin_paper
            if addresses_gap:
                fields["addresses_gap"] = addresses_gap
            if priority:
                fields["priority"] = priority
            if generation_path:
                fields["generation_path"] = generation_path
        if source_hash:
            fields["source_hash"] = source_hash

        # if updating, preserve original created date
        if is_update:
            old_meta = _parse_frontmatter(page_path.read_text(encoding="utf-8"))
            if old_meta.get("created"):
                fields["created"] = old_meta["created"]

        frontmatter = _build_frontmatter(fields)

        # Append ## Related Pages section with [[wikilink]] links for Obsidian
        related_list = [r.strip() for r in related_pages.split(",") if r.strip()] if related_pages else []
        body = _strip_related_pages_section(content) if related_list else content
        if related_list:
            full_content = f"{frontmatter}\n\n{body}\n{_build_related_pages_section(related_list)}\n"
        else:
            full_content = f"{frontmatter}\n\n{body}\n"

        page_path.write_text(full_content, encoding="utf-8")

        return json.dumps({
            "status": "updated" if is_update else "created",
            "path": str(page_path.relative_to(self._root_dir)).replace("\\", "/"),
            "slug": slug,
            "type": entity_type,
        }, ensure_ascii=False)

    async def _arun(self, **kwargs) -> str:
        kwargs.pop("run_manager", None)
        return await asyncio.to_thread(self._run, **kwargs)


# ---------------------------------------------------------------------------
# Read Wiki Page Tool (extended for all entity types)
# ---------------------------------------------------------------------------

class ReadWikiPageInput(BaseModel):
    slug: str = Field(..., description="Slug of the wiki page to read.")
    entity_type: str = Field(
        default="",
        description="Entity type (paper, concept, etc.). If empty, searches all types.",
    )


class ReadWikiPageTool(BaseTool):
    """Read a wiki page by slug, optionally specifying entity type."""

    name: str = "read_wiki_page"
    description: str = (
        "Read a wiki page by slug. If entity_type is specified, looks only in that subdirectory. "
        "Otherwise searches all entity types. Returns full Markdown with frontmatter."
    )
    args_schema: Type[BaseModel] = ReadWikiPageInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(self, slug: str, entity_type: str = "", run_manager=None) -> str:
        slug = _sanitize_slug(slug)
        wiki_dir = self._root_dir / "wiki"

        if entity_type and entity_type in ENTITY_TYPES:
            path = wiki_dir / ENTITY_TYPES[entity_type] / f"{slug}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")[:15000]
            return f"Page not found: {entity_type}/{slug}"

        # search all types
        for subdir in ENTITY_TYPES.values():
            path = wiki_dir / subdir / f"{slug}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")[:15000]

        return f"Page not found: {slug}"

    async def _arun(self, slug: str, entity_type: str = "", run_manager=None) -> str:
        return await asyncio.to_thread(self._run, slug, entity_type, None)


# ---------------------------------------------------------------------------
# List Wiki Pages Tool (extended for all entity types)
# ---------------------------------------------------------------------------

class ListWikiPagesInput(BaseModel):
    entity_type: str = Field(
        default="",
        description="Filter by entity type. Empty returns all types.",
    )
    keyword: str = Field(default="", description="Optional keyword filter (matches title or tags).")


class ListWikiPagesTool(BaseTool):
    """List wiki pages, optionally filtered by entity type and keyword."""

    name: str = "list_wiki_pages"
    description: str = (
        "List wiki pages. Filter by entity_type (paper, concept, method, etc.) "
        "and/or keyword. Returns JSON array with slug, title, type, status."
    )
    args_schema: Type[BaseModel] = ListWikiPagesInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(self, entity_type: str = "", keyword: str = "", run_manager=None) -> str:
        wiki_dir = self._root_dir / "wiki"
        pages: list[dict] = []

        types_to_scan = (
            {entity_type: ENTITY_TYPES[entity_type]}
            if entity_type and entity_type in ENTITY_TYPES
            else ENTITY_TYPES
        )

        for etype, subdir in types_to_scan.items():
            type_dir = wiki_dir / subdir
            if not type_dir.exists():
                continue
            for md_file in sorted(type_dir.glob("*.md")):
                text = md_file.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                title = meta.get("title", md_file.stem)
                tags = meta.get("tags", [])
                status = meta.get("status", "unknown")

                if keyword:
                    kw = keyword.lower()
                    if kw not in title.lower() and not any(kw in str(t).lower() for t in tags):
                        continue

                pages.append({
                    "slug": md_file.stem,
                    "title": title,
                    "type": etype,
                    "status": status,
                    "tags": tags,
                    "path": str(md_file.relative_to(self._root_dir)).replace("\\", "/"),
                })

        return json.dumps(pages, ensure_ascii=False, indent=2)

    async def _arun(self, entity_type: str = "", keyword: str = "", run_manager=None) -> str:
        return await asyncio.to_thread(self._run, entity_type, keyword, None)


# ---------------------------------------------------------------------------
# Rebuild Index Tool
# ---------------------------------------------------------------------------

class RebuildIndexInput(BaseModel):
    pass


class RebuildIndexTool(BaseTool):
    """Rebuild wiki/index.md from actual wiki page files."""

    name: str = "rebuild_index"
    description: str = (
        "Rebuild wiki/index.md by scanning all wiki page files. "
        "Groups entries by entity type with titles and links."
    )
    args_schema: Type[BaseModel] = RebuildIndexInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(self, run_manager=None) -> str:
        wiki_dir = self._root_dir / "wiki"
        lines = [
            "# Academic Paper Wiki Index",
            "",
            f"> Auto-generated by wiki engine. Last rebuilt: {_now_str()}",
            "",
        ]

        # Collect data for context_brief
        type_counts: dict[str, int] = {}
        all_tags: dict[str, list[str]] = {}  # tag -> [titles]
        recent_pages: list[tuple[str, str, str]] = []  # (title, path, date)

        total = 0
        for etype, subdir in ENTITY_TYPES.items():
            type_dir = wiki_dir / subdir
            header = etype.title() + "s"
            lines.append(f"## {header}")
            lines.append("")

            if not type_dir.exists():
                lines.append(f"*No {header.lower()} yet.*")
                lines.append("")
                type_counts[etype] = 0
                continue

            md_files = sorted(type_dir.glob("*.md"))
            if not md_files:
                lines.append(f"*No {header.lower()} yet.*")
                lines.append("")
                type_counts[etype] = 0
                continue

            type_counts[etype] = len(md_files)
            for md_file in md_files:
                text = md_file.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                title = meta.get("title", md_file.stem)
                status_icon = {"complete": "[x]", "in_progress": "[~]", "stub": "[ ]"}.get(
                    meta.get("status", ""), "[?]"
                )
                lines.append(f"- {status_icon} [{title}]({subdir}/{md_file.name})")
                total += 1

                # collect tags for context_brief
                for tag in meta.get("tags", []):
                    all_tags.setdefault(tag, []).append(title)

                # collect recent pages
                created = meta.get("created", "")
                if created:
                    recent_pages.append((title, f"{subdir}/{md_file.name}", created))

            lines.append("")

        index_path = wiki_dir / "index.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Generate context_brief.md
        self._generate_context_brief(wiki_dir, type_counts, all_tags, recent_pages, total)
        self._generate_open_questions(wiki_dir)

        return json.dumps({
            "status": "rebuilt",
            "total_pages": total,
            "path": "wiki/index.md",
        })

    def _generate_context_brief(
        self,
        wiki_dir: Path,
        type_counts: dict[str, int],
        all_tags: dict[str, list[str]],
        recent_pages: list[tuple[str, str, str]],
        total: int,
    ) -> None:
        """Generate wiki/graph/context_brief.md — a global summary for agent context."""
        graph_dir = wiki_dir / "graph"
        graph_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            "# Wiki Knowledge Context Brief",
            "",
            f"> Auto-generated by wiki engine. Last updated: {_today_str()}",
            "",
            "## Statistics",
            "",
            f"- **Total pages**: {total}",
        ]

        for etype in ENTITY_TYPES:
            count = type_counts.get(etype, 0)
            if count > 0:
                lines.append(f"- **{etype.title()}s**: {count}")

        lines.append("")

        # Top tags
        if all_tags:
            lines.append("## Research Topics")
            lines.append("")
            sorted_tags = sorted(all_tags.items(), key=lambda x: len(x[1]), reverse=True)
            for tag, titles in sorted_tags[:10]:
                title_list = ", ".join(titles[:5])
                if len(titles) > 5:
                    title_list += f" (+{len(titles) - 5} more)"
                lines.append(f"- **{tag}** ({len(titles)}): {title_list}")
            lines.append("")

        # Recent pages (last 5)
        if recent_pages:
            recent_pages.sort(key=lambda x: x[2], reverse=True)
            lines.append("## Recent Pages")
            lines.append("")
            for title, path, date in recent_pages[:5]:
                lines.append(f"- [{title}]({path}) — {date}")
            lines.append("")

        brief_path = graph_dir / "context_brief.md"
        brief_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _generate_open_questions(self, wiki_dir: Path) -> None:
        """Generate wiki/graph/open_questions.md — aggregated research gaps for ideation."""
        import re

        graph_dir = wiki_dir / "graph"
        graph_dir.mkdir(parents=True, exist_ok=True)

        gaps: list[dict] = []

        # Scan papers for Open Questions and Limitations sections
        papers_dir = wiki_dir / "papers"
        if papers_dir.exists():
            for md_file in sorted(papers_dir.glob("*.md")):
                text = md_file.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                title = meta.get("title", md_file.stem)

                open_qs = self._extract_section(text, "Open Questions")
                if open_qs:
                    for q in self._bullet_items(open_qs):
                        gaps.append({
                            "source": f"papers/{md_file.name}",
                            "source_title": title,
                            "type": "open_question",
                            "text": q,
                        })

                limits = self._extract_section(text, "Limitations")
                if limits:
                    for lim in self._bullet_items(limits):
                        gaps.append({
                            "source": f"papers/{md_file.name}",
                            "source_title": title,
                            "type": "limitation",
                            "text": lim,
                        })

        # Scan methods for Limitations section
        methods_dir = wiki_dir / "methods"
        if methods_dir.exists():
            for md_file in sorted(methods_dir.glob("*.md")):
                text = md_file.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                title = meta.get("title", md_file.stem)

                limits = self._extract_section(text, "Limitations")
                if limits:
                    for lim in self._bullet_items(limits):
                        gaps.append({
                            "source": f"methods/{md_file.name}",
                            "source_title": title,
                            "type": "limitation",
                            "text": lim,
                        })

        lines = [
            "# Open Questions & Research Gaps",
            "",
            f"> Auto-generated by wiki engine. Last updated: {_today_str()}",
            "",
            "This file aggregates Open Questions from papers and Limitations from papers and methods.",
            "It serves as input for the `/ideate` skill to identify research opportunities.",
            "",
        ]

        if not gaps:
            lines.append("*No gaps found yet. Import more papers to build the gap map.*")
        else:
            open_qs = [g for g in gaps if g["type"] == "open_question"]
            limitations = [g for g in gaps if g["type"] == "limitation"]

            if open_qs:
                lines.append(f"## Open Questions ({len(open_qs)})")
                lines.append("")
                for g in open_qs:
                    lines.append(f"- **{g['text']}**")
                    lines.append(f"  - Source: [{g['source_title']}]({g['source']})")
                lines.append("")

            if limitations:
                lines.append(f"## Limitations ({len(limitations)})")
                lines.append("")
                for g in limitations:
                    lines.append(f"- **{g['text']}**")
                    lines.append(f"  - Source: [{g['source_title']}]({g['source']})")
                lines.append("")

            lines.append("## Summary")
            lines.append("")
            lines.append(f"- Total gaps: {len(gaps)}")
            lines.append(f"- Open questions: {len(open_qs)}")
            lines.append(f"- Limitations: {len(limitations)}")

            source_counts: dict[str, int] = {}
            for g in gaps:
                source_counts[g["source_title"]] = source_counts.get(g["source_title"], 0) + 1
            lines.append("")
            lines.append("### Gap Distribution by Source")
            lines.append("")
            for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"- {source}: {count} gap(s)")

        oq_path = graph_dir / "open_questions.md"
        oq_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _extract_section(text: str, heading: str) -> str:
        """Extract content under a ## heading until the next ## heading or end."""
        import re
        pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
        match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _bullet_items(text: str) -> list[str]:
        """Extract bullet point items from a section's text."""
        import re
        items = []
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                item = line[2:].strip()
                item = re.sub(r"\[confidence:.*?\]", "", item).strip()
                if item:
                    items.append(item)
        return items

    async def _arun(self, run_manager=None) -> str:
        return await asyncio.to_thread(self._run, None)


# ---------------------------------------------------------------------------
# Append Log Tool
# ---------------------------------------------------------------------------

class AppendLogInput(BaseModel):
    operation: str = Field(..., description="Operation type: ingest, update, query, lint, fix.")
    details: str = Field(..., description="Human-readable details of the operation.")
    pages_affected: str = Field(
        default="",
        description="Comma-separated list of affected wiki page paths.",
    )


class AppendLogTool(BaseTool):
    """Append an entry to the wiki operation log."""

    name: str = "append_log"
    description: str = (
        "Append an entry to wiki/log.md. Use after any wiki-modifying operation "
        "(ingest, update, lint fix) for audit trail."
    )
    args_schema: Type[BaseModel] = AppendLogInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(self, operation: str, details: str, pages_affected: str = "", run_manager=None) -> str:
        log_path = self._root_dir / "wiki" / "log.md"
        if not log_path.exists():
            log_path.write_text("# Wiki Operation Log\n\n> Append-only log of all wiki operations.\n\n", encoding="utf-8")

        entry_lines = [
            f"## {_now_str()} — {operation}",
            f"- {details}",
        ]
        if pages_affected:
            for page in pages_affected.split(","):
                page = page.strip()
                if page:
                    entry_lines.append(f"  - Affected: `{page}`")
        entry_lines.append("")

        existing = log_path.read_text(encoding="utf-8")
        log_path.write_text(existing + "\n".join(entry_lines), encoding="utf-8")

        return json.dumps({"status": "logged", "operation": operation})

    async def _arun(self, operation: str, details: str, pages_affected: str = "", run_manager=None) -> str:
        return await asyncio.to_thread(self._run, operation, details, pages_affected, None)


# ---------------------------------------------------------------------------
# Lint Wiki Tool
# ---------------------------------------------------------------------------

class LintWikiInput(BaseModel):
    auto_fix: bool = Field(
        default=False,
        description="If true, automatically fix safe issues (e.g., rebuild index).",
    )
    backfill: bool = Field(
        default=False,
        description="If true, scan existing papers and create missing concept/method/dataset pages.",
    )


class LintWikiTool(BaseTool):
    """Run health checks on the wiki and report issues."""

    name: str = "lint_wiki"
    description: str = (
        "Scan the wiki for health issues: orphan pages, missing backlinks, "
        "index mismatches, missing frontmatter, invalid statuses. "
        "Returns a JSON report with severity levels (red/yellow/blue). "
        "Set auto_fix=true to automatically fix safe issues. "
        "Set backfill=true to scan existing papers and create missing concept/method/dataset pages."
    )
    args_schema: Type[BaseModel] = LintWikiInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(self, auto_fix: bool = False, backfill: bool = False, run_manager=None) -> str:
        wiki_dir = self._root_dir / "wiki"
        issues: list[dict] = []

        # Collect all pages and their metadata
        all_pages: dict[str, dict] = {}  # slug -> {path, meta, type}
        all_slugs: set[str] = set()

        for etype, subdir in ENTITY_TYPES.items():
            type_dir = wiki_dir / subdir
            if not type_dir.exists():
                continue
            for md_file in sorted(type_dir.glob("*.md")):
                text = md_file.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                slug = md_file.stem
                all_slugs.add(slug)
                all_pages[slug] = {
                    "path": str(md_file.relative_to(self._root_dir)).replace("\\", "/"),
                    "meta": meta,
                    "type": etype,
                    "text": text,
                }

                # Check missing frontmatter
                if not meta:
                    issues.append({
                        "severity": "red",
                        "type": "missing_frontmatter",
                        "page": slug,
                        "message": f"Page {slug} has no YAML frontmatter",
                    })

                # Check required fields
                req_fields = REQUIRED_FIELDS.get(etype, [])
                for field in req_fields:
                    if field not in meta:
                        issues.append({
                            "severity": "red",
                            "type": "missing_field",
                            "page": slug,
                            "message": f"Page {slug} missing required field: {field}",
                        })

                # Check invalid status
                status = meta.get("status", "")
                if status and status not in VALID_STATUSES:
                    issues.append({
                        "severity": "yellow",
                        "type": "invalid_status",
                        "page": slug,
                        "message": f"Page {slug} has invalid status: {status}",
                    })

                # Check invalid confidence
                conf = meta.get("confidence", "")
                if conf and conf not in VALID_CONFIDENCES:
                    issues.append({
                        "severity": "yellow",
                        "type": "invalid_confidence",
                        "page": slug,
                        "message": f"Page {slug} has invalid confidence: {conf}",
                    })

        # Check backlinks: every related_pages entry should point to an existing page
        for slug, info in all_pages.items():
            related = info["meta"].get("related_pages", [])
            for ref in related:
                ref_slug = ref.split("/")[-1].replace(".md", "")
                if ref_slug not in all_slugs:
                    issues.append({
                        "severity": "yellow",
                        "type": "dangling_reference",
                        "page": slug,
                        "message": f"Page {slug} references non-existent page: {ref}",
                    })

        # Check orphan pages: pages not referenced by any other page
        referenced_slugs: set[str] = set()
        for info in all_pages.values():
            for ref in info["meta"].get("related_pages", []):
                ref_slug = ref.split("/")[-1].replace(".md", "")
                referenced_slugs.add(ref_slug)

        for slug in all_slugs:
            if slug not in referenced_slugs and all_pages[slug]["type"] != "paper":
                issues.append({
                    "severity": "blue",
                    "type": "orphan_page",
                    "page": slug,
                    "message": f"Page {slug} is not referenced by any other page",
                })

        # Check index.md consistency
        index_path = wiki_dir / "index.md"
        if index_path.exists():
            index_text = index_path.read_text(encoding="utf-8")
            for slug in all_slugs:
                if slug not in index_text:
                    issues.append({
                        "severity": "yellow",
                        "type": "index_mismatch",
                        "page": slug,
                        "message": f"Page {slug} exists but not in index.md",
                    })

        # Auto-fix safe issues
        fixes_applied: list[str] = []
        if auto_fix:
            import re as _re

            # 1. Fix missing_frontmatter: add default frontmatter
            for issue in [i for i in issues if i["type"] == "missing_frontmatter"]:
                slug = issue["page"]
                info = all_pages.get(slug)
                if not info:
                    continue
                page_path = self._root_dir / info["path"]
                text = info["text"]
                entity_type = info["type"]
                now = _today_str()
                fields = {
                    "slug": slug,
                    "title": slug.replace("-", " ").title(),
                    "type": entity_type,
                    "created": now,
                    "updated": now,
                    "status": "stub",
                    "confidence": "low",
                    "source_count": 0,
                    "tags": [],
                    "related_pages": [],
                }
                frontmatter = _build_frontmatter(fields)
                page_path.write_text(f"{frontmatter}\n\n{text}\n", encoding="utf-8")
                fixes_applied.append(f"Added frontmatter to {slug}")

            # 2. Fix missing_field: patch required fields with defaults
            #    Skip pages that already got full frontmatter in step 1
            fm_fixed_slugs = {i["page"] for i in issues if i["type"] == "missing_frontmatter"}
            for issue in [i for i in issues if i["type"] == "missing_field" and i["page"] not in fm_fixed_slugs]:
                slug = issue["page"]
                info = all_pages.get(slug)
                if not info:
                    continue
                page_path = self._root_dir / info["path"]
                text = page_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = _re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=_re.DOTALL).lstrip("\n")
                now = _today_str()
                changed = False
                defaults = {
                    "slug": slug,
                    "title": meta.get("title", slug.replace("-", " ").title()),
                    "type": info["type"],
                    "created": now,
                    "updated": now,
                    "status": "stub",
                    "confidence": "low",
                    "source_count": 0,
                    "tags": [],
                    "related_pages": [],
                }
                field_name = issue["message"].split("missing required field: ")[-1] if "missing required field:" in issue["message"] else ""
                if field_name and field_name not in meta:
                    meta[field_name] = defaults.get(field_name, "")
                    changed = True
                if changed:
                    frontmatter = _build_frontmatter(meta)
                    page_path.write_text(f"{frontmatter}\n\n{body}", encoding="utf-8")
                    fixes_applied.append(f"Filled missing field '{field_name}' in {slug}")

            # 3. Fix invalid_status: reset to in_progress
            for issue in [i for i in issues if i["type"] == "invalid_status"]:
                slug = issue["page"]
                info = all_pages.get(slug)
                if not info:
                    continue
                page_path = self._root_dir / info["path"]
                text = page_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = _re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=_re.DOTALL).lstrip("\n")
                meta["status"] = "in_progress"
                meta["updated"] = _today_str()
                frontmatter = _build_frontmatter(meta)
                page_path.write_text(f"{frontmatter}\n\n{body}", encoding="utf-8")
                fixes_applied.append(f"Reset status to 'in_progress' in {slug}")

            # 4. Fix invalid_confidence: reset to medium
            for issue in [i for i in issues if i["type"] == "invalid_confidence"]:
                slug = issue["page"]
                info = all_pages.get(slug)
                if not info:
                    continue
                page_path = self._root_dir / info["path"]
                text = page_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = _re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=_re.DOTALL).lstrip("\n")
                meta["confidence"] = "medium"
                meta["updated"] = _today_str()
                frontmatter = _build_frontmatter(meta)
                page_path.write_text(f"{frontmatter}\n\n{body}", encoding="utf-8")
                fixes_applied.append(f"Reset confidence to 'medium' in {slug}")

            # 5. Fix dangling_reference: remove invalid related_pages entries
            for issue in [i for i in issues if i["type"] == "dangling_reference"]:
                slug = issue["page"]
                info = all_pages.get(slug)
                if not info:
                    continue
                page_path = self._root_dir / info["path"]
                text = page_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = _re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=_re.DOTALL).lstrip("\n")
                # Extract the dangling ref from the message
                msg = issue["message"]
                ref_match = _re.search(r"references non-existent page: (.+)$", msg)
                if ref_match:
                    dangling = ref_match.group(1).strip()
                    related = meta.get("related_pages", [])
                    if dangling in related:
                        related.remove(dangling)
                        meta["related_pages"] = related
                        meta["updated"] = _today_str()
                        frontmatter = _build_frontmatter(meta)
                        page_path.write_text(f"{frontmatter}\n\n{body}", encoding="utf-8")
                        fixes_applied.append(f"Removed dangling ref '{dangling}' from {slug}")

            # 6. Rebuild index if there are mismatches
            index_issues = [i for i in issues if i["type"] == "index_mismatch"]
            if index_issues:
                lines = [
                    "# Academic Paper Wiki Index",
                    "",
                    f"> Auto-generated by wiki engine. Last rebuilt: {_now_str()}",
                    "",
                ]
                for etype, subdir in ENTITY_TYPES.items():
                    type_dir = wiki_dir / subdir
                    header = etype.title() + "s"
                    lines.append(f"## {header}")
                    lines.append("")
                    md_files = sorted(type_dir.glob("*.md")) if type_dir.exists() else []
                    if not md_files:
                        lines.append(f"*No {header.lower()} yet.*")
                        lines.append("")
                        continue
                    for md_file in md_files:
                        meta = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                        title = meta.get("title", md_file.stem)
                        lines.append(f"- [{title}]({subdir}/{md_file.name})")
                    lines.append("")
                index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                fixes_applied.append("Rebuilt index.md")

            # 7. Add missing Related Pages section to pages with related_pages
            for slug, info in all_pages.items():
                related = info["meta"].get("related_pages", [])
                if not related:
                    continue
                if "## Related Pages" in info["text"]:
                    continue  # already has it
                page_path = self._root_dir / info["path"]
                text = page_path.read_text(encoding="utf-8")
                meta = _parse_frontmatter(text)
                body = _re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=_re.DOTALL).lstrip("\n")
                meta["updated"] = _today_str()
                frontmatter = _build_frontmatter(meta)
                related_section = _build_related_pages_section(related)
                page_path.write_text(f"{frontmatter}\n\n{body.rstrip()}\n{related_section}\n", encoding="utf-8")
                fixes_applied.append(f"Added Related Pages section to {slug}")

        # Backfill: create missing entity pages for existing papers
        backfill_results: list[str] = []
        if backfill:
            import re as _re
            from service.digest_pipeline import _extract_entities_with_llm, _get_llm
            from service.arxiv_service import ArxivPaper

            papers_dir = wiki_dir / "papers"
            if papers_dir.exists():
                # collect existing entity slugs
                existing_entities: set[str] = set()
                for etype in ("concepts", "methods", "datasets"):
                    edir = wiki_dir / etype
                    if edir.exists():
                        for f in edir.glob("*.md"):
                            existing_entities.add(f.stem)

                for paper_file in sorted(papers_dir.glob("*.md")):
                    text = paper_file.read_text(encoding="utf-8")
                    meta = _parse_frontmatter(text)
                    paper_slug = paper_file.stem

                    # extract paper info for entity extraction
                    paper_title = meta.get("title", paper_slug)
                    paper_authors = meta.get("authors", [])
                    if isinstance(paper_authors, str):
                        paper_authors = [a.strip() for a in paper_authors.split(",")]
                    arxiv_id = meta.get("arxiv_id", "")
                    published = str(meta.get("created", ""))

                    # get the analysis content (body after frontmatter)
                    body = _re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=_re.DOTALL).strip()

                    # skip if paper already has related entity pages
                    related = meta.get("related_pages", [])
                    has_entities = any(
                        r.startswith(("concepts/", "methods/", "datasets/"))
                        for r in related
                    )
                    if has_entities:
                        continue

                    # create a minimal ArxivPaper for the extractor
                    paper_obj = ArxivPaper(
                        arxiv_id=arxiv_id,
                        title=paper_title,
                        authors=paper_authors,
                        abstract=body[:2000],
                        pdf_url="",
                        categories=meta.get("tags", []),
                        published=published,
                    )

                    try:
                        entities = _extract_entities_with_llm(paper_obj, body[:8000])
                    except Exception as e:
                        logger.warning("Backfill entity extraction failed for %s: %s", paper_slug, e)
                        continue

                    new_related = list(related)
                    created_count = 0

                    # create concept pages
                    for concept in entities.get("concepts", []):
                        c_slug = _sanitize_slug(concept.get("slug", concept.get("name", "")))
                        if not c_slug or c_slug in existing_entities:
                            # just add backlink
                            if c_slug:
                                c_path = wiki_dir / "concepts" / f"{c_slug}.md"
                                if c_path.exists():
                                    existing = c_path.read_text(encoding="utf-8")
                                    if paper_slug not in existing:
                                        c_path.write_text(
                                            existing.rstrip() + f"\n- [[{paper_slug}]]\n",
                                            encoding="utf-8",
                                        )
                            continue
                        c_path = wiki_dir / "concepts" / f"{c_slug}.md"
                        c_path.parent.mkdir(parents=True, exist_ok=True)
                        c_content = (
                            f"## Definition\n\n{concept.get('definition', '')}\n\n"
                            f"## Key Papers\n\n- [[{paper_slug}]]\n"
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
                            "related_pages": [paper_slug],
                        })
                        c_path.write_text(f"{c_fm}\n\n{c_content}\n", encoding="utf-8")
                        existing_entities.add(c_slug)
                        new_related.append(f"concepts/{c_slug}")
                        created_count += 1

                    # create method pages
                    for method in entities.get("methods", []):
                        m_slug = _sanitize_slug(method.get("slug", method.get("name", "")))
                        if not m_slug or m_slug in existing_entities:
                            if m_slug:
                                m_path = wiki_dir / "methods" / f"{m_slug}.md"
                                if m_path.exists():
                                    existing = m_path.read_text(encoding="utf-8")
                                    if paper_slug not in existing:
                                        m_path.write_text(
                                            existing.rstrip() + f"\n- [[{paper_slug}]]\n",
                                            encoding="utf-8",
                                        )
                            continue
                        m_path = wiki_dir / "methods" / f"{m_slug}.md"
                        m_path.parent.mkdir(parents=True, exist_ok=True)
                        m_content = (
                            f"## Mechanism\n\n{method.get('mechanism', '')}\n\n"
                            f"## Evaluated By\n\n- [[{paper_slug}]]\n"
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
                            "related_pages": [paper_slug],
                        })
                        m_path.write_text(f"{m_fm}\n\n{m_content}\n", encoding="utf-8")
                        existing_entities.add(m_slug)
                        new_related.append(f"methods/{m_slug}")
                        created_count += 1

                    # create dataset pages
                    for ds in entities.get("datasets", []):
                        d_slug = _sanitize_slug(ds.get("slug", ds.get("name", "")))
                        if not d_slug or d_slug in existing_entities:
                            if d_slug:
                                d_path = wiki_dir / "datasets" / f"{d_slug}.md"
                                if d_path.exists():
                                    existing = d_path.read_text(encoding="utf-8")
                                    if paper_slug not in existing:
                                        d_path.write_text(
                                            existing.rstrip() + f"\n- [[{paper_slug}]]\n",
                                            encoding="utf-8",
                                        )
                            continue
                        d_path = wiki_dir / "datasets" / f"{d_slug}.md"
                        d_path.parent.mkdir(parents=True, exist_ok=True)
                        d_content = (
                            f"## Overview\n\n{ds.get('description', '')}\n\n"
                            f"## Used In\n\n- [[{paper_slug}]]\n"
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
                            "related_pages": [paper_slug],
                        })
                        d_path.write_text(f"{d_fm}\n\n{d_content}\n", encoding="utf-8")
                        existing_entities.add(d_slug)
                        new_related.append(f"datasets/{d_slug}")
                        created_count += 1

                    # update paper's related_pages if new entities were created
                    if created_count > 0 and set(new_related) != set(related):
                        meta["related_pages"] = new_related
                        meta["updated"] = _today_str()
                        frontmatter = _build_frontmatter(meta)
                        paper_file.write_text(f"{frontmatter}\n\n{body}\n", encoding="utf-8")
                        backfill_results.append(f"{paper_slug}: +{created_count} entities")

            # rebuild index after backfill
            if backfill_results:
                try:
                    lines = [
                        "# Academic Paper Wiki Index",
                        "",
                        f"> Auto-generated by wiki engine. Last rebuilt: {_now_str()}",
                        "",
                    ]
                    for etype, subdir in ENTITY_TYPES.items():
                        type_dir = wiki_dir / subdir
                        header = etype.title() + "s"
                        lines.append(f"## {header}")
                        lines.append("")
                        md_files = sorted(type_dir.glob("*.md")) if type_dir.exists() else []
                        if not md_files:
                            lines.append(f"*No {header.lower()} yet.*")
                            lines.append("")
                            continue
                        for md_file in md_files:
                            m = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                            title = m.get("title", md_file.stem)
                            lines.append(f"- [{title}]({subdir}/{md_file.name})")
                        lines.append("")
                    index_path = wiki_dir / "index.md"
                    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                except Exception as e:
                    logger.error("Failed to rebuild index after backfill: %s", e)

        # Summary
        red_count = sum(1 for i in issues if i["severity"] == "red")
        yellow_count = sum(1 for i in issues if i["severity"] == "yellow")
        blue_count = sum(1 for i in issues if i["severity"] == "blue")

        result = {
            "total_pages": len(all_pages),
            "issues": {
                "red": red_count,
                "yellow": yellow_count,
                "blue": blue_count,
                "total": len(issues),
            },
            "details": issues,
            "fixes_applied": fixes_applied,
            "status": "healthy" if not issues else f"{len(issues)} issue(s) found",
        }
        if backfill_results:
            result["backfill"] = {
                "papers_processed": len(backfill_results),
                "details": backfill_results,
            }

        return json.dumps(result, ensure_ascii=False, indent=2)

    async def _arun(self, auto_fix: bool = False, backfill: bool = False, run_manager=None) -> str:
        return await asyncio.to_thread(self._run, auto_fix, backfill, None)


# ---------------------------------------------------------------------------
# Query Wiki Tool
# ---------------------------------------------------------------------------

class QueryWikiInput(BaseModel):
    query: str = Field(..., description="Natural language question about the wiki content.")
    max_pages: int = Field(default=10, description="Maximum number of pages to read.")
    mode: str = Field(
        default="hybrid",
        description="Search mode: 'hybrid' (BM25 + embedding, default), 'bm25' (keyword only), 'semantic' (embedding only).",
    )


class QueryWikiTool(BaseTool):
    """Search the wiki using hybrid retrieval (BM25 + embedding semantic search) and return relevant page summaries."""

    name: str = "query_wiki"
    description: str = (
        "Search the wiki for pages relevant to a query using hybrid retrieval "
        "(BM25 keyword + embedding semantic search with RRF fusion). "
        "Returns matching page summaries with titles, types, scores, and key content snippets. "
        "The agent should synthesize an answer from these results with proper citations."
    )
    args_schema: Type[BaseModel] = QueryWikiInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _run(self, query: str, max_pages: int = 10, mode: str = "hybrid", run_manager=None) -> str:
        from service.wiki_retriever import get_wiki_retriever

        wiki_dir = self._root_dir / "wiki"
        retriever = get_wiki_retriever(wiki_dir)
        results = retriever.search(query, top_k=max_pages, mode=mode)

        return json.dumps({
            "query": query,
            "mode": mode,
            "results_count": len(results),
            "results": results,
        }, ensure_ascii=False, indent=2)

    async def _arun(self, query: str, max_pages: int = 10, mode: str = "hybrid", run_manager=None) -> str:
        return await asyncio.to_thread(self._run, query, max_pages, mode, None)
