"""Repository for pipeline state (PostgreSQL)."""

from __future__ import annotations

import logging
from typing import Any

from .pg import get_connection, put_connection

logger = logging.getLogger(__name__)


class PipelineStateRepo:
    """CRUD for memory_v3.pipeline_state."""

    def get(self, session_id: str) -> dict[str, Any] | None:
        sql = """
            SELECT session_id, conversation_count, warmup_threshold,
                   buffered_message_ids, last_l1_at, last_l2_at, last_l3_at,
                   last_l3_fact_count, pending_l2, updated_at
            FROM memory_v3.pipeline_state WHERE session_id = %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (session_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            put_connection(conn)

    def upsert(self, state: dict[str, Any]) -> None:
        sql = """
            INSERT INTO memory_v3.pipeline_state
                (session_id, conversation_count, warmup_threshold,
                 buffered_message_ids, last_l1_at, last_l2_at, last_l3_at,
                 last_l3_fact_count, pending_l2, updated_at)
            VALUES (%(session_id)s, %(conversation_count)s, %(warmup_threshold)s,
                    %(buffered_message_ids)s, %(last_l1_at)s, %(last_l2_at)s,
                    %(last_l3_at)s, %(last_l3_fact_count)s, %(pending_l2)s, now())
            ON CONFLICT (session_id) DO UPDATE SET
                conversation_count = EXCLUDED.conversation_count,
                warmup_threshold = EXCLUDED.warmup_threshold,
                buffered_message_ids = EXCLUDED.buffered_message_ids,
                last_l1_at = EXCLUDED.last_l1_at,
                last_l2_at = EXCLUDED.last_l2_at,
                last_l3_at = EXCLUDED.last_l3_at,
                last_l3_fact_count = EXCLUDED.last_l3_fact_count,
                pending_l2 = EXCLUDED.pending_l2,
                updated_at = now()
        """
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, state)
        finally:
            put_connection(conn)
