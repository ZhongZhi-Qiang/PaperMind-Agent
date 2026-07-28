"""PDF paper parser — extracts text and metadata from academic PDFs.

Uses PyMuPDF (fitz) for text extraction with academic paper heuristics:
- Detects title from first-page font sizes
- Extracts abstract section
- Cleans LaTeX artifacts and excessive whitespace
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class PDFParserInput(BaseModel):
    file_path: str = Field(
        default="",
        description=(
            "Path to a local PDF file. Supports absolute paths and paths relative "
            "to the project root (e.g. 'papers/attention.pdf'). "
            "Use this for local files. Mutually exclusive with url — one must be provided."
        ),
    )
    url: str = Field(
        default="",
        description=(
            "URL to a PDF file (e.g. arXiv link or direct PDF link). "
            "When provided and MinerU is configured, the remote MinerU service "
            "is used for higher-quality parsing (formulas, tables, layout). "
            "Falls back to downloading + local parsing if MinerU fails. "
            "Mutually exclusive with file_path — one must be provided."
        ),
    )
    max_pages: int = Field(
        default=30,
        description="Maximum number of pages to extract (default 30). Only used for local parsing.",
    )


def _clean_latex_artifacts(text: str) -> str:
    """Remove common LaTeX commands and normalize whitespace."""
    # unwrap \textbf{...}, \textit{...}, \emph{...}
    text = re.sub(r"\\(?:textbf|textit|emph|texttt|underline)\{([^}]*)\}", r"\1", text)
    # remove \begin{...}, \end{...}
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", text)
    # remove common commands: \label{...}, \ref{...}, \cite{...}
    text = re.sub(r"\\(?:label|ref|cite|eqref|autoref|pageref)\{[^}]*\}", "", text)
    # remove \vspace, \hspace, \noindent, etc.
    text = re.sub(r"\\(?:vspace|hspace|noindent|indent|par|bigskip|medskip|smallskip)[^\n]*", "", text)
    # remove remaining backslash-commands without braces (e.g. \item, \section)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", text)
    # collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_title(page_text: str) -> str:
    """Heuristic: first non-empty line that looks like a title (short, capitalized)."""
    for line in page_text.split("\n")[:15]:
        line = line.strip()
        if not line or len(line) < 3:
            continue
        # skip lines that look like metadata
        if any(
            keyword in line.lower()
            for keyword in ["arxiv", "preprint", "conference", "journal", "proceedings", "abstract"]
        ):
            continue
        # title is usually short-ish
        if len(line) < 200:
            return line
    return ""


def _extract_abstract(text: str) -> str:
    """Extract the abstract section from paper text."""
    # match "Abstract" header followed by text until next section header
    pattern = re.compile(
        r"(?:^|\n)\s*Abstract\s*\n(.*?)(?=\n\s*(?:\d+[\.\s]|Introduction|Keywords|1\s+Introduction|\n\n))",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        abstract = match.group(1).strip()
        # clean up: remove newlines within abstract
        abstract = re.sub(r"\s*\n\s*", " ", abstract)
        return abstract[:3000]
    return ""


def _extract_authors(first_page: str, title: str) -> str:
    """Heuristic: lines between title and abstract/keywords."""
    lines = first_page.split("\n")
    try:
        title_idx = next(
            i for i, line in enumerate(lines[:20]) if title and title[:40] in line
        )
    except StopIteration:
        return ""

    # collect lines after title until we hit abstract/keywords/introduction
    author_lines: list[str] = []
    for line in lines[title_idx + 1 : title_idx + 15]:
        line = line.strip()
        if not line:
            if author_lines:
                break
            continue
        lower = line.lower()
        if any(kw in lower for kw in ["abstract", "keywords", "introduction", "1 "]):
            break
        # skip lines that are clearly not author lines
        if len(line) > 300:
            continue
        author_lines.append(line)
    return " ".join(author_lines)[:500]


def _parse_pdf(file_path: str, max_pages: int = 30) -> dict:
    """Parse a PDF and return structured metadata + text."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "error": (
                "PyMuPDF is not installed. Install it with: pip install pymupdf"
            ),
        }

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.suffix.lower() == ".pdf":
        return {"error": f"Not a PDF file: {file_path}"}

    doc = fitz.open(str(path))
    pages_text: list[str] = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pages_text.append(page.get_text())

    full_text = "\n\n".join(pages_text)
    full_text = _clean_latex_artifacts(full_text)

    first_page = pages_text[0] if pages_text else ""
    title = _detect_title(first_page)
    abstract = _extract_abstract(full_text)
    authors = _extract_authors(first_page, title)

    doc.close()

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "full_text": full_text,
        "page_count": len(pages_text),
        "file_path": str(path.resolve()),
    }


