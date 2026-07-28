"""MinerU API client — remote document parsing via OpenAI Lab MinerU service.

POST to create an extraction task, then poll GET until done/failed.
Returns results in a dict format compatible with _parse_pdf() consumers.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0


def _create_task(
    base_url: str,
    token: str,
    file_url: str,
    *,
    model_version: str = "vlm",
    is_ocr: bool = False,
    enable_formula: bool = True,
    enable_table: bool = True,
    language: str = "ch",
    no_cache: bool = False,
    cache_tolerance: int = 900,
) -> dict[str, Any]:
    """Create a MinerU extraction task. Returns {"task_id": "..."} or {"error": "..."}."""
    try:
        resp = httpx.post(
            f"{base_url}/extract/task",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={
                "url": file_url,
                "model_version": model_version,
                "is_ocr": is_ocr,
                "enable_formula": enable_formula,
                "enable_table": enable_table,
                "language": language,
                "no_cache": no_cache,
                "cache_tolerance": cache_tolerance,
            },
            timeout=_HTTP_TIMEOUT,
        )
        data = resp.json()
        if data.get("code") != 0:
            return {"error": f"MinerU task creation failed: {data.get('msg', 'unknown error')}"}
        task_id = data.get("data", {}).get("task_id")
        if not task_id:
            return {"error": "MinerU task creation returned no task_id"}
        return {"task_id": task_id}
    except httpx.HTTPError as e:
        logger.warning("MinerU create_task HTTP error: %s", e)
        return {"error": f"MinerU HTTP error: {e}"}
    except Exception as e:
        logger.warning("MinerU create_task unexpected error: %s", e)
        return {"error": f"MinerU error: {e}"}


def _poll_task(base_url: str, token: str, task_id: str) -> dict[str, Any]:
    """Query MinerU task status. Returns the full response data dict."""
    try:
        resp = httpx.get(
            f"{base_url}/extract/task/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT,
        )
        return resp.json()
    except httpx.HTTPError as e:
        logger.warning("MinerU poll_task HTTP error: %s", e)
        return {"code": -1, "msg": str(e)}
    except Exception as e:
        logger.warning("MinerU poll_task unexpected error: %s", e)
        return {"code": -1, "msg": str(e)}


def _wait_for_task(
    base_url: str,
    token: str,
    task_id: str,
    *,
    poll_interval: int = 3,
    max_wait: int = 300,
) -> dict[str, Any]:
    """Poll until task reaches terminal state. Returns final data dict."""
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > max_wait:
            return {"error": f"MinerU task timed out after {max_wait}s (task_id={task_id})"}

        result = _poll_task(base_url, token, task_id)
        if result.get("code") != 0:
            return {"error": f"MinerU poll failed: {result.get('msg', 'unknown')}"}

        data = result.get("data", {})
        state = data.get("state", "")

        if state == "done":
            return data

        if state in ("failed", "error"):
            return {"error": f"MinerU task failed: state={state}, data={data}"}

        time.sleep(poll_interval)


def _download_and_unzip(zip_url: str) -> str:
    """Download the result ZIP from MinerU and extract markdown content.

    Returns the combined text content, or empty string on failure.
    """
    import io
    import zipfile

    try:
        resp = httpx.get(zip_url, timeout=60.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("MinerU ZIP download failed: %s", e)
        return ""

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            md_files = sorted([n for n in zf.namelist() if n.endswith(".md")])
            parts: list[str] = []
            for name in md_files:
                parts.append(zf.read(name).decode("utf-8", errors="replace"))
            return "\n\n".join(parts)
    except Exception as e:
        logger.warning("MinerU ZIP extraction failed: %s", e)
        return ""


def _extract_title_from_md(text: str) -> str:
    """Extract title from markdown — first H1 heading or first substantial line."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("# ") and len(line) > 3:
            return line[2:].strip()
    # fallback: first non-empty line that isn't a header pattern
    for line in text.split("\n")[:10]:
        line = line.strip()
        if line and len(line) > 5 and not line.startswith(">"):
            # skip lines that look like metadata
            if any(kw in line.lower() for kw in ["arxiv", "preprint", "http", "doi:", "volume"]):
                continue
            return line[:200]
    return ""


