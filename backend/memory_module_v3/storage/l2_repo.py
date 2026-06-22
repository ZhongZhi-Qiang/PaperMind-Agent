"""Repository for L2 scene blocks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .pg import get_connection, put_connection

logger = logging.getLogger(__name__)


@dataclass
class L2Scene:
    scene_id: int | None = None
    scene_name: str = ""
    content_md: str = ""
    fact_ids: list[int] = field(default_factory=list)
    updated_at: datetime | None = None
    embedding: list[float] | None = None


class L2Repo:
    """CRUD for memory_v3.l2_scenes."""

    def upsert(self, scene: L2Scene) -> int:
        """Insert or update a scene block. Returns scene_id."""
        sql = """
            INSERT INTO memory_v3.l2_scenes (scene_name, content_md, fact_ids, embedding)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (scene_name) DO UPDATE SET
                content_md = EXCLUDED.content_md,
                fact_ids = EXCLUDED.fact_ids,
                embedding = EXCLUDED.embedding,
                updated_at = now()
            RETURNING scene_id
        """
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    scene.scene_name,
                    scene.content_md,
                    scene.fact_ids or None,
                    _format_vector(scene.embedding) if scene.embedding else None,
                ))
                return cur.fetchone()[0]
        finally:
            put_connection(conn)

    def get_by_name(self, scene_name: str) -> dict[str, Any] | None:
        sql = """
            SELECT scene_id, scene_name, content_md, fact_ids, updated_at
            FROM memory_v3.l2_scenes WHERE scene_name = %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (scene_name,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            put_connection(conn)

    def get_all(self) -> list[dict[str, Any]]:
        sql = """
            SELECT scene_id, scene_name, content_md, fact_ids, updated_at
            FROM memory_v3.l2_scenes
            ORDER BY updated_at DESC
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def list_names(self) -> list[str]:
        sql = "SELECT scene_name FROM memory_v3.l2_scenes ORDER BY scene_name"
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def update_embedding(self, scene_id: int, embedding: list[float]) -> None:
        sql = "UPDATE memory_v3.l2_scenes SET embedding = %s WHERE scene_id = %s"
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (_format_vector(embedding), scene_id))
        finally:
            put_connection(conn)

    def count(self) -> int:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM memory_v3.l2_scenes")
                return cur.fetchone()[0]
        finally:
            put_connection(conn)

    def dense_search(
        self, query_embedding: list[float], top_k: int = 10
    ) -> list[dict[str, Any]]:
        """pgvector cosine search over scene embeddings."""
        vec_str = _format_vector(query_embedding)
        sql = """
            SELECT scene_id, scene_name, content_md, fact_ids,
                   1 - (embedding <=> %s::vector) AS cosine_sim
            FROM memory_v3.l2_scenes
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (vec_str, vec_str, top_k))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def delete_by_name(self, scene_name: str) -> bool:
        sql = "DELETE FROM memory_v3.l2_scenes WHERE scene_name = %s"
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (scene_name,))
                return cur.rowcount > 0
        finally:
            put_connection(conn)


# --- Pipeline state repo ---

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


# --- KV store repo ---

class KVRepo:
    """CRUD for memory_v3.kv_store."""

    def get(self, key: str) -> str | None:
        sql = "SELECT value FROM memory_v3.kv_store WHERE key = %s"
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (key,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            put_connection(conn)

    def set(self, key: str, value: str) -> None:
        sql = """
            INSERT INTO memory_v3.kv_store (key, value, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (key, value))
        finally:
            put_connection(conn)

    def delete(self, key: str) -> bool:
        sql = "DELETE FROM memory_v3.kv_store WHERE key = %s"
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (key,))
                return cur.rowcount > 0
        finally:
            put_connection(conn)


def _format_vector(vec: list[float] | Any) -> str:
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return json.dumps(vec)