class PDFParserTool(BaseTool):
    """Parse academic PDFs — local via PyMuPDF, remote URLs via MinerU if configured."""

    name: str = "pdf_parser"
    description: str = (
        "Parse an academic PDF paper and extract structured content: title, authors, "
        "abstract, and full text. Returns JSON with the extracted fields. "
        "Accepts either a local file_path OR a url (arXiv link, PDF link). "
        "When a URL is provided, uses the MinerU remote service for higher-quality "
        "parsing if configured, falling back to download + local parsing."
    )
    args_schema: Type[BaseModel] = PDFParserInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _resolve_path(self, file_path: str) -> str:
        """Resolve path — supports absolute and root-relative paths."""
        p = Path(file_path)
        if p.is_absolute():
            return str(p)
        return str(self._root_dir / file_path)

    def _run(
        self,
        file_path: str = "",
        url: str = "",
        max_pages: int = 30,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        import json

        if not file_path and not url:
            return "Error: either file_path or url must be provided."

        # ── Try MinerU (URL or file upload) if configured ──
        try:
            from config import get_settings
            settings = get_settings()
            if settings.mineru_token:
                if url:
                    from tools.mineru_client import parse_via_mineru
                    result = parse_via_mineru(
                        url,
                        base_url=settings.mineru_base_url,
                        token=settings.mineru_token,
                        model_version=settings.mineru_default_model,
                        poll_interval=settings.mineru_poll_interval,
                        max_wait=settings.mineru_max_wait,
                    )
                elif file_path:
                    from tools.mineru_client import parse_via_mineru_file
                    result = parse_via_mineru_file(
                        self._resolve_path(file_path),
                        base_url=settings.mineru_base_url,
                        token=settings.mineru_token,
                        model_version=settings.mineru_default_model,
                        poll_interval=settings.mineru_poll_interval,
                        max_wait=settings.mineru_max_wait,
                    )
                else:
                    result = {"error": "no input"}

                if "error" not in result and result.get("full_text"):
                    text = result.get("full_text", "")
                    if len(text) > 50000:
                        result["full_text"] = text[:50000] + "\n\n...[truncated at 50000 chars]"
                    result["text_length"] = len(text)
                    return json.dumps(result, ensure_ascii=False, indent=2)
                # MinerU failed → fall through to local parse or download
        except Exception:
            pass

        # ── URL without MinerU: download + local parse ──
        if url and not file_path:
            import tempfile
            import urllib.request
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "PaperMind-Agent/1.0"},
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                tmp.write(data)
                tmp.close()
                file_path = tmp.name
            except Exception as e:
                return f"Error downloading PDF from URL: {e}"

        # ── Local PyMuPDF fallback ──
        resolved = self._resolve_path(file_path)
        result = _parse_pdf(resolved, max_pages)

        if "error" in result:
            return result["error"]

        # truncate full_text to avoid flooding the context window
        text = result.get("full_text", "")
        if len(text) > 50000:
            result["full_text"] = text[:50000] + "\n\n...[truncated at 50000 chars]"
        result["text_length"] = len(text)

        return json.dumps(result, ensure_ascii=False, indent=2)

    async def _arun(
        self,
        file_path: str = "",
        url: str = "",
        max_pages: int = 30,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        return await asyncio.to_thread(self._run, file_path, url, max_pages, None)