def _extract_authors_from_md(text: str) -> str:
    """Extract authors from markdown — collect lines between title and first ## section."""
    import re as _re
    lines = text.split("\n")
    found_title = False
    author_parts: list[str] = []

    for line in lines[:40]:
        stripped = line.strip()
        if stripped.startswith("# ") and not found_title:
            found_title = True
            continue
        if not found_title or not stripped:
            continue
        # Stop at section headers
        if stripped.startswith("## "):
            break
        lower = stripped.lower()
        # Skip lines that are license notices or affiliation-only
        if any(kw in lower for kw in ["provided proper attribution", "google hereby grants"]):
            continue

        # Clean HTML/superscript tags, then extract name part (before email/URL)
        cleaned = _re.sub(r"<[^>]+>", "", stripped)
        # Remove parenthesized affiliations: (Google Brain), (Equal Contribution), etc.
        cleaned = _re.sub(r"\([^)]*(?:university|institute|college|lab|google|deepmind|meta|microsoft|openai|equal|joint|correspond)[^)]*\)", "", cleaned, flags=_re.IGNORECASE)
        # Remove emails
        cleaned = _re.sub(r"\S+@\S+", "", cleaned)
        # Remove URLs
        cleaned = _re.sub(r"https?://\S+", "", cleaned)
        # Remove footnote markers like ∗, †, ‡, §, ¶, 1, 2 etc. at start/end
        cleaned = _re.sub(r"^[\*\†\‡\§\¶\d\s]+", "", cleaned)
        cleaned = _re.sub(r"[\*\†\‡\§\¶\d]+$", "", cleaned)
        cleaned = cleaned.strip().rstrip(",").strip()

        if cleaned and len(cleaned) > 1:
            author_parts.append(cleaned)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for a in author_parts:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    result = ", ".join(unique)
    return result[:500] if result else ""


def _normalize_content(raw: dict[str, Any], file_url: str = "") -> dict[str, Any]:
    """Convert MinerU response data to _parse_pdf-compatible dict."""
    full_text = ""

    # Primary: content returned inline
    content = raw.get("content", {})
    if isinstance(content, dict):
        full_text = content.get("markdown") or content.get("text") or content.get("body") or ""
    elif isinstance(content, str):
        full_text = content

    # Fallback: download and extract ZIP
    zip_url = raw.get("full_zip_url", "")
    if not full_text and zip_url:
        logger.info("MinerU downloading result ZIP: %s", zip_url)
        full_text = _download_and_unzip(zip_url)

    # Extract metadata
    meta = raw.get("meta", raw.get("metadata", {}))
    if not isinstance(meta, dict):
        meta = {}

    title = meta.get("title", "")
    authors = meta.get("authors", "")
    if isinstance(authors, list):
        authors = ", ".join(authors)

    # Fallback: extract title/authors from full_text if MinerU didn't return them
    if not title and full_text:
        title = _extract_title_from_md(full_text)
    if not authors and full_text:
        authors = _extract_authors_from_md(full_text)

    page_count = raw.get("progress", {}).get("total_pages", 0)

    return {
        "title": title or "",
        "authors": authors or "",
        "abstract": meta.get("abstract", ""),
        "full_text": full_text,
        "page_count": page_count,
        "file_path": file_url or raw.get("file_url", ""),
        "source": "mineru",
    }


