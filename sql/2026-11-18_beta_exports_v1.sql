-- Apply to the same Neon database as agent_ref_reference_checks_v2 before enabling Beta logging.
-- A row records a download click, not proof that the browser finished saving a file.
CREATE TABLE IF NOT EXISTS agent_ref_beta_exports_v1 (
    export_id uuid PRIMARY KEY,
    requested_at timestamptz NOT NULL DEFAULT now(),
    check_run_id uuid REFERENCES agent_ref_reference_checks_v2(run_id) ON DELETE SET NULL,
    file_identifier text NOT NULL,
    report_filename text NOT NULL,
    report_sha256 text NOT NULL,
    reference_count integer NOT NULL,
    result_counts jsonb NOT NULL,
    validator_version text NOT NULL
);

CREATE INDEX IF NOT EXISTS agent_ref_beta_exports_v1_requested_at_idx
    ON agent_ref_beta_exports_v1 (requested_at DESC);
CREATE INDEX IF NOT EXISTS agent_ref_beta_exports_v1_file_identifier_idx
    ON agent_ref_beta_exports_v1 (file_identifier);
