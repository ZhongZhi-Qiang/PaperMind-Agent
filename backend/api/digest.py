"""API endpoints for the daily arXiv digest — manual trigger, test, and status."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

router = APIRouter()


@router.get("/digest/status")
async def digest_status():
    """Return scheduler status and next run time."""
    from service.scheduler import get_scheduler_status
    return get_scheduler_status()


@router.get("/digest/test")
async def digest_test():
    """Test arXiv fetching without processing — returns paper list for inspection."""
    from service.arxiv_service import fetch_arxiv_papers, load_registered_arxiv_ids, filter_new_papers
    from config import get_settings

    target_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    papers = fetch_arxiv_papers(target_date=target_date)

    settings = get_settings()
    registered = load_registered_arxiv_ids(settings.backend_dir)
    new_papers = filter_new_papers(papers, registered)

    return {
        "target_date": target_date,
        "total_fetched": len(papers),
        "already_registered": len(papers) - len(new_papers),
        "new_papers": [p.to_dict() for p in new_papers],
    }


@router.post("/digest/run")
async def digest_run():
    """Manually trigger the full digest pipeline (fetch + process + notify)."""
    from service.scheduler import run_daily_digest

    asyncio.create_task(run_daily_digest())
    return {"status": "triggered", "message": "Daily digest job started in background"}
