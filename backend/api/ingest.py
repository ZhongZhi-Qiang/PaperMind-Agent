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

        try:
            async for event in process_pdf_upload(
                file_path=file_path,
                pdf_url=url,
                user_title=title,
                base_dir=base_dir,
            ):
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
