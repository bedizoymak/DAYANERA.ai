-- =====================================================================
-- DAYANERA.ai - initial schema (0001)
-- Portable PostgreSQL (>= 13) SQL. Compatible with Supabase Postgres:
-- no vendor extensions are required (gen_random_uuid() is core since 13).
-- Full-text search (tsvector + GIN) is the baseline retrieval mechanism.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Users, sessions, roles and data scopes
-- ---------------------------------------------------------------------
CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username        text NOT NULL UNIQUE,
    display_name    text NOT NULL,
    password_hash   text NOT NULL,
    role            text NOT NULL CHECK (role IN ('owner_admin', 'member')),
    is_active       boolean NOT NULL DEFAULT true,
    created_by      uuid REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    token_hash      text NOT NULL UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz,
    user_agent      text,
    client_addr     text
);
CREATE INDEX ix_auth_sessions_user ON auth_sessions (user_id);

CREATE TABLE knowledge_areas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                text NOT NULL UNIQUE,
    name                text NOT NULL,
    description         text,
    is_verified_corpus  boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_scopes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id),
    scope_type  text NOT NULL CHECK (scope_type IN ('knowledge_area', 'document', 'conversation')),
    scope_id    uuid NOT NULL,
    granted_by  uuid REFERENCES users(id),
    granted_at  timestamptz NOT NULL DEFAULT now(),
    revoked_by  uuid REFERENCES users(id),
    revoked_at  timestamptz
);
CREATE UNIQUE INDEX ux_user_scopes_active
    ON user_scopes (user_id, scope_type, scope_id) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------
-- Documents, versions (lineage), pages, chunks and ingestion
-- ---------------------------------------------------------------------
CREATE TABLE documents (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_area_id   uuid REFERENCES knowledge_areas(id),
    title               text NOT NULL,
    standard_code       text,
    source_kind         text NOT NULL CHECK (source_kind IN ('watched', 'upload', 'attachment')),
    source_root         text,
    source_relpath      text,
    original_filename   text NOT NULL,
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'active', 'deleted', 'archived', 'failed')),
    current_version_id  uuid,
    owner_id            uuid REFERENCES users(id),
    created_by          uuid REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    deleted_by          uuid REFERENCES users(id),
    delete_reason       text
);
CREATE UNIQUE INDEX ux_documents_watched_path
    ON documents (source_root, source_relpath) WHERE source_kind = 'watched';
CREATE INDEX ix_documents_status ON documents (status);
CREATE INDEX ix_documents_area ON documents (knowledge_area_id);

CREATE TABLE document_versions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id          uuid NOT NULL REFERENCES documents(id),
    version_number       integer NOT NULL,
    sha256               char(64) NOT NULL,
    size_bytes           bigint NOT NULL,
    mime_type            text NOT NULL,
    file_extension       text,
    storage_relpath      text NOT NULL,
    source_path_snapshot text,
    source_mtime         timestamptz,
    -- lifecycle state (historical states are never active technical sources)
    state                text NOT NULL DEFAULT 'pending'
                         CHECK (state IN ('pending', 'active', 'superseded', 'deleted', 'archived', 'failed')),
    is_active            boolean NOT NULL DEFAULT false,
    -- processing outcome
    ingestion_status     text NOT NULL DEFAULT 'queued'
                         CHECK (ingestion_status IN ('queued', 'processing', 'indexed', 'stored_only', 'failed')),
    ingestion_error      text,
    page_count           integer,
    extraction_summary   jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by           uuid REFERENCES users(id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    ingested_at          timestamptz,
    superseded_at        timestamptz,
    UNIQUE (document_id, version_number)
);
CREATE UNIQUE INDEX ux_document_versions_one_active
    ON document_versions (document_id) WHERE is_active;
CREATE INDEX ix_document_versions_sha ON document_versions (sha256);

ALTER TABLE documents
    ADD CONSTRAINT fk_documents_current_version
    FOREIGN KEY (current_version_id) REFERENCES document_versions(id);

CREATE TABLE document_pages (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         uuid NOT NULL REFERENCES documents(id),
    version_id          uuid NOT NULL REFERENCES document_versions(id),
    page_number         integer NOT NULL,
    locator             text NOT NULL,
    text                text NOT NULL,
    extraction_method   text NOT NULL,
    confidence_status   text NOT NULL
                        CHECK (confidence_status IN ('verified_source', 'user_confirmed', 'draft_extraction',
                                                     'unverified', 'superseded', 'deleted', 'archived', 'rejected')),
    ocr_mean_confidence double precision,
    confirmed_by        uuid REFERENCES users(id),
    confirmed_at        timestamptz,
    review_note         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (version_id, page_number)
);
CREATE INDEX ix_document_pages_status ON document_pages (confidence_status);

