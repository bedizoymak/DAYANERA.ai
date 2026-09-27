-- =====================================================================
-- DAYANERA.ai - revert 0004_engineering_chunks
-- Removes the engineering-chunk metadata. Parent rows (context windows that
-- duplicate their children) are deleted so that the 0002 retrieval does not
-- return the same text twice; message_sources keep their page/excerpt
-- provenance (chunk_id becomes NULL for deleted parents). New content types are
-- mapped back to the 0002 set. Re-index the corpus after a downgrade
-- (python -m app.cli reindex) to return to the 0002 chunking.
-- =====================================================================
DELETE FROM document_chunks WHERE chunk_role = 'parent';

UPDATE document_chunks SET content_type = CASE content_type
    WHEN 'normative_requirement' THEN 'text'
    WHEN 'formula_context' THEN 'text'
    WHEN 'general_text' THEN 'text'
    WHEN 'procedure' THEN 'list'
    WHEN 'reference' THEN 'text'
    WHEN 'figure_caption' THEN 'text'
    WHEN 'variable_definition' THEN 'definition'
    WHEN 'table_row_group' THEN 'table'
    WHEN 'example' THEN 'note'
    WHEN 'section' THEN 'text'
    ELSE content_type END;

ALTER TABLE document_chunks DROP CONSTRAINT ck_document_chunks_content_type;
ALTER TABLE document_chunks ADD CONSTRAINT ck_document_chunks_content_type
    CHECK (content_type IN ('text', 'table', 'formula', 'list', 'note', 'definition', 'front_matter'));

DROP INDEX IF EXISTS ix_document_chunks_parent;
DROP INDEX IF EXISTS ix_document_chunks_role;
DROP INDEX IF EXISTS ix_document_chunks_tsv;
ALTER TABLE document_chunks DROP COLUMN tsv;
ALTER TABLE document_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple'::regconfig, coalesce(clause, '') || ' ' || coalesce(heading, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(heading, '')), 'A')
    || to_tsvector('english'::regconfig, coalesce(text, ''))
    || to_tsvector('simple'::regconfig, coalesce(text, ''))
) STORED;
CREATE INDEX ix_document_chunks_tsv ON document_chunks USING gin (tsv);

ALTER TABLE document_chunks
    DROP COLUMN parent_id,
    DROP COLUMN chunk_role,
    DROP COLUMN heading_path,
    DROP COLUMN context,
    DROP COLUMN equation_numbers,
    DROP COLUMN table_numbers,
    DROP COLUMN figure_numbers,
    DROP COLUMN symbols,
    DROP COLUMN units,
    DROP COLUMN token_count,
    DROP COLUMN chunker_version,
    DROP COLUMN formula,
    DROP COLUMN table_data,
    DROP COLUMN meta,
    DROP COLUMN validation_status,
    DROP COLUMN meta_hash;
