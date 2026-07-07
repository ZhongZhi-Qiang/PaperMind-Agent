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
