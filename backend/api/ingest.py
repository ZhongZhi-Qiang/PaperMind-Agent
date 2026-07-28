"""API endpoint for PDF ingest — user uploads a PDF to add to the wiki."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import StreamingResponse

from api._sse_utils import sse_event
from config import get_settings

router = APIRouter()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/ingest/pdf")
async def ingest_pdf(
    file: UploadFile | None = None,
    url: str | None = Form(None),
    title: str | None = Form(None),
):
    """Upload a PDF file (or provide a URL) and ingest it into the wiki knowledge base.

    Returns SSE stream with progress events: progress, done, error.
    """
    if file is None and not url:
        return StreamingResponse(
            _single_error("请提供 PDF 文件或 URL"),
            media_type="text/event-stream",
        )

    settings = get_settings()
    base_dir = settings.backend_dir

    # Save uploaded file to temp if provided
    tmp_file = None
    file_path = None

    if file is not None:
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            return StreamingResponse(
                _single_error("文件过大，最大支持 50MB"),
                media_type="text/event-stream",
            )

        suffix = Path(file.filename or "upload.pdf").suffix or ".pdf"
        tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_file.write(content)
        tmp_file.close()
        file_path = tmp_file.name

    async def event_stream():
        from service.ingest_service import process_pdf_upload

        ingest_ok = False
        try:
            async for event in process_pdf_upload(
                file_path=file_path,
                pdf_url=url,
                user_title=title,
                base_dir=base_dir,
            ):
                if event.get("event") == "done":
                    ingest_ok = True
                yield sse_event(event.pop("event"), event)
        except Exception as e:
            yield sse_event("error", {"error": str(e)})
        finally:
            # Clean up temp file
            if tmp_file is not None:
                try:
                    Path(tmp_file.name).unlink(missing_ok=True)
                except Exception:
                    pass

        # Fire-and-forget: run wiki lint + auto-fix asynchronously after
        # the SSE stream ends, so the user sees the paper summary first.
        if ingest_ok:
            asyncio.create_task(_background_lint(base_dir))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _single_error(msg: str):
    yield sse_event("error", {"error": msg})


async def _background_lint(base_dir: Path) -> None:
    """Run wiki lint with auto-fix in the background after a successful ingest.

    Runs after the SSE stream has already closed, so lint results don't
    block or distract from the paper summary shown to the user.
    """
    import json as _json
    import logging
    _logger = logging.getLogger(__name__)

    try:
        from tools.wiki_engine_tool import LintWikiTool

        _logger.info("Background lint started after ingest")
        lint_tool = LintWikiTool(root_dir=base_dir)
        result_str = await asyncio.to_thread(
            lint_tool._run, auto_fix=True, backfill=True, run_manager=None,
        )
        result = _json.loads(result_str)

        issues = result.get("issues", {})
        fixes = result.get("fixes_applied", [])
        backfill = result.get("backfill", {})

        parts = []
        if issues.get("total", 0) > 0:
            parts.append(
                f"{issues['total']} issues "
                f"(red={issues.get('red', 0)}, "
                f"yellow={issues.get('yellow', 0)}, "
                f"blue={issues.get('blue', 0)})"
            )
        if fixes:
            parts.append(f"{len(fixes)} fixes applied")
        if backfill.get("papers_processed", 0) > 0:
            parts.append(f"{backfill['papers_processed']} papers backfilled")

        if parts:
            _logger.info("Background lint completed: %s", "; ".join(parts))
        else:
            _logger.info("Background lint completed: wiki healthy")
    except Exception as e:
        _logger.error("Background lint failed: %s", e)