CREATE TABLE document_chunks (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         uuid NOT NULL REFERENCES documents(id),
    version_id          uuid NOT NULL REFERENCES document_versions(id),
    page_id             uuid REFERENCES document_pages(id) ON DELETE CASCADE,
    chunk_index         integer NOT NULL,
    page_number         integer,
    locator             text NOT NULL,
    char_start          integer NOT NULL DEFAULT 0,
    char_end            integer NOT NULL DEFAULT 0,
    text                text NOT NULL,
    extraction_method   text NOT NULL,
    confidence_status   text NOT NULL
                        CHECK (confidence_status IN ('verified_source', 'user_confirmed', 'draft_extraction',
                                                     'unverified', 'superseded', 'deleted', 'archived', 'rejected')),
    tsv                 tsvector GENERATED ALWAYS AS (
                            to_tsvector('english'::regconfig, coalesce(text, ''))
                            || to_tsvector('simple'::regconfig, coalesce(text, ''))
                        ) STORED,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (version_id, chunk_index)
);
CREATE INDEX ix_document_chunks_tsv ON document_chunks USING gin (tsv);
CREATE INDEX ix_document_chunks_version ON document_chunks (version_id);
CREATE INDEX ix_document_chunks_status ON document_chunks (confidence_status);

CREATE TABLE archive_members (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id       uuid NOT NULL REFERENCES document_versions(id),
    member_path      text NOT NULL,
    is_dir           boolean NOT NULL DEFAULT false,
    size_bytes       bigint,
    compressed_bytes bigint,
    mime_type        text,
    sha256           char(64),
    status           text NOT NULL CHECK (status IN ('extracted', 'catalogued', 'skipped', 'blocked')),
    reason           text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_archive_members_version ON archive_members (version_id);

CREATE TABLE document_relationships (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_document_id uuid NOT NULL REFERENCES documents(id),
    to_document_id   uuid NOT NULL REFERENCES documents(id),
    relation_type    text NOT NULL CHECK (relation_type IN ('references', 'same_standard', 'derived_from', 'related')),
    note             text,
    created_by       uuid REFERENCES users(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (from_document_id, to_document_id, relation_type)
);

CREATE TABLE ingestion_jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL REFERENCES documents(id),
    version_id    uuid NOT NULL REFERENCES document_versions(id),
    job_type      text NOT NULL CHECK (job_type IN ('ingest', 'reindex')),
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    attempts      integer NOT NULL DEFAULT 0,
    requested_by  uuid REFERENCES users(id),
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz
);
CREATE INDEX ix_ingestion_jobs_status ON ingestion_jobs (status, created_at);

-- Draft extraction values (OCR / vision / transcription / parsing).
CREATE TABLE extracted_values (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             uuid NOT NULL REFERENCES documents(id),
    version_id              uuid NOT NULL REFERENCES document_versions(id),
    page_id                 uuid REFERENCES document_pages(id) ON DELETE SET NULL,
    page_number             integer,
    locator                 text NOT NULL,
    quantity_kind           text,
    label                   text NOT NULL,
    context_text            text,
    raw_text                text NOT NULL,
    value_numeric           double precision,
    unit                    text,
    extraction_method       text NOT NULL,
    status                  text NOT NULL DEFAULT 'draft_extraction'
                            CHECK (status IN ('draft_extraction', 'user_confirmed', 'rejected', 'superseded', 'deleted')),
    original                jsonb NOT NULL DEFAULT '{}'::jsonb,
    confirmed_value_numeric double precision,
    confirmed_unit          text,
    confirmed_by            uuid REFERENCES users(id),
    confirmed_at            timestamptz,
    review_note             text,
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_extracted_values_status ON extracted_values (status);

-- ---------------------------------------------------------------------
-- Conversations, messages, attachments and source provenance
-- ---------------------------------------------------------------------
CREATE TABLE conversations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     uuid NOT NULL REFERENCES users(id),
    title        text NOT NULL,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    archived_at  timestamptz
);
CREATE INDEX ix_conversations_owner ON conversations (owner_id, status, updated_at DESC);

CREATE TABLE messages (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id      uuid NOT NULL REFERENCES conversations(id),
    author_user_id       uuid REFERENCES users(id),
    role                 text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content              text NOT NULL,
    answer_mode          text CHECK (answer_mode IN ('general', 'verified_source', 'calculation',
                                                     'draft_extraction', 'unverified')),
    status               text NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'streaming', 'error')),
    error_code           text,
    show_sources         boolean NOT NULL DEFAULT false,
    provider             text,
    model                text,
    latency_ms           integer,
    reply_to_message_id  uuid REFERENCES messages(id),
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    tsv                  tsvector GENERATED ALWAYS AS (
                             to_tsvector('simple'::regconfig, coalesce(content, ''))
                         ) STORED,
    created_at           timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_messages_conversation ON messages (conversation_id, created_at);
