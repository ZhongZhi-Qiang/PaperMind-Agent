"""Repository for L0 raw conversation messages."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .pg import get_connection, put_connection

logger = logging.getLogger(__name__)


@dataclass
class L0Message:
    msg_id: int | None = None
    session_id: str = ""
    role: str = ""
    content: str = ""
    ts: datetime | None = None
    embedding: list[float] | None = None


class L0Repo:
    """CRUD for memory_v3.l0_messages."""

    def insert(self, msg: L0Message) -> int:
        """Insert a message, return msg_id."""
        sql = """
            INSERT INTO memory_v3.l0_messages (session_id, role, content, ts, embedding)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING msg_id
        """
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    msg.session_id,
                    msg.role,
                    msg.content,
                    msg.ts or datetime.now(timezone.utc),
                    _format_vector(msg.embedding) if msg.embedding else None,
                ))
                return cur.fetchone()[0]
        finally:
            put_connection(conn)

    def insert_batch(self, messages: list[L0Message]) -> list[int]:
        """Insert multiple messages, return list of msg_ids."""
        if not messages:
            return []
        sql = """
            INSERT INTO memory_v3.l0_messages (session_id, role, content, ts, embedding)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING msg_id
        """
        ids: list[int] = []
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                for msg in messages:
                    cur.execute(sql, (
                        msg.session_id,
                        msg.role,
                        msg.content,
                        msg.ts or datetime.now(timezone.utc),
                        _format_vector(msg.embedding) if msg.embedding else None,
                    ))
                    ids.append(cur.fetchone()[0])
        finally:
            put_connection(conn)
        return ids

    def update_embedding(self, msg_id: int, embedding: list[float]) -> None:
        """Set embedding for a message (used by async background task)."""
        sql = "UPDATE memory_v3.l0_messages SET embedding = %s WHERE msg_id = %s"
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (_format_vector(embedding), msg_id))
        finally:
            put_connection(conn)

    def get_by_session(
        self, session_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT msg_id, session_id, role, content, ts
            FROM memory_v3.l0_messages
            WHERE session_id = %s
            ORDER BY ts DESC
            LIMIT %s OFFSET %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (session_id, limit, offset))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def get_by_ids(self, msg_ids: list[int]) -> list[dict[str, Any]]:
        if not msg_ids:
            return []
        sql = """
            SELECT msg_id, session_id, role, content, ts
            FROM memory_v3.l0_messages
            WHERE msg_id = ANY(%s)
            ORDER BY ts
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (msg_ids,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def messages_without_embedding(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get messages that need embedding computation."""
        sql = """
            SELECT msg_id, content
            FROM memory_v3.l0_messages
            WHERE embedding IS NULL
            ORDER BY ts DESC
            LIMIT %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def count(self) -> int:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM memory_v3.l0_messages")
                return cur.fetchone()[0]
        finally:
            put_connection(conn)

    def dense_search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
        *,
        session_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """pgvector cosine distance search on L0 messages."""
        conditions = ["embedding IS NOT NULL"]
        params: list[Any] = []
        if session_ids:
            conditions.append("session_id = ANY(%s)")
            params.append(session_ids)
        where = "WHERE " + " AND ".join(conditions)
        params = [_format_vector(query_embedding)] + params

        sql = f"""
            SELECT msg_id, session_id, role, content, ts,
                   1 - (embedding <=> %s::vector) AS cosine_sim
            FROM memory_v3.l0_messages
            {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params.append(_format_vector(query_embedding))
        params.append(top_k)

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)


def _format_vector(vec: list[float] | Any) -> str:
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return json.dumps(vec)
