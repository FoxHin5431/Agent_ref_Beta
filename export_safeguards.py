"""File identification and interpretation safeguards for Beta results exports."""

from __future__ import annotations

import hashlib
import io
import json
import re

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from agent_ref_validator.core import SUFFICIENT_MATCH_LABEL
from agent_ref_validator.exports import build_excel_workbook


EXPORT_WARNING = (
    "IMPORTANT: Agent Ref checks bibliographic information against retrieved records. "
    "Results requiring manual checking are not evidence of fabricated references or academic misconduct.\n\n"
    "Automated classifications must not be used alone as grounds for an academic conduct referral. "
    "The original reference and an appropriate source must be checked manually, and any concerns "
    "considered in the context of the wider student submission."
)
ACKNOWLEDGEMENT = (
    "I understand that Agent Ref identifies bibliographic discrepancies and does not establish "
    "academic misconduct. I acknowledge that all results other than 'Sufficient match' require "
    "manual verification before being used to support any academic conduct referral."
)
MANUAL_CHECK = "Manual check required"


def clean_file_identifier(value: str) -> str:
    """Accept a short, non-empty code supplied by the user for this file."""
    identifier = str(value or "").strip()
    if len(identifier) > 120 or not any(character.isalnum() for character in identifier):
        return ""
    if any(ord(character) < 32 for character in identifier):
        return ""
    return identifier


def export_eligible(file_identifier: str, acknowledged: bool) -> bool:
    return bool(clean_file_identifier(file_identifier) and acknowledged)


def require_export_eligibility(file_identifier: str, acknowledged: bool) -> str:
    identifier = clean_file_identifier(file_identifier)
    if not identifier or not acknowledged:
        raise ValueError("A file identifier and acknowledgement are required for every results export")
    return identifier


def results_signature(results: object) -> str:
    content = json.dumps(results, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def export_filename(file_identifier: str, day: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", clean_file_identifier(file_identifier)).strip("-")[:60]
    return f"validated_references_{safe or 'file'}_{day}.xlsx"


def manual_check_status(label: object) -> str:
    return "" if str(label or "") == SUFFICIENT_MATCH_LABEL else MANUAL_CHECK


def _annotate_excel(contents: bytes, file_identifier: str) -> bytes:
    workbook = load_workbook(io.BytesIO(contents))
    cover = workbook.create_sheet("Read first", 0)
    cover.sheet_view.showGridLines = False
    cover.column_dimensions["A"].width = 20
    cover.column_dimensions["B"].width = 86
    cover.merge_cells("A1:B1")
    cover["A1"] = "Agent Ref results — read before use"
    cover["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    cover["A1"].fill = PatternFill("solid", fgColor="067A8F")
    cover["A1"].alignment = Alignment(vertical="center")
    cover.row_dimensions[1].height = 34
    cover["A3"] = "File identifier"
    cover["A3"].font = Font(bold=True, color="034E5B")
    cover["B3"] = file_identifier
    cover["A5"] = "IMPORTANT"
    cover["A5"].font = Font(bold=True, color="8A5700")
    cover.merge_cells("A6:B10")
    cover["A6"] = EXPORT_WARNING
    cover["A6"].alignment = Alignment(wrap_text=True, vertical="top")
    cover["A6"].font = Font(color="102A2E")
    cover["A6"].fill = PatternFill("solid", fgColor="FFF3C4")
    cover.row_dimensions[6].height = 80
    cover["A12"] = "Interpretation"
    cover["A12"].font = Font(bold=True, color="034E5B")
    cover["B12"] = "Every result other than Sufficient match requires manual checking."
    cover["B12"].alignment = Alignment(wrap_text=True)

    for sheet_name in ("Results", "Issues"):
        sheet = workbook[sheet_name]
        headers = [cell.value for cell in sheet[1]]
        if "Validation Result" not in headers:
            continue
        category_column = headers.index("Validation Result") + 1
        first_new_column = sheet.max_column + 1
        for offset, heading in enumerate(("Manual check status", "File identifier", "Important notice")):
            cell = sheet.cell(1, first_new_column + offset, heading)
            cell.font = Font(bold=True, color="034E5B")
            cell.fill = PatternFill("solid", fgColor="EAF7F4")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for row in range(2, sheet.max_row + 1):
            category = sheet.cell(row, category_column).value
            sheet.cell(row, first_new_column, manual_check_status(category))
            sheet.cell(row, first_new_column + 1, file_identifier)
            notice = sheet.cell(row, first_new_column + 2, EXPORT_WARNING)
            notice.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.column_dimensions[sheet.cell(1, first_new_column).column_letter].width = 23
        sheet.column_dimensions[sheet.cell(1, first_new_column + 1).column_letter].width = 28
        sheet.column_dimensions[sheet.cell(1, first_new_column + 2).column_letter].width = 65
        if sheet.auto_filter.ref:
            sheet.auto_filter.ref = f"A1:{sheet.cell(sheet.max_row, first_new_column + 2).coordinate}"

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_guarded_excel(
    dataframe: pd.DataFrame,
    *,
    file_identifier: str,
    acknowledged: bool,
    include_review: bool,
    debug_mode: bool = False,
) -> bytes:
    """Only generate the full results workbook after the export gate passes."""
    identifier = require_export_eligibility(file_identifier, acknowledged)
    original = build_excel_workbook(
        dataframe, debug_mode=debug_mode, view="Full", include_review=include_review,
    )
    return _annotate_excel(original, identifier)
