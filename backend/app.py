from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Fix Windows ProactorEventLoop incompatibility with asyncpg/psycopg
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from api.chat import router as chat_router
from api.compress import router as compress_router
from api.config_api import router as config_router
from api.digest import router as digest_router
from api.files import router as files_router
from api.ingest import router as ingest_router
from api.sessions import router as sessions_router
from api.tokens import router as tokens_router
from config import get_settings
from graph.agent import agent_manager
from graph.checkpointer import init_checkpointer_async
from service.scheduler import start_scheduler, shutdown_scheduler
from tools.skills_scanner import refresh_snapshot


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    await init_checkpointer_async()
    refresh_snapshot(settings.backend_dir)
    agent_manager.initialize(settings.backend_dir)
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="Mini-OpenClaw API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(files_router, prefix="/api", tags=["files"])
app.include_router(ingest_router, prefix="/api", tags=["ingest"])
app.include_router(tokens_router, prefix="/api", tags=["tokens"])
app.include_router(compress_router, prefix="/api", tags=["compress"])
app.include_router(config_router, prefix="/api", tags=["config"])
app.include_router(digest_router, prefix="/api", tags=["digest"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global fallback: any uncaught exception returns structured JSON, not a bare 500.
    `/api/chat` errors are handled inside its SSE event_generator, so they don't reach here."""
    logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "internal", "detail": str(exc)},
    )
