"""Pipeline session state dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PipelineSessionState:
    """Per-session pipeline state, persisted in pipeline_state table."""
    session_id: str = ""
    conversation_count: int = 0
    warmup_threshold: int = 1
    buffered_message_ids: list[int] = field(default_factory=list)
    last_l1_at: datetime | None = None
    last_l2_at: datetime | None = None
    pending_l2: bool = False

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "conversation_count": self.conversation_count,
            "warmup_threshold": self.warmup_threshold,
            "buffered_message_ids": self.buffered_message_ids,
            "last_l1_at": self.last_l1_at,
            "last_l2_at": self.last_l2_at,
            "pending_l2": self.pending_l2,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PipelineSessionState:
        return cls(
            session_id=d.get("session_id", ""),
            conversation_count=d.get("conversation_count", 0),
            warmup_threshold=d.get("warmup_threshold", 1),
            buffered_message_ids=d.get("buffered_message_ids") or [],
            last_l1_at=d.get("last_l1_at"),
            last_l2_at=d.get("last_l2_at"),
            pending_l2=d.get("pending_l2", False),
        )
