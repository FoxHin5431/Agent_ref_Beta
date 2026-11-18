"""Beta-only Neon export events; check runs use the shared pilot logger."""

from __future__ import annotations

import uuid

from export_safeguards import clean_file_identifier


EXPORT_TABLE = "agent_ref_beta_exports_v1"


def configured_db_url(secrets, environ) -> str:
    """Use the pilot's DB_URL convention, with an optional explicit off switch."""
    try:
        url = str(secrets.get("DB_URL", "") or environ.get("DB_URL", "")
                  or environ.get("DATABASE_URL", "")).strip()
        enabled = secrets.get("LOGGING_ENABLED", None)
    except Exception:
        url = str(environ.get("DB_URL", "") or environ.get("DATABASE_URL", "")).strip()
        enabled = None
    if enabled is not None and str(enabled).strip().lower() in {"false", "0", "off", "no"}:
        return ""
    return url


def result_counts(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        label = str(finding.get("Validation Result") or "Unknown")
        counts[label] = counts.get(label, 0) + 1
    return counts


def log_export_request(
    db_url: str, *, file_identifier: str, run_id: str | None,
    findings: list[dict], validator_version: str,
    filename: str, report_sha256: str,
) -> str:
    """Record a download click; the browser cannot confirm a completed save."""
    identifier = clean_file_identifier(file_identifier)
    if not db_url or not identifier:
        raise ValueError("A database URL and file identifier are required")
    if not report_sha256 or len(report_sha256) != 64:
        raise ValueError("A report SHA256 is required")
    import psycopg2
    from psycopg2.extras import Json

    export_id = str(uuid.uuid4())
    with psycopg2.connect(db_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_ref_beta_exports_v1
                (export_id, check_run_id, file_identifier, report_filename,
                 report_sha256, reference_count, result_counts, validator_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (export_id, run_id, identifier, filename, report_sha256,
                 len(findings), Json(result_counts(findings)), validator_version),
            )
    return export_id
