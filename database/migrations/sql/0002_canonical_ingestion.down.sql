-- =====================================================================
-- DAYANERA.ai - revert 0002_canonical_ingestion
-- Removes only the lifecycle / lineage objects added by 0002. Raw versions,
-- pages, chunks and the append-only audit log (which records every approval)
-- are preserved. After a downgrade the 0001 retrieval rules apply again.
-- =====================================================================
DROP INDEX IF EXISTS ix_document_chunks_clause;
ALTER TABLE document_chunks DROP COLUMN tsv;
ALTER TABLE document_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    to_tsvector('english'::regconfig, coalesce(text, ''))
    || to_tsvector('simple'::regconfig, coalesce(text, ''))
) STORED;
CREATE INDEX ix_document_chunks_tsv ON document_chunks USING gin (tsv);

ALTER TABLE document_chunks
    DROP COLUMN page_start,
    DROP COLUMN page_end,
    DROP COLUMN clause,
    DROP COLUMN heading,
    DROP COLUMN content_type,
    DROP COLUMN standard_code,
    DROP COLUMN extraction_confidence,
    DROP COLUMN source_hash,
    DROP COLUMN content_hash,
    DROP COLUMN parser,
    DROP COLUMN parser_version;

ALTER TABLE document_pages
    DROP COLUMN parser,
    DROP COLUMN parser_version,
    DROP COLUMN quality;

DROP INDEX IF EXISTS ix_document_versions_corpus;
ALTER TABLE document_versions
    DROP CONSTRAINT ck_document_versions_verified_lineage,
    DROP COLUMN corpus_status,
    DROP COLUMN quality_report,
    DROP COLUMN parser,
    DROP COLUMN parser_version,
    DROP COLUMN verified_by,
    DROP COLUMN verified_at,
    DROP COLUMN verified_fingerprint,
    DROP COLUMN review_note;
