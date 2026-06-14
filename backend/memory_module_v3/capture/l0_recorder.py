"""Auto-capture: record each LLM turn to L0 raw conversation table."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..storage.l0_repo import L0Message, L0Repo

logger = logging.getLogger(__name__)


class L0Recorder:
    """Writes conversation messages to l0_messages.

    Designed to be called after each LLM turn in AgentManager.astream().
    Metadata is written synchronously; embedding is computed asynchronously.
    """

    def __init__(self, l0_repo: L0Repo, embedding_fn=None):
        self._repo = l0_repo
        self._embedding_fn = embedding_fn  # async callable: (text) -> list[float]
        self._pending_ids: list[int] = []  # msg_ids awaiting embedding
        self._bg_task: asyncio.Task | None = None

    async def capture(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
        *,
        user_ts: datetime | None = None,
        assistant_ts: datetime | None = None,
    ) -> tuple[int, int]:
        """Capture a user+assistant turn. Returns (user_msg_id, assistant_msg_id).

        Writes content synchronously, schedules embedding computation in background.
        """
        now = datetime.now(timezone.utc)

        user_msg = L0Message(
            session_id=session_id,
            role="user",
            content=user_message,
            ts=user_ts or now,
        )
        asst_msg = L0Message(
            session_id=session_id,
            role="assistant",
            content=assistant_response,
            ts=assistant_ts or now,
        )

        # Synchronous write: metadata only (embedding=None)
        msg_ids = self._repo.insert_batch([user_msg, asst_msg])
        user_id, asst_id = msg_ids

        logger.debug("L0 captured: session=%s user_msg=%d asst_msg=%d", session_id, user_id, asst_id)

        # Schedule async embedding
        if self._embedding_fn:
            self._pending_ids.extend(msg_ids)
            self._schedule_embedding()

        return user_id, asst_id

    def _schedule_embedding(self) -> None:
        """Schedule background embedding computation if not already running."""
        if self._bg_task and not self._bg_task.done():
            return  # already running
        self._bg_task = asyncio.create_task(self._compute_embeddings())

    async def _compute_embeddings(self) -> None:
        """Compute embeddings for pending messages in background."""
        ids = list(self._pending_ids)
        self._pending_ids.clear()

        if not ids:
            return

        try:
            messages = self._repo.get_by_ids(ids)
            for msg in messages:
                try:
                    embedding = await self._embedding_fn(msg["content"])
                    self._repo.update_embedding(msg["msg_id"], embedding)
                except Exception as exc:
                    logger.warning("Failed to compute embedding for msg %d: %s", msg["msg_id"], exc)
        except Exception as exc:
            logger.error("L0 embedding background task failed: %s", exc)

    async def flush(self) -> None:
        """Wait for all pending embedding tasks to complete."""
        if self._bg_task and not self._bg_task.done():
            try:
                await asyncio.wait_for(self._bg_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("L0 embedding flush timed out after 5s")
                self._bg_task.cancel()
