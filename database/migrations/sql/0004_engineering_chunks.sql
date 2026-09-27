-- =====================================================================
-- DAYANERA.ai - engineering chunks (0004)
-- Hierarchical (parent/child) chunks with structural context, formula and
-- table payloads and chunk-level validation. Additive and reversible
-- (0004_engineering_chunks.down.sql); existing rows keep working unchanged
-- (chunk_role 'leaf', empty metadata) until a document is re-indexed.
-- Portable PostgreSQL (>= 13), Supabase-compatible: no extensions.
-- =====================================================================

ALTER TABLE document_chunks
    -- parent/child lineage: a child (formula, table row group, paragraph group) points to the
    -- section/table window it belongs to; retrieval searches leaves and children, and expands
    -- a hit with its parent's context
    ADD COLUMN parent_id uuid REFERENCES document_chunks(id) ON DELETE CASCADE,
    ADD COLUMN chunk_role text NOT NULL DEFAULT 'leaf'
        CONSTRAINT ck_document_chunks_role CHECK (chunk_role IN ('leaf', 'parent', 'child')),
    -- "4 Individual cylindrical gears" > "4.3 Involute helicoids" > "4.3.10 Base cylinder, ..."
    ADD COLUMN heading_path text[] NOT NULL DEFAULT '{}',
    -- structural overlap (derived, indexed): document > heading path, symbol meanings, table columns
    ADD COLUMN context text NOT NULL DEFAULT '',
    ADD COLUMN equation_numbers text[] NOT NULL DEFAULT '{}',
    ADD COLUMN table_numbers text[] NOT NULL DEFAULT '{}',
    ADD COLUMN figure_numbers text[] NOT NULL DEFAULT '{}',
    ADD COLUMN symbols text[] NOT NULL DEFAULT '{}',
    ADD COLUMN units text[] NOT NULL DEFAULT '{}',
    ADD COLUMN token_count integer,
    ADD COLUMN chunker_version text,
    -- [{number, raw, plain, latex, status, confidence, reasons, variables, lead_in, ...}]
    ADD COLUMN formula jsonb,
    -- {number, caption, columns, header_rows, rows, units, unit_note, footnotes, row_range, ...}
    ADD COLUMN table_data jsonb,
    ADD COLUMN meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- a chunk whose formula could not be reconstructed with certainty, or OCR text, needs review
    ADD COLUMN validation_status text NOT NULL DEFAULT 'ok'
        CONSTRAINT ck_document_chunks_validation CHECK (validation_status IN ('ok', 'needs_review')),
    -- hash of the structural metadata (role, parent, type, formula/LaTeX, table cells): the corpus
    -- fingerprint binds an approval to text AND structure
    ADD COLUMN meta_hash char(64);

-- content types of the engineering chunker (the 0002 values stay valid for existing rows)
ALTER TABLE document_chunks DROP CONSTRAINT ck_document_chunks_content_type;
ALTER TABLE document_chunks ADD CONSTRAINT ck_document_chunks_content_type CHECK (content_type IN (
    'text', 'table', 'formula', 'list', 'note', 'definition', 'front_matter',
    'normative_requirement', 'formula_context', 'variable_definition', 'table_row_group', 'figure_caption',
    'procedure', 'example', 'reference', 'general_text', 'section'));

-- Full-text vector: clause number and heading keep weight A; the structural context (heading path,
-- symbol meanings such as "db: base diameter", table columns) is weight C, above running text (D).
DROP INDEX IF EXISTS ix_document_chunks_tsv;
ALTER TABLE document_chunks DROP COLUMN tsv;
ALTER TABLE document_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple'::regconfig, coalesce(clause, '') || ' ' || coalesce(heading, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(heading, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(context, '')), 'C')
    || setweight(to_tsvector('simple'::regconfig, coalesce(context, '')), 'C')
    || to_tsvector('english'::regconfig, coalesce(text, ''))
    || to_tsvector('simple'::regconfig, coalesce(text, ''))
) STORED;
CREATE INDEX ix_document_chunks_tsv ON document_chunks USING gin (tsv);
CREATE INDEX ix_document_chunks_parent ON document_chunks (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX ix_document_chunks_role ON document_chunks (version_id, chunk_role);
