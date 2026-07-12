"""Repository for L1 structured atomic facts with pgvector HNSW + tsvector FTS."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .pg import get_connection, put_connection

logger = logging.getLogger(__name__)


@dataclass
class L1Fact:
    fact_id: int | None = None
    content: str = ""
    fact_type: str = ""          # persona / episodic / instruction
    priority: int = 0
    scene_name: str | None = None
    source_msg_ids: list[int] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    session_id: str | None = None
    embedding: list[float] | None = None


class L1Repo:
    """CRUD for memory_v3.l1_facts with hybrid search."""

    def insert(self, fact: L1Fact) -> int:
        """Insert a fact, return fact_id."""
        sql = """
            INSERT INTO memory_v3.l1_facts
                (content, fact_type, priority, scene_name, source_msg_ids,
                 timestamps, session_id, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING fact_id
        """
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    fact.content,
                    fact.fact_type,
                    fact.priority,
                    fact.scene_name,
                    fact.source_msg_ids or None,
                    fact.timestamps or None,
                    fact.session_id,
                    _format_vector(fact.embedding) if fact.embedding else None,
                ))
                return cur.fetchone()[0]
        finally:
            put_connection(conn)

    def update(self, fact: L1Fact) -> None:
        """Update an existing fact."""
        sql = """
            UPDATE memory_v3.l1_facts SET
                content = %s, fact_type = %s, priority = %s, scene_name = %s,
                source_msg_ids = %s, timestamps = %s, session_id = %s,
                embedding = %s, updated_at = now()
            WHERE fact_id = %s
        """
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    fact.content,
                    fact.fact_type,
                    fact.priority,
                    fact.scene_name,
                    fact.source_msg_ids or None,
                    fact.timestamps or None,
                    fact.session_id,
                    _format_vector(fact.embedding) if fact.embedding else None,
                    fact.fact_id,
                ))
        finally:
            put_connection(conn)

    def update_embedding(self, fact_id: int, embedding: list[float]) -> None:
        """Set embedding for a fact (async background task)."""
        sql = "UPDATE memory_v3.l1_facts SET embedding = %s WHERE fact_id = %s"
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (_format_vector(embedding), fact_id))
        finally:
            put_connection(conn)

    def get_by_id(self, fact_id: int) -> dict[str, Any] | None:
        sql = """
            SELECT fact_id, content, fact_type, priority, scene_name,
                   source_msg_ids, timestamps, created_at, updated_at, session_id
            FROM memory_v3.l1_facts WHERE fact_id = %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (fact_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            put_connection(conn)

    def get_by_scene(self, scene_name: str) -> list[dict[str, Any]]:
        sql = """
            SELECT fact_id, content, fact_type, priority, scene_name,
                   source_msg_ids, timestamps, created_at, session_id
            FROM memory_v3.l1_facts
            WHERE scene_name = %s
            ORDER BY priority DESC, created_at DESC
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (scene_name,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def get_all(self, *, limit: int = 500) -> list[dict[str, Any]]:
        sql = """
            SELECT fact_id, content, fact_type, priority, scene_name,
                   source_msg_ids, timestamps, created_at, session_id
            FROM memory_v3.l1_facts
            ORDER BY created_at DESC
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

    def get_since(self, since: datetime, *, limit: int = 500) -> list[dict[str, Any]]:
        """Fetch facts created or updated after a given timestamp (for incremental L2)."""
        sql = """
            SELECT fact_id, content, fact_type, priority, scene_name,
                   source_msg_ids, timestamps, created_at, session_id
            FROM memory_v3.l1_facts
            WHERE created_at > %s
            ORDER BY created_at ASC
            LIMIT %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (since, limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def facts_without_embedding(self, limit: int = 100) -> list[dict[str, Any]]:
        sql = """
            SELECT fact_id, content
            FROM memory_v3.l1_facts
            WHERE embedding IS NULL
            ORDER BY created_at DESC
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
                cur.execute("SELECT count(*) FROM memory_v3.l1_facts")
                return cur.fetchone()[0]
        finally:
            put_connection(conn)

    # --- Search methods ---

    def dense_search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """pgvector cosine distance search using HNSW index."""
        conditions = ["embedding IS NOT NULL"]
        params: list[Any] = []

        if fact_type:
            conditions.append("fact_type = %s")
            params.append(fact_type)
        if scene_name:
            conditions.append("scene_name = %s")
            params.append(scene_name)

        where = "WHERE " + " AND ".join(conditions)
        vec_str = _format_vector(query_embedding)
        params = [vec_str] + params + [vec_str, top_k]

        sql = f"""
            SELECT fact_id, content, fact_type, priority, scene_name,
                   source_msg_ids, created_at, session_id,
                   1 - (embedding <=> %s::vector) AS cosine_sim
            FROM memory_v3.l1_facts
            {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            return rows
        finally:
            put_connection(conn)

    def keyword_search(
        self,
        query: str,
        top_k: int = 20,
        *,
        fact_type: str | None = None,
        scene_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search using tsvector GIN index.

        Uses plainto_tsquery('simple', ...) for Chinese-friendly tokenization.
        """
        conditions = ["content_tsv @@ plainto_tsquery('simple', %s)"]
        params: list[Any] = [query]

        if fact_type:
            conditions.append("fact_type = %s")
            params.append(fact_type)
        if scene_name:
            conditions.append("scene_name = %s")
            params.append(scene_name)

        where = "WHERE " + " AND ".join(conditions)
        params.append(top_k)

        sql = f"""
            SELECT fact_id, content, fact_type, priority, scene_name,
                   source_msg_ids, created_at, session_id,
                   ts_rank_cd(content_tsv, plainto_tsquery('simple', %s)) AS rank_score
            FROM memory_v3.l1_facts
            {where}
            ORDER BY rank_score DESC
            LIMIT %s
        """
        # Need to pass query twice: once for WHERE, once for rank
        rank_params = [query] + params

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, rank_params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            put_connection(conn)

    def delete_expired(self, cutoff_iso: str) -> int:
        """Delete facts older than cutoff. Returns count deleted."""
        sql = "DELETE FROM memory_v3.l1_facts WHERE created_at < %s"
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (cutoff_iso,))
                return cur.rowcount
        finally:
            put_connection(conn)


def _format_vector(vec: list[float] | Any) -> str:
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return json.dumps(vec)
