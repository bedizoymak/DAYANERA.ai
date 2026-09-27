-- =====================================================================
-- DAYANERA.ai - self-maintaining engineering knowledge (0003)
-- Structured LLM-draft mismatch events and the correction memory built from
-- them. Portable PostgreSQL (>= 13), Supabase-compatible: no extensions.
-- Reversible: 0003_self_maintenance.down.sql drops only these two tables
-- (lifecycle history stays in the append-only audit_events table).
--
-- Authority: an LLM never promotes a correction. Only the deterministic
-- regression (app.knowledge.regression) moves CANDIDATE -> TESTING ->
-- VERIFIED; a failed regression makes it REJECTED. Only VERIFIED corrections
-- are retrieved into future Qwen prompts.
-- =====================================================================

CREATE TABLE engineering_corrections (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- general rule identity: "<formula family>:<root cause>" (never a numeric example)
    correction_key       text NOT NULL UNIQUE,
    family               text NOT NULL,
    root_cause           text NOT NULL,
    calc_type            text NOT NULL,
    output_keys          jsonb NOT NULL DEFAULT '[]'::jsonb,
    formula_ids          jsonb NOT NULL DEFAULT '[]'::jsonb,
    title                text NOT NULL,
    statement            text NOT NULL,
    claims               jsonb NOT NULL DEFAULT '[]'::jsonb,
    invariants           jsonb NOT NULL DEFAULT '[]'::jsonb,
    reference_values     jsonb NOT NULL DEFAULT '[]'::jsonb,
    source               text NOT NULL CHECK (source IN ('diagnosis', 'proposal')),
    proposed_by          text NOT NULL DEFAULT 'system',
    status               text NOT NULL DEFAULT 'CANDIDATE'
                         CHECK (status IN ('CANDIDATE', 'TESTING', 'VERIFIED', 'REJECTED')),
    regression_status    text NOT NULL DEFAULT 'not_run'
                         CHECK (regression_status IN ('not_run', 'running', 'passed', 'failed', 'blocked',
                                                      'not_applicable')),
    regression_result    jsonb NOT NULL DEFAULT '{}'::jsonb,
    engine_version       text,
    registry_fingerprint text,
    occurrences          integer NOT NULL DEFAULT 0,
    last_seen_at         timestamptz,
    verified_at          timestamptz,
    rejected_reason      text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    -- a VERIFIED correction always carries the engine/registry it was verified against
    CONSTRAINT ck_engineering_corrections_verified
        CHECK (status <> 'VERIFIED' OR (regression_status = 'passed' AND verified_at IS NOT NULL
                                        AND engine_version IS NOT NULL AND registry_fingerprint IS NOT NULL))
);
CREATE INDEX ix_engineering_corrections_lookup ON engineering_corrections (calc_type, status);

CREATE TABLE calc_mismatch_events (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    calculation_id      uuid REFERENCES calculations(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    calc_type           text NOT NULL,
    engine_version      text NOT NULL,
    model               text,
    -- numeric canonical inputs only (after engine defaults); never message text
    normalized_inputs   jsonb NOT NULL DEFAULT '{}'::jsonb,
    assumptions         jsonb NOT NULL DEFAULT '[]'::jsonb,
    llm_values          jsonb NOT NULL DEFAULT '{}'::jsonb,
    engine_values       jsonb NOT NULL DEFAULT '{}'::jsonb,
    fields              jsonb NOT NULL DEFAULT '[]'::jsonb,
    mismatching_fields  jsonb NOT NULL DEFAULT '[]'::jsonb,
    formula_ids         jsonb NOT NULL DEFAULT '[]'::jsonb,
    authority_sources   jsonb NOT NULL DEFAULT '[]'::jsonb,
    supporting_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    suspected_class     text NOT NULL,
    llm_suggested_class text,
    diagnosis           jsonb NOT NULL DEFAULT '{}'::jsonb,
    correction_id       uuid REFERENCES engineering_corrections(id),
    regression_status   text,
    disposition         text NOT NULL DEFAULT 'open'
                        CHECK (disposition IN ('open', 'verified_correction', 'known_verified_correction',
                                               'rejected_correction', 'blocked', 'no_authority', 'failed'))
);
CREATE INDEX ix_calc_mismatch_events_created ON calc_mismatch_events (created_at DESC);
CREATE INDEX ix_calc_mismatch_events_class ON calc_mismatch_events (suspected_class);
CREATE INDEX ix_calc_mismatch_events_correction ON calc_mismatch_events (correction_id);
