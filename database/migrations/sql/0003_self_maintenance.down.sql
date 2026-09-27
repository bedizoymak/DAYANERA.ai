-- =====================================================================
-- DAYANERA.ai - revert 0003_self_maintenance
-- Drops the mismatch events and the correction memory. Calculations (with
-- their stored Qwen drafts and comparisons) and the append-only audit log,
-- which records every lifecycle transition, are preserved.
-- =====================================================================
DROP TABLE IF EXISTS calc_mismatch_events;
DROP TABLE IF EXISTS engineering_corrections;
