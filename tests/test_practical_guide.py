"""The Associate Lecturer guidance is reachable and renders its review advice."""
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class PracticalGuideTests(unittest.TestCase):
    def test_sidebar_navigation_and_guidance(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=40)
        app.secrets["LOGGING_ENABLED"] = False
        app.run()
        self.assertFalse(app.exception)

        app.radio(key="page_choice").set_value("What do I do if…?").run()
        self.assertFalse(app.exception)
        self.assertTrue(any(header.value == "What do I do if…?" for header in app.header))
        self.assertEqual(len(app.expander), 9)  # Eight questions plus sidebar Advanced.
        questions = [item.label for item in app.expander]
        self.assertTrue(any("Citation details need checking" in text for text in questions))
        self.assertTrue(any("Major mismatch" in text for text in questions))
        self.assertTrue(any("Could not verify" in text for text in questions))
        warning = " ".join(item.value for item in app.warning)
        self.assertIn("Do not use these labels alone as grounds", warning)
        info = " ".join(item.value for item in app.info)
        self.assertIn("Sufficient match require manual checking", info)
        answers = " ".join(item.value for item in app.markdown)
        self.assertIn("Manual review — web source", answers)
        self.assertIn("Automated classifications alone must never be used", answers)

        app.radio(key="page_choice").set_value("Check references").run()
        app.session_state["validation_results"] = [{
            "Ref #": 1,
            "Reference": "Example, A. (2020) Example title.",
            "Validation Result": "🟡 Could not verify",
            "Score": 0,
            "Notes": "No matching record was found.",
            "Domain": "",
        }]
        app.run()
        self.assertFalse(app.exception)
        app.button(key="result_guidance").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["page_choice"], "What do I do if…?")


if __name__ == "__main__":
    unittest.main()
