"""Beta shares pilot check logging and records export download requests."""

from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from streamlit.testing.v1 import AppTest

from beta_logging import configured_db_url, log_export_request


REPO = Path(__file__).resolve().parents[1]


class BetaLoggingTests(unittest.TestCase):
    def test_logging_only_uses_a_configured_url(self):
        self.assertEqual(configured_db_url({}, {}), "")
        self.assertEqual(configured_db_url({"DB_URL": "postgresql://fixture"}, {}),
                         "postgresql://fixture")
        self.assertEqual(configured_db_url({"DB_URL": "postgresql://fixture",
                                             "LOGGING_ENABLED": False}, {}), "")
        self.assertEqual(configured_db_url({}, {"DATABASE_URL": "postgresql://fixture"}),
                         "postgresql://fixture")

    @patch("psycopg2.connect")
    def test_export_request_logs_identifier_and_summary_without_reference_text(self, connect):
        findings = [{"Validation Result": "✅ Sufficient match", "Reference": "Private citation"},
                    {"Validation Result": "🟠 Citation details need checking", "Reference": "Another citation"}]
        export_id = log_export_request(
            "postgresql://fixture", file_identifier="  FILE-42  ",
            run_id=None, findings=findings, validator_version="test-version",
            filename="report.xlsx", report_sha256="a" * 64,
        )
        self.assertTrue(export_id)
        connect.assert_called_once_with("postgresql://fixture", connect_timeout=10)
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        sql, values = cursor.execute.call_args.args
        self.assertIn("INSERT INTO agent_ref_beta_exports_v1", sql)
        self.assertEqual(values[2], "FILE-42")
        self.assertEqual(values[5], 2)
        self.assertEqual(values[6].adapted["✅ Sufficient match"], 1)
        self.assertNotIn("Private citation", str(values))
        self.assertTrue((REPO / "sql" / "2026-11-18_beta_exports_v1.sql").is_file())

    def test_app_links_check_and_export_request_logs(self):
        app = AppTest.from_file(str(REPO / "app.py"), default_timeout=40)
        app.secrets["DB_URL"] = "postgresql://fixture"
        app.secrets["LOGGING_ENABLED"] = True
        app.run()
        app.selectbox(key="module_choice").set_value("S826").run()
        reference = "Example, A. (2020) 'A sufficiently distinctive reference title', Journal of Examples."
        app.text_area(key="ref_input").set_value(reference).run()
        finding = {
            "Reference": reference, "Validation Result": "🟡 Could not verify",
            "Score": 0, "Notes": "Lookup unavailable", "Domain": "",
        }
        with patch("agent_ref_validator.core.validate_single_ref", new=AsyncMock(return_value=finding)), \
             patch("agent_ref_validator.run_records.save_check", return_value="00000000-0000-0000-0000-000000000042") as save:
            next(button for button in app.button if button.label == "Check references").click().run()
        self.assertFalse(app.exception)
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["app_name"], "Agent_ref_Beta")
        self.assertEqual(app.session_state["logged_run_id"], "00000000-0000-0000-0000-000000000042")

        app.text_input(key="file_identifier").set_value("FILE-42").run()
        with patch("beta_logging.log_export_request", return_value="export-id") as log:
            app.checkbox(key="export_acknowledged").check().run()
            download = next(item for item in app.get("download_button")
                            if item.label == "Download Excel report")
            self.assertFalse(download.disabled)
            download.click().run()
        self.assertFalse(app.exception)
        log.assert_called_once()
        self.assertEqual(log.call_args.kwargs["file_identifier"], "FILE-42")
        self.assertEqual(log.call_args.kwargs["run_id"], "00000000-0000-0000-0000-000000000042")
        self.assertFalse(app.session_state["export_acknowledged"])


if __name__ == "__main__":
    unittest.main()
