-- memory_module_v3: Four-layer memory pyramid schema
-- Requires: PostgreSQL with pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS memory_v3;

-- L0: Raw conversation messages
CREATE TABLE IF NOT EXISTS memory_v3.l0_messages (
    msg_id      BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding   vector(1024)
);

CREATE INDEX IF NOT EXISTS ix_l0_messages_session_ts
    ON memory_v3.l0_messages(session_id, ts);

-- L1: Structured atomic facts
CREATE TABLE IF NOT EXISTS memory_v3.l1_facts (
    fact_id        BIGSERIAL PRIMARY KEY,
    content        TEXT NOT NULL,
    fact_type      TEXT NOT NULL,          -- persona / episodic / instruction
    priority       INT DEFAULT 0,
    scene_name     TEXT,
    source_msg_ids BIGINT[],
    timestamps     TIMESTAMPTZ[],
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id     TEXT,
    embedding      vector(1024),
    content_tsv    TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);

CREATE INDEX IF NOT EXISTS ix_l1_facts_type
    ON memory_v3.l1_facts(fact_type);

CREATE INDEX IF NOT EXISTS ix_l1_facts_scene
    ON memory_v3.l1_facts(scene_name);

CREATE INDEX IF NOT EXISTS ix_l1_facts_tsv
    ON memory_v3.l1_facts USING GIN (content_tsv);

-- HNSW index for pgvector cosine search (created after first embeddings are written)
-- Uncomment when enough data exists:
-- CREATE INDEX IF NOT EXISTS ix_l1_facts_embedding_hnsw
--     ON memory_v3.l1_facts USING hnsw (embedding vector_cosine_ops);

-- L2: Scene blocks
CREATE TABLE IF NOT EXISTS memory_v3.l2_scenes (
    scene_id    BIGSERIAL PRIMARY KEY,
    scene_name  TEXT NOT NULL UNIQUE,
    content_md  TEXT NOT NULL,
    fact_ids    BIGINT[],
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding   vector(1024)
);

-- HNSW index for L2 scene search
-- CREATE INDEX IF NOT EXISTS ix_l2_scenes_embedding_hnsw
--     ON memory_v3.l2_scenes USING hnsw (embedding vector_cosine_ops);

-- Pipeline state per session
CREATE TABLE IF NOT EXISTS memory_v3.pipeline_state (
    session_id            TEXT PRIMARY KEY,
    conversation_count    INT DEFAULT 0,
    warmup_threshold      INT DEFAULT 1,
    buffered_message_ids  BIGINT[],
    last_l1_at            TIMESTAMPTZ,
    last_l2_at            TIMESTAMPTZ,
    pending_l2            BOOLEAN DEFAULT FALSE,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- KV store for singletons (L3 persona, etc.)
CREATE TABLE IF NOT EXISTS memory_v3.kv_store (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
