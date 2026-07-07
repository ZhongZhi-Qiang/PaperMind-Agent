"""WeChat Work (企业微信) Webhook notifier — pushes paper digests to a group chat."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

WECHAT_WEBHOOK_BASE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
MAX_MSG_BYTES = 4096  # WeChat markdown limit


def _get_webhook_key() -> str:
    key = os.getenv("WECHAT_WEBHOOK_KEY", "")
    if not key:
        raise RuntimeError("WECHAT_WEBHOOK_KEY is not set in environment")
    return key


def send_markdown(content: str, webhook_key: str | None = None) -> bool:
    """Send a markdown message via WeChat Work webhook. Returns True on success."""
    key = webhook_key or _get_webhook_key()
    url = f"{WECHAT_WEBHOOK_BASE}?key={key}"

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("WeChat webhook sent successfully")
                return True
            else:
                logger.error("WeChat webhook error: %s", result)
                return False
    except Exception as e:
        logger.error("WeChat webhook request failed: %s", e)
        return False


def _build_single_paper(p: dict, index: int) -> str:
    """Build formatted markdown for one paper with clear prefixes."""
    title = p.get("title", "Untitled")
    authors = p.get("authors", [])
    author_str = ", ".join(authors[:3])
    if len(authors) > 3:
        author_str += " et al."

    categories = p.get("categories", [])
    cat_str = ", ".join(categories[:3])

    summary = p.get("abstract", "")

    arxiv_id = p.get("arxiv_id", "")
    pdf_url = p.get("pdf_url", "")

    lines = [
        f"**{index}. {title}**",
        f"**作者**: {author_str} | **分类**: {cat_str}",
        f"**摘要**: {summary}",
        f"[arXiv](https://arxiv.org/abs/{arxiv_id}) | [PDF]({pdf_url})",
    ]
    return "\n".join(lines)


def build_messages(
    papers: list[dict],
    date_str: str,
    wiki_created: list[str] | None = None,
) -> list[str]:
    """Build one message per paper, plus a header and optional footer."""
    if not papers:
        return [f"## Agent+Memory 日报 ({date_str})\n> 今日未发现匹配论文"]

    messages: list[str] = []

    # Header
    header = f"## Agent+Memory 日报 ({date_str})\n> 共发现 **{len(papers)}** 篇新论文"
    if wiki_created:
        header += f"，已收录 **{len(wiki_created)}** 篇到 Wiki"
    messages.append(header)

    # One message per paper
    for i, p in enumerate(papers, 1):
        messages.append(_build_single_paper(p, i))

    return messages


def send_daily_digest(
    papers: list[dict],
    date_str: str,
    wiki_created: list[str] | None = None,
    webhook_key: str | None = None,
) -> bool:
    """Send the daily digest. Auto-splits into multiple messages if needed."""
    messages = build_messages(papers, date_str, wiki_created)

    all_ok = True
    for msg in messages:
        if not send_markdown(msg, webhook_key=webhook_key):
            all_ok = False

    logger.info("Sent %d message(s) to WeChat", len(messages))
    return all_ok


def send_error_notification(error_msg: str, date_str: str) -> bool:
    """Push an error notification when the daily digest fails."""
    content = (
        f"## Agent+Memory 日报 - 执行失败\n"
        f"> 日期: {date_str}\n\n"
        f"**错误**: {error_msg[:300]}"
    )
    return send_markdown(content)
