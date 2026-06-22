"""Repository for L0 raw conversation messages — file-based storage."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class L0Message:
    msg_id: int | None = None
    session_id: str = ""
    role: str = ""
    content: str = ""
    ts: datetime | None = None


class L0FileRepo:
    """File-based CRUD for L0 raw messages.

    Each session is stored as {l0_dir}/{session_id}.json.
    msg_id is the message index within the session file.
    """

    def __init__(self, l0_dir: str | Path):
        self._dir = Path(l0_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _read_session(self, session_id: str) -> list[dict[str, Any]]:
        path = self._session_path(session_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("messages", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _write_session(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        path = self._session_path(session_id)
        data = {"session_id": session_id, "messages": messages}
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def insert(self, msg: L0Message) -> int:
        """Insert a message, return msg_id (index in the file)."""
        messages = self._read_session(msg.session_id)
        msg_id = len(messages)
        messages.append({
            "msg_id": msg_id,
            "role": msg.role,
            "content": msg.content,
            "ts": (msg.ts or datetime.now(timezone.utc)).isoformat(),
        })
        self._write_session(msg.session_id, messages)
        return msg_id

    def insert_batch(self, messages: list[L0Message]) -> list[int]:
        """Insert multiple messages, return list of msg_ids."""
        if not messages:
            return []
        session_id = messages[0].session_id
        existing = self._read_session(session_id)
        start_id = len(existing)
        ids: list[int] = []
        for i, msg in enumerate(messages):
            msg_id = start_id + i
            existing.append({
                "msg_id": msg_id,
                "role": msg.role,
                "content": msg.content,
                "ts": (msg.ts or datetime.now(timezone.utc)).isoformat(),
            })
            ids.append(msg_id)
        self._write_session(session_id, existing)
        return ids

    def get_by_ids(self, msg_ids: list[int], session_id: str = "") -> list[dict[str, Any]]:
        """Fetch messages by their IDs from a specific session."""
        if not msg_ids:
            return []
        if not session_id:
            return []
        messages = self._read_session(session_id)
        if not messages:
            return []
        return [messages[mid] for mid in msg_ids if 0 <= mid < len(messages)]

    def get_by_session(
        self, session_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return messages for a session, most recent first."""
        messages = self._read_session(session_id)
        # Reverse for most-recent-first, then apply offset/limit
        messages = list(reversed(messages))
        return messages[offset: offset + limit]