def parse_via_mineru(
    file_url: str,
    *,
    base_url: str,
    token: str,
    model_version: str = "vlm",
    enable_formula: bool = True,
    enable_table: bool = True,
    language: str = "ch",
    poll_interval: int = 3,
    max_wait: int = 300,
) -> dict[str, Any]:
    """Parse a document URL via MinerU API.

    Returns dict with keys matching _parse_pdf output, or {"error": "..."} on failure.
    """
    created = _create_task(
        base_url,
        token,
        file_url,
        model_version=model_version,
        enable_formula=enable_formula,
        enable_table=enable_table,
        language=language,
    )
    if "error" in created:
        return created

    task_id = created["task_id"]
    logger.info("MinerU task created: %s for %s", task_id, file_url)

    data = _wait_for_task(
        base_url,
        token,
        task_id,
        poll_interval=poll_interval,
        max_wait=max_wait,
    )
    if "error" in data:
        return data

    return _normalize_content(data, file_url)


def parse_via_mineru_file(
    file_path: str,
    *,
    base_url: str,
    token: str,
    model_version: str = "vlm",
    enable_formula: bool = True,
    enable_table: bool = True,
    language: str = "ch",
    poll_interval: int = 3,
    max_wait: int = 300,
) -> dict[str, Any]:
    """Upload a local file to MinerU and parse it.

    Uses the file-upload API flow:
    1. Request a signed upload URL via /file-urls/batch
    2. PUT the file to that URL
    3. Poll /extract-results/batch/{batch_id} until done

    Returns dict with keys matching _parse_pdf output, or {"error": "..."} on failure.
    """
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a file: {file_path}"}

    file_size = path.stat().st_size
    if file_size > 200 * 1024 * 1024:
        return {"error": f"File too large for MinerU: {file_size} bytes (max 200MB)"}

    # ── Step 1: Request signed upload URL ──
    try:
        resp = httpx.post(
            f"{base_url}/file-urls/batch",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={
                "files": [
                    {
                        "name": path.name,
                        "data_id": path.stem[:128],
                    }
                ],
                "model_version": model_version,
                "enable_table": enable_table,
                "enable_formula": enable_formula,
                "language": language,
            },
            timeout=_HTTP_TIMEOUT,
        )
        create_result = resp.json()
        if create_result.get("code") != 0:
            return {"error": f"MinerU file-urls batch failed: {create_result.get('msg', 'unknown')}"}

        batch_id = create_result["data"]["batch_id"]
        upload_url = create_result["data"]["file_urls"][0]
        logger.info("MinerU file upload batch created: %s for %s", batch_id, path.name)
    except httpx.HTTPError as e:
        return {"error": f"MinerU file-urls HTTP error: {e}"}
    except Exception as e:
        return {"error": f"MinerU file-urls error: {e}"}

    # ── Step 2: Upload file to signed URL ──
    try:
        with path.open("rb") as f:
            upload_resp = httpx.put(
                upload_url,
                content=f.read(),
                timeout=300.0,
            )
            upload_resp.raise_for_status()
        logger.info("MinerU file uploaded: %s (%d bytes)", path.name, file_size)
    except httpx.HTTPError as e:
        return {"error": f"MinerU file upload HTTP error: {e}"}
    except Exception as e:
        return {"error": f"MinerU file upload error: {e}"}

    # ── Step 3: Poll for results ──
    result_url = f"{base_url}/extract-results/batch/{batch_id}"
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > max_wait:
            return {"error": f"MinerU file task timed out after {max_wait}s (batch_id={batch_id})"}

        try:
            result_resp = httpx.get(
                result_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_HTTP_TIMEOUT,
            )
            result = result_resp.json()
        except Exception as e:
            logger.warning("MinerU poll batch error: %s", e)
            time.sleep(poll_interval)
            continue

        if result.get("code") != 0:
            return {"error": f"MinerU batch poll failed: {result.get('msg', 'unknown')}"}

        items = result.get("data", {}).get("extract_result", [])
        if not items:
            time.sleep(poll_interval)
            continue

        item = items[0]
        state = item.get("state", "")

        if state == "done":
            logger.info("MinerU file task done: batch_id=%s", batch_id)
            return _normalize_content(item, str(path))
        if state == "failed":
            return {"error": f"MinerU file task failed: {item.get('err_msg', 'unknown')}"}

        time.sleep(poll_interval)
