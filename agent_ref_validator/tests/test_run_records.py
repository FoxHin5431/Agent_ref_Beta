"""Check module validation and isolated, complete database writes."""

import unittest
from unittest.mock import MagicMock, patch

from agent_ref_validator.run_records import module_details, save_check


class RunRecordTests(unittest.TestCase):
    def test_required_choices(self):
        self.assertIsNone(module_details(None))
        self.assertIsNone(module_details("S390"))
        self.assertIsNone(module_details("Other", other_module=" "))
        self.assertEqual(module_details("S390", "SXB")["s390_choice"], "SXB")

    @patch("psycopg2.connect")
    def test_new_table_receives_references_findings_and_summary(self, connect):
        cursor = MagicMock()
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        run_id = save_check(
            "postgresql://fixture", app_name="pilot",
            module=module_details("S390", "SXB"), input_text="Reference A",
            split_refs=["Reference A"], findings=[{"Validation Result": "✅ Real", "Reference": "Reference A"}],
            validator_version="test",
        )
        self.assertTrue(run_id)
        self.assertEqual(cursor.execute.call_count, 1)
        insert_sql, values = cursor.execute.call_args.args
        self.assertIn("INSERT INTO agent_ref_reference_checks_v2", insert_sql)
        self.assertEqual(values[2:4], ("S390", "SXB"))
        self.assertEqual(values[5], "Reference A")
        self.assertEqual(values[6].adapted, ["Reference A"])
        self.assertEqual(values[7].adapted[0]["Validation Result"], "✅ Real")
        self.assertEqual(values[8].adapted, {"✅ Real": 1})


if __name__ == "__main__":
    unittest.main()
