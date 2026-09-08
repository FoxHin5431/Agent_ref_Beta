from __future__ import annotations

import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class UiLinkTests(unittest.TestCase):
    def test_regex_inspector_link_is_present(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")

        self.assertIn('"Open regex inspector"', source)
        self.assertIn(
            "https://agent-ref-regex-inspector.streamlit.app/",
            source,
        )

    def test_redesigned_page_structure_is_present(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")

        self.assertIn('initial_sidebar_state="expanded"', source)
        self.assertIn("Detect reviews: {'on'", source)
        self.assertIn(
            "Review detection will slow down the response time.", source
        )
        self.assertIn('page_choice = st.radio(', source)
        self.assertIn('if page_choice != "Check references":', source)
        self.assertIn(
            "Validate a reference list against academic databases.", source
        )
        self.assertIn('"Part by part"', source)
        self.assertIn('"Technical details"', source)
        self.assertIn('**Validator notes**', source)
        self.assertIn('st.column_config.NumberColumn(', source)
        self.assertIn(
            'st.columns([1.7, 1.7, 4.6]', source
        )
        self.assertNotIn('disabled=not bool(user_input.strip())', source)
        self.assertIn('height: 2.75rem;', source)
        self.assertIn('key="summary_filters"', source)
        self.assertIn('st.session_state["result_filter"]', source)
        self.assertIn('options=filtered_indices', source)
        self.assertIn(
            "Select a summary card to filter the references below.",
            source,
        )
        self.assertIn('if active_filter == "review":', source)
        self.assertIn('visible_part_df = part_df[["Part", "Status"]].copy()', source)
        self.assertIn('selected.get("Source URL")', source)
        self.assertIn('st.column_config.LinkColumn(', source)
        self.assertIn('df.iloc[filtered_indices][overview_columns]', source)
        self.assertIn('["Excel", "CSV"]', source)
        self.assertIn('["Condensed", "Full"]', source)


if __name__ == "__main__":
    unittest.main()