CREATE INDEX ix_messages_tsv ON messages USING gin (tsv);

CREATE TABLE message_attachments (
    message_id   uuid NOT NULL REFERENCES messages(id),
    document_id  uuid NOT NULL REFERENCES documents(id),
    version_id   uuid REFERENCES document_versions(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, document_id)
);

CREATE TABLE message_sources (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id         uuid NOT NULL REFERENCES messages(id),
    document_id        uuid NOT NULL REFERENCES documents(id),
    version_id         uuid NOT NULL REFERENCES document_versions(id),
    chunk_id           uuid REFERENCES document_chunks(id) ON DELETE SET NULL,
    rank               integer NOT NULL,
    score              double precision NOT NULL,
    page_number        integer,
    locator            text NOT NULL,
    excerpt            text NOT NULL,
    excerpt_start      integer NOT NULL DEFAULT 0,
    excerpt_end        integer NOT NULL DEFAULT 0,
    document_title     text NOT NULL,
    standard_code      text,
    version_number     integer NOT NULL,
    confidence_status  text NOT NULL,
    cited              boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_message_sources_message ON message_sources (message_id);

-- ---------------------------------------------------------------------
-- Deterministic calculations
-- ---------------------------------------------------------------------
CREATE TABLE calculations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id),
    conversation_id  uuid REFERENCES conversations(id),
    message_id       uuid REFERENCES messages(id),
    calc_type        text NOT NULL,
    engine_version   text NOT NULL,
    status           text NOT NULL CHECK (status IN ('ok', 'refused', 'invalid_input', 'error')),
    inputs           jsonb NOT NULL DEFAULT '{}'::jsonb,
    result           jsonb NOT NULL DEFAULT '{}'::jsonb,
    llm_draft        jsonb,
    comparison       jsonb,
    mismatch         boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_calculations_user ON calculations (user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- Persistent memory (facts, preferences, confirmed values, summaries)
-- ---------------------------------------------------------------------
CREATE TABLE memory_items (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id                   uuid NOT NULL REFERENCES users(id),
    visibility                 text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared')),
    kind                       text NOT NULL CHECK (kind IN ('fact', 'preference', 'technical_value',
                                                             'conversation_summary', 'source_link', 'relationship', 'note')),
    title                      text NOT NULL,
    content                    text NOT NULL,
    structured                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                     text NOT NULL CHECK (status IN ('verified_source', 'user_confirmed', 'draft_extraction',
                                                               'unverified', 'superseded', 'deleted', 'archived')),
    source_conversation_id     uuid REFERENCES conversations(id),
    source_message_id          uuid REFERENCES messages(id),
    source_document_id         uuid REFERENCES documents(id),
    source_version_id          uuid REFERENCES document_versions(id),
    source_extracted_value_id  uuid REFERENCES extracted_values(id),
    supersedes_id              uuid REFERENCES memory_items(id),
    superseded_by_id           uuid REFERENCES memory_items(id),
    created_by                 uuid REFERENCES users(id),
    confirmed_by               uuid REFERENCES users(id),
    confirmed_at               timestamptz,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    tsv                        tsvector GENERATED ALWAYS AS (
                                   to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(content, ''))
                               ) STORED
);
CREATE INDEX ix_memory_items_owner ON memory_items (owner_id, status);
CREATE INDEX ix_memory_items_tsv ON memory_items USING gin (tsv);

-- ---------------------------------------------------------------------
-- Recommendation notes index (files live only in agent-notes/)
-- ---------------------------------------------------------------------
CREATE TABLE recommendation_notes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    filename    text NOT NULL UNIQUE,
    title       text NOT NULL,
    status      text NOT NULL CHECK (status IN ('öneri', 'inceleniyor', 'uygulandı', 'reddedildi')),
    author      text NOT NULL,
    created_by  uuid REFERENCES users(id),
    sha256      char(64) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- System state (watcher status etc.)
-- ---------------------------------------------------------------------
CREATE TABLE system_state (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Immutable-style audit log
-- ---------------------------------------------------------------------
CREATE TABLE audit_events (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_user_id   uuid REFERENCES users(id),
    actor_username  text,
    session_id      uuid,
    event_type      text NOT NULL,
    target_type     text,
    target_id       text,
    outcome         text NOT NULL CHECK (outcome IN ('success', 'denied', 'failure', 'info')),
    client_addr     text,
    details         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_audit_events_time ON audit_events (occurred_at DESC);
CREATE INDEX ix_audit_events_actor ON audit_events (actor_user_id, occurred_at DESC);
CREATE INDEX ix_audit_events_type ON audit_events (event_type, occurred_at DESC);

CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = 'audit_events is append-only: ' || TG_OP || ' blocked',
        ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER trg_audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();

CREATE TRIGGER trg_audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_mutation();
