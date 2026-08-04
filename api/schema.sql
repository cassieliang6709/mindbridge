-- MindBridge three-tier memory schema.
-- {embedding_dim} is substituted from MINDBRIDGE_EMBEDDING_DIM at startup, so
-- the vector column width always matches the configured embedding provider.

CREATE EXTENSION IF NOT EXISTS vector;

-- --- T1: session buffer -------------------------------------------------
-- Raw turns exactly as they arrived. Nothing is summarised or deleted here;
-- the "window" is a read concern (ORDER BY created_at DESC LIMIT n), which
-- keeps eviction auditable instead of destructive.
CREATE TABLE IF NOT EXISTS session_turns (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content     TEXT        NOT NULL,
    tool        TEXT,
    token_count INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS session_turns_session_created_idx
    ON session_turns (session_id, created_at DESC);

-- --- T2: rolling summary ------------------------------------------------
-- One structured card per period. Re-running a day's batch updates the card
-- in place rather than appending a second one.
CREATE TABLE IF NOT EXISTS rolling_summaries (
    id                        BIGSERIAL PRIMARY KEY,
    session_id                TEXT,
    period                    TEXT        NOT NULL,
    summary                   TEXT        NOT NULL,
    developer_behavior_facts  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    token_count               INTEGER     NOT NULL DEFAULT 0,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A period is unique per session; the NULL-session (global) card is handled
-- by a second partial index because NULL never equals NULL in a UNIQUE.
CREATE UNIQUE INDEX IF NOT EXISTS rolling_summaries_session_period_key
    ON rolling_summaries (session_id, period)
    WHERE session_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rolling_summaries_global_period_key
    ON rolling_summaries (period)
    WHERE session_id IS NULL;

-- M2 extraction output. `summary` stays the deterministic rule-based headline;
-- `narrative` is model-written prose. Keeping both means the card can always
-- fall back to the reproducible version, and `generated_by` records which one
-- the UI is showing — a model-written card must never be indistinguishable
-- from a computed one.
ALTER TABLE rolling_summaries
    ADD COLUMN IF NOT EXISTS narrative     TEXT,
    ADD COLUMN IF NOT EXISTS open_threads  JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS generated_by  TEXT NOT NULL DEFAULT 'rule',
    ADD COLUMN IF NOT EXISTS model         TEXT,
    ADD COLUMN IF NOT EXISTS extracted_at  TIMESTAMPTZ;

-- --- T3: long-term vector memory ---------------------------------------
-- valid_at is the bitemporal half of the design: created_at says when we
-- learned the fact, valid_at says when it stopped being true. Superseding a
-- preference closes the old row instead of overwriting it, so the history of
-- what the user used to prefer survives.
CREATE TABLE IF NOT EXISTS memory_vectors (
    id               BIGSERIAL PRIMARY KEY,
    content          TEXT        NOT NULL,
    category         TEXT        NOT NULL DEFAULT 'other',
    embedding        VECTOR({embedding_dim}) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_at         TIMESTAMPTZ,
    superseded_by    BIGINT      REFERENCES memory_vectors (id) ON DELETE SET NULL,
    access_count     INTEGER     NOT NULL DEFAULT 0,
    decay_factor     REAL        NOT NULL DEFAULT 1.0,
    last_accessed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS memory_vectors_open_idx
    ON memory_vectors (category, created_at DESC)
    WHERE valid_at IS NULL;

-- Cosine ANN index. Rebuild (or raise lists) once the table is large; below a
-- few thousand rows Postgres will sequential-scan anyway, which is exact.
CREATE INDEX IF NOT EXISTS memory_vectors_embedding_idx
    ON memory_vectors USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- --- Path A ingestion cursors -------------------------------------------
-- One row per transcript file. bytes_read is a resume point: these logs are
-- append-only, so re-running ingestion only reads what was added since. If a
-- file shrinks (rotated or rewritten) the reader restarts it from zero.
CREATE TABLE IF NOT EXISTS ingest_cursors (
    source          TEXT        NOT NULL,
    path            TEXT        NOT NULL,
    bytes_read      BIGINT      NOT NULL DEFAULT 0,
    turns_ingested  INTEGER     NOT NULL DEFAULT 0,
    last_uuid       TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, path)
);

-- Turns are keyed by their source record so re-ingesting a file cannot create
-- duplicates even if a cursor is lost or reset.
ALTER TABLE session_turns
    ADD COLUMN IF NOT EXISTS source_key TEXT;

-- Metadata the T2 digest needs. Persisted per turn so a day card can be
-- rebuilt from the database over the WHOLE day, instead of from whatever an
-- incremental run happened to parse. Without these columns a nightly run
-- rewrites the card using only that run's delta, which silently shrinks it.
ALTER TABLE session_turns
    ADD COLUMN IF NOT EXISTS project    TEXT,
    ADD COLUMN IF NOT EXISTS git_branch TEXT,
    ADD COLUMN IF NOT EXISTS tool_names JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS session_turns_created_idx
    ON session_turns (created_at);

CREATE UNIQUE INDEX IF NOT EXISTS session_turns_source_key_idx
    ON session_turns (source_key)
    WHERE source_key IS NOT NULL;
