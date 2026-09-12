"""Exercise the actual Beta controls and exported physics provenance."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agent_ref_validator import core
from agent_ref_validator.exports import build_excel_workbook


class PhysicsUITests(unittest.TestCase):
    def test_load_run_and_export_twenty_examples(self):
        import pandas as pd
        from openpyxl import load_workbook
        from streamlit.testing.v1 import AppTest
        folder = REPO / "examples" / "physics"
        pack = json.loads((folder / "arxiv-20-metadata.json").read_text(encoding="utf-8"))
        records = [row["metadata"] for row in pack["records"]]
        app = AppTest.from_file(str(REPO / "app.py"), default_timeout=40)
        app.secrets["LOGGING_ENABLED"] = False
        app.secrets["ADS_API_TOKEN"] = "fixture-token"
        app.run()
        self.assertFalse(app.exception)
        app.button(key="load_arxiv_examples").click().run()
        self.assertEqual(len(core.split_references(app.text_area(key="ref_input").value)), 20)
        with patch.object(core, "fetch_arxiv_records", AsyncMock(return_value=(records, ""))), \
             patch.object(core, "check_doi_org_head", AsyncMock(return_value=True)):
            next(button for button in app.button if button.label == "Check references").click().run()
        self.assertFalse(app.exception, [error.message for error in app.exception])
        results = app.session_state["validation_results"]
        self.assertEqual(len(results), 20)
        self.assertTrue(all(row["Validation Result"] == "✅ Real" for row in results))
        self.assertTrue(all(row["Metadata Source"] == "arXiv" for row in results))
        self.assertTrue(any("NASA ADS: configured" in c.value for c in app.caption))
        workbook = load_workbook(io.BytesIO(build_excel_workbook(pd.DataFrame(results))))
        headers = [c.value for c in workbook["Results"][1]]
        self.assertIn("Metadata Source", headers)
        self.assertIn("arXiv ID", headers)


if __name__ == "__main__":
    unittest.main()
