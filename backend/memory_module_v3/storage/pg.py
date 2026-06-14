"""PostgreSQL connection pool & schema initialization for memory_module_v3."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

_pool: Any | None = None  # psycopg_pool.ConnectionPool when available


def _get_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if dsn:
        return dsn
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    db = os.getenv("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def get_pool() -> Any:
    """Return a shared connection pool (creates on first call)."""
    global _pool
    if _pool is not None:
        return _pool
    try:
        from psycopg_pool import ConnectionPool
    except ImportError:
        logger.warning(
            "psycopg_pool not installed; falling back to per-call connections. "
            "Install with: pip install 'psycopg_pool'"
        )
        return None
    dsn = _get_dsn()
    _pool = ConnectionPool(conninfo=dsn, min_size=2, max_size=10)
    logger.info("memory_v3 connection pool created (dsn=%s)", dsn.split("@")[-1])
    return _pool


def get_connection(*, autocommit: bool = False) -> psycopg.Connection[Any]:
    """Get a connection from the pool, or create a new one if pool unavailable."""
    pool = get_pool()
    if pool is not None:
        conn = pool.getconn()
        # psycopg_pool returns connections with autocommit=False by default.
        # We must set autocommit on the connection itself when requested.
        if autocommit and not conn.autocommit:
            conn.autocommit = True
        return conn
    return psycopg.connect(_get_dsn(), autocommit=autocommit)


def put_connection(conn: psycopg.Connection[Any]) -> None:
    """Return a connection to the pool (no-op if pool unavailable)."""
    pool = get_pool()
    if pool is not None:
        # Reset autocommit to pool default before returning
        if conn.autocommit:
            conn.autocommit = False
        pool.putconn(conn)


def _split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            current.append(line)
            if stripped.endswith(";") and current:
                stmt = "\n".join(current).strip()
                if stmt and not all(l.strip().startswith("--") or not l.strip() for l in current):
                    statements.append(stmt)
                current = []
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        stmt = "\n".join(current).strip()
        if stmt:
            statements.append(stmt)
    return [s for s in statements if s.strip()]


def ensure_schema() -> None:
    """Create the memory_v3 schema and tables if they don't exist.

    Idempotent: safe to call on every startup.
    """
    try:
        conn = get_connection(autocommit=True)
        try:
            with conn.cursor() as cur:
                for statement in _split_statements(_SCHEMA_SQL):
                    cur.execute(statement)
            logger.info("memory_v3 schema initialized successfully")
        finally:
            put_connection(conn)
    except Exception as exc:
        logger.error(
            "Failed to initialize memory_v3 schema. "
            "Make sure PostgreSQL is running and pgvector extension is available. "
            "Error: %s",
            exc,
        )
        raise
