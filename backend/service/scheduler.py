"""APScheduler integration — runs daily arXiv paper digest at a configured time."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _get_digest_hour() -> int:
    return int(os.getenv("ARXIV_DIGEST_HOUR", "8"))


def _get_digest_enabled() -> bool:
    val = os.getenv("ARXIV_DIGEST_ENABLED", "true").strip().lower()
    return val in {"1", "true", "yes", "on"}


async def run_daily_digest():
    """The daily digest job — fetches arXiv papers, creates wiki pages, pushes to WeChat."""
    from config import get_settings
    from service.arxiv_service import fetch_arxiv_papers, load_registered_arxiv_ids, filter_new_papers
    from service.digest_pipeline import run_digest
    from service.wechat_notifier import (
        send_daily_digest,
        send_markdown,
        send_error_notification,
    )

    target_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info("Starting daily digest for date: %s", target_date)

    try:
        settings = get_settings()
        base_dir = settings.backend_dir

        # 1. Fetch papers from arXiv
        papers = fetch_arxiv_papers(target_date=target_date)
        if not papers:
            logger.info("No papers found for %s", target_date)
            send_markdown(f"## Agent+Memory 日报 ({target_date})\n> 今日未发现新论文")
            return

        # 2. Filter out already registered papers
        registered = load_registered_arxiv_ids(base_dir)
        new_papers = filter_new_papers(papers, registered)
        if not new_papers:
            logger.info("All %d papers already registered", len(papers))
            send_markdown(f"## Agent+Memory 日报 ({target_date})\n> 发现 {len(papers)} 篇论文，但均已在 Wiki 中")
            return

        # 3. Process papers: download, parse, analyze, create wiki pages, generate Chinese summaries
        created_slugs, paper_dicts = run_digest(new_papers, base_dir)

        # 4. Push to WeChat (auto-splits into multiple messages if needed)
        send_daily_digest(
            papers=paper_dicts,
            date_str=target_date,
            wiki_created=created_slugs,
        )

        logger.info(
            "Daily digest completed: %d new papers, %d wiki pages created",
            len(new_papers),
            len(created_slugs),
        )

    except Exception as e:
        logger.exception("Daily digest failed")
        try:
            send_error_notification(str(e), target_date)
        except Exception:
            logger.error("Failed to send error notification")

    # 5. Post-digest lint: fire-and-forget (runs async, won't block digest completion)
    asyncio.create_task(_run_post_digest_lint_async())


async def _run_post_digest_lint_async():
    """Run wiki lint with auto-fix and backfill after digest completes.

    Designed to be called via asyncio.create_task (fire-and-forget) so it
    doesn't block the digest job from completing while lint runs.
    Errors are logged but not re-raised.
    """
    from config import get_settings
    from tools.wiki_engine_tool import LintWikiTool
    import json as _json

    try:
        settings = get_settings()
        base_dir = settings.backend_dir

        logger.info("Post-digest lint started (background)")
        lint_tool = LintWikiTool(root_dir=base_dir)

        result_str = await asyncio.to_thread(lint_tool._run, auto_fix=True, backfill=True, run_manager=None)
        result = _json.loads(result_str)

        issues = result.get("issues", {})
        fixes = result.get("fixes_applied", [])
        backfill = result.get("backfill", {})

        summary_parts = []
        if issues.get("total", 0) > 0:
            summary_parts.append(f"{issues['total']} issues (red={issues.get('red', 0)}, yellow={issues.get('yellow', 0)}, blue={issues.get('blue', 0)})")
        if fixes:
            summary_parts.append(f"{len(fixes)} fixes applied")
        if backfill.get("papers_processed", 0) > 0:
            summary_parts.append(f"{backfill['papers_processed']} papers backfilled")

        if summary_parts:
            logger.info("Post-digest lint completed: %s", "; ".join(summary_parts))
        else:
            logger.info("Post-digest lint completed: wiki healthy, no action needed")
    except Exception as e:
        logger.error("Post-digest lint failed: %s", e)


def start_scheduler():
    """Start the APScheduler with the daily digest job."""
    global _scheduler

    if not _get_digest_enabled():
        logger.info("arXiv digest is disabled (ARXIV_DIGEST_ENABLED=false)")
        return

    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    hour = _get_digest_hour()
    _scheduler.add_job(
        run_daily_digest,
        trigger=CronTrigger(hour=hour, minute=0),
        id="daily_arxiv_digest",
        name="Daily arXiv Paper Digest",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    logger.info("Scheduler started — daily digest at %02d:00 (Asia/Shanghai)", hour)


def shutdown_scheduler():
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
        _scheduler = None


def get_scheduler_status() -> dict:
    """Return current scheduler status for API inspection."""
    if _scheduler is None:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    return {
        "running": _scheduler.running,
        "jobs": jobs,
        "digest_hour": _get_digest_hour(),
        "enabled": _get_digest_enabled(),
    }
