"""Results exports require acknowledgement and carry review instructions."""

import io
from pathlib import Path
import unittest

import pandas as pd
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from agent_ref_validator import core
from export_safeguards import (
    EXPORT_WARNING, MANUAL_CHECK, build_guarded_excel, clean_file_identifier,
    export_eligible, export_filename, results_signature,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def sample_results():
    labels = [
        core.SUFFICIENT_MATCH_LABEL,
        core.CITATION_DETAILS_LABEL,
        core.MAJOR_MISMATCH_LABEL,
        core.UNVERIFIED_LABEL,
        core.MANUAL_REVIEW_LABEL,
    ]
    return [{
        "Ref #": index,
        "Validation Result": label,
        "Score": 10 - index,
        "Reference": f"Example reference {index}",
        "Notes": f"Explanation {index}",
        "Domain": "",
        "AI URL flag": False,
        "Validator Version": core.VALIDATOR_VERSION,
        "Validator SHA256": core.VALIDATOR_SHA256,
    } for index, label in enumerate(labels, 1)]


class ExportSafeguardTests(unittest.TestCase):
    def test_identifier_and_acknowledgement_gate(self):
        data = pd.DataFrame(sample_results())
        for identifier in ("", "   ", "\t\n"):
            with self.subTest(identifier=identifier):
                self.assertFalse(export_eligible(identifier, True))
                with self.assertRaises(ValueError):
                    build_guarded_excel(data, file_identifier=identifier, acknowledged=True, include_review=False)
        self.assertFalse(export_eligible("OU-S390-042", False))
        with self.assertRaises(ValueError):
            build_guarded_excel(data, file_identifier="OU-S390-042", acknowledged=False,
                                include_review=False)
        for identifier in ("OU-S390-042", "2026-18", "Case_Á7"):
            with self.subTest(identifier=identifier):
                self.assertTrue(export_eligible(identifier, True))
        self.assertEqual(clean_file_identifier("  OU-S390-042  "), "OU-S390-042")
        self.assertEqual(export_filename("OU/S390 042", "2026-11-18"),
                         "validated_references_OU-S390-042_2026-11-18.xlsx")

    def test_workbook_keeps_results_and_adds_prominent_safeguards(self):
        contents = build_guarded_excel(
            pd.DataFrame(sample_results()), file_identifier="  OU-S390-042  ",
            acknowledged=True, include_review=False,
        )
        book = load_workbook(io.BytesIO(contents))
        self.assertEqual(book.sheetnames[:3], ["Read first", "Summary", "Results"])
        self.assertEqual(book["Read first"]["A3"].value, "File identifier")
        self.assertEqual(book["Read first"]["B3"].value, "OU-S390-042")
        self.assertEqual(book["Read first"]["A6"].value, EXPORT_WARNING)
        self.assertEqual(book["Results"].max_row, 6)
        for name in ("Results", "Issues"):
            sheet = book[name]
            headers = [cell.value for cell in sheet[1]]
            self.assertIn("Manual check status", headers)
            self.assertIn("File identifier", headers)
            self.assertIn("Important notice", headers)
            rows = list(sheet.values)[1:]
            status_index = headers.index("Manual check status")
            label_index = headers.index("Validation Result")
            for row in rows:
                expected = "" if row[label_index] == core.SUFFICIENT_MATCH_LABEL else MANUAL_CHECK
                self.assertEqual(row[status_index] or "", expected)
                self.assertEqual(row[headers.index("File identifier")], "OU-S390-042")
                self.assertEqual(row[headers.index("Important notice")], EXPORT_WARNING)
        self.assertEqual(book["Results"]["A2"].value, "1")
        self.assertEqual(book["Results"]["E2"].value, "False")
        self.assertTrue(book["Results"].conditional_formatting)

    def test_results_signature_changes_with_replaced_results(self):
        original = sample_results()
        changed = sample_results()
        changed[0]["Notes"] = "Rechecked"
        self.assertNotEqual(results_signature(original), results_signature(changed))

    def test_ui_resets_acknowledgement_after_export_and_result_change(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=40)
        app.secrets["LOGGING_ENABLED"] = False
        app.run()
        app.session_state["validation_results"] = sample_results()
        app.run()
        self.assertFalse(app.exception)
        download = lambda: next(button for button in app.get("download_button")
                                if button.label == "Download Excel report")
        self.assertTrue(download().disabled)

        app.text_input(key="file_identifier").set_value("  OU-S390-042  ").run()
        self.assertEqual(app.session_state["file_identifier"], "OU-S390-042")
        self.assertTrue(download().disabled)
        app.checkbox(key="export_acknowledged").check().run()
        self.assertFalse(download().disabled)
        download().click().run()
        self.assertFalse(app.session_state["export_acknowledged"])
        self.assertTrue(download().disabled)

        app.checkbox(key="export_acknowledged").check().run()
        changed = sample_results()
        changed[0]["Notes"] = "New analysis"
        app.session_state["validation_results"] = changed
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.session_state["export_acknowledged"])
        self.assertEqual(app.session_state["file_identifier"], "")


if __name__ == "__main__":
    unittest.main()
