"""Auto-capture: record each LLM turn to L0 raw conversation store."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..storage.l0_file_repo import L0FileRepo, L0Message

logger = logging.getLogger(__name__)


class L0Recorder:
    """Writes conversation messages to L0 file storage.

    Designed to be called after each LLM turn in AgentManager.astream().
    """

    def __init__(self, l0_repo: L0FileRepo):
        self._repo = l0_repo

    async def capture(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
        *,
        user_ts: datetime | None = None,
        assistant_ts: datetime | None = None,
    ) -> tuple[int, int]:
        """Capture a user+assistant turn. Returns (user_msg_id, assistant_msg_id)."""
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

        msg_ids = self._repo.insert_batch([user_msg, asst_msg])
        user_id, asst_id = msg_ids

        logger.debug("L0 captured: session=%s user_msg=%d asst_msg=%d", session_id, user_id, asst_id)

        return user_id, asst_id
