from __future__ import annotations

import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class UiLinkTests(unittest.TestCase):
    def test_regex_inspector_link_is_present(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")

        self.assertIn('st.expander("Reference debugging tool")', source)
        self.assertIn(
            "https://agent-ref-regex-inspector.streamlit.app/",
            source,
        )


if __name__ == "__main__":
    unittest.main()
