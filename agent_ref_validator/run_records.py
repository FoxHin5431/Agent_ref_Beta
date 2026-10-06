"""Module selection and isolated Neon records for reference checks."""

from __future__ import annotations

import json
import uuid

MODULES = ("S826", "SD816", "S390", "S350", "SXH890", "Other")
S390_CHOICES = ("SXB", "SXH", "SXC", "SXG", "SXP", "SXE", "SXBM")
TABLE_NAME = "agent_ref_reference_checks_v2"


def module_details(module: str | None, s390_choice: str | None = None,
                   other_module: str | None = None) -> dict[str, str] | None:
    """Return a complete, valid choice, or None until the user fills it in."""
    if module not in MODULES:
        return None
    if module == "S390" and s390_choice not in S390_CHOICES:
        return None
    other = (other_module or "").strip()
    if module == "Other" and not other:
        return None
    return {"module": module, "s390_choice": s390_choice if module == "S390" else "",
            "other_module": other if module == "Other" else ""}


def save_check(db_url: str, *, app_name: str, module: dict[str, str],
               input_text: str, split_refs: list[str], findings: list[dict],
               validator_version: str, check_reviews: bool = False,
               error_text: str | None = None) -> str:
    """Store one check in the pre-created table. Return its run ID."""
    if not db_url:
        raise ValueError("Database URL is required")
    validated = module_details(module.get("module"), module.get("s390_choice"),
                               module.get("other_module"))
    if validated is None:
        raise ValueError("A complete module choice is required")
    import psycopg2
    from psycopg2.extras import Json

    counts: dict[str, int] = {}
    for finding in findings:
        label = str(finding.get("Validation Result", "Unknown"))
        counts[label] = counts.get(label, 0) + 1
    run_id = str(uuid.uuid4())
    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agent_ref_reference_checks_v2
                (run_id, app_name, module, s390_choice, other_module, input_text,
                 split_refs, findings, result_counts, validator_version, check_reviews, error_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (run_id, app_name, validated["module"], validated["s390_choice"] or None,
                  validated["other_module"] or None, input_text, Json(split_refs),
                  Json(json.loads(json.dumps(findings, default=str))), Json(counts),
                  validator_version, bool(check_reviews), error_text))
    return run_id
