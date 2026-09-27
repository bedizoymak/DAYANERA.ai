-- =====================================================================
-- DAYANERA.ai - canonical ingestion (0002)
-- Verified-corpus lifecycle, chunk lineage and structure-aware full-text
-- indexing. Portable PostgreSQL (>= 13), Supabase-compatible: no extensions.
-- Reversible: 0002_canonical_ingestion.down.sql removes only the objects added
-- here (raw files, pages, chunks and audit records are untouched).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Corpus lifecycle of a version (its exact bytes + their extraction)
--   candidate     registered (raw stored, SHA-256 recorded), not yet through
--                 the canonical pipeline
--   extracted     extraction, chunking and indexing succeeded and every quality
--                 gate passed; awaiting approval
--   needs_review  extracted and indexed, but a quality gate asks for manual review
--   verified      approved by an owner_admin: the only state retrieval and the
--                 calculation engine accept as evidence
--   failed        extraction or indexing failed, or a blocking gate failed
-- ---------------------------------------------------------------------
ALTER TABLE document_versions
    ADD COLUMN corpus_status text NOT NULL DEFAULT 'candidate'
        CONSTRAINT ck_document_versions_corpus_status
        CHECK (corpus_status IN ('candidate', 'extracted', 'needs_review', 'verified', 'failed')),
    ADD COLUMN quality_report jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN parser text,
    ADD COLUMN parser_version text,
    ADD COLUMN verified_by uuid REFERENCES users(id),
    ADD COLUMN verified_at timestamptz,
    -- content fingerprint of the approved extraction (pipeline version + chunk hashes):
    -- re-indexing keeps "verified" only while the extraction is byte-identical
    ADD COLUMN verified_fingerprint char(64),
    ADD COLUMN review_note text;

ALTER TABLE document_versions
    ADD CONSTRAINT ck_document_versions_verified_lineage
    CHECK (corpus_status <> 'verified'
           OR (verified_by IS NOT NULL AND verified_at IS NOT NULL AND verified_fingerprint IS NOT NULL));

-- Versions ingested before quality gates existed are not verified: failed ones
-- stay failed, all others stay 'candidate' until re-processed (app.cli reindex).
UPDATE document_versions SET corpus_status = 'failed' WHERE ingestion_status = 'failed';

CREATE INDEX ix_document_versions_corpus ON document_versions (corpus_status) WHERE is_active;

-- ---------------------------------------------------------------------
-- Page-level parser provenance and quality metrics
-- ---------------------------------------------------------------------
ALTER TABLE document_pages
    ADD COLUMN parser text,
    ADD COLUMN parser_version text,
    ADD COLUMN quality jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------
-- Chunk lineage (document_id, version_id, page_id, chunk_index, extraction_method
-- and confidence_status already exist)
-- ---------------------------------------------------------------------
ALTER TABLE document_chunks
    ADD COLUMN page_start integer,
    ADD COLUMN page_end integer,
    ADD COLUMN clause text,
    ADD COLUMN heading text,
    ADD COLUMN content_type text NOT NULL DEFAULT 'text'
        CONSTRAINT ck_document_chunks_content_type
        CHECK (content_type IN ('text', 'table', 'formula', 'list', 'note', 'definition', 'front_matter')),
    ADD COLUMN standard_code text,
    ADD COLUMN extraction_confidence double precision,
    ADD COLUMN source_hash char(64),
    ADD COLUMN content_hash char(64),
    ADD COLUMN parser text,
    ADD COLUMN parser_version text;

UPDATE document_chunks c
SET page_start = c.page_number, page_end = c.page_number, source_hash = v.sha256, standard_code = d.standard_code
FROM document_versions v, documents d
WHERE v.id = c.version_id AND d.id = c.document_id;

-- Full-text vector: clause number and heading carry weight A so that "4.2.4" or
-- "Reference diameter" rank their own clause first; the 'simple' configuration keeps
-- engineering symbols (αP, hfP, mn) and clause numbers as exact lexemes.
ALTER TABLE document_chunks DROP COLUMN tsv;
ALTER TABLE document_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple'::regconfig, coalesce(clause, '') || ' ' || coalesce(heading, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(heading, '')), 'A')
    || to_tsvector('english'::regconfig, coalesce(text, ''))
    || to_tsvector('simple'::regconfig, coalesce(text, ''))
) STORED;
CREATE INDEX ix_document_chunks_tsv ON document_chunks USING gin (tsv);
CREATE INDEX ix_document_chunks_clause ON document_chunks (version_id, clause);
