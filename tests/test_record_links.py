"""Retrieved record links must refer to evidence, not an unverified submitted DOI."""
import ast
from pathlib import Path
import unittest
import urllib.parse

import pandas as pd


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "retrieved_record_link")
namespace = {"urllib": urllib}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(APP_PATH), "exec"), namespace)
retrieved_record_link = namespace["retrieved_record_link"]


class RecordLinkTests(unittest.TestCase):
    def test_links_to_the_record_used_for_comparison(self):
        cases = [
            ({"Metadata Source": "Crossref", "Metadata DOI": "10.1234/real"},
             "https://doi.org/10.1234/real"),
            ({"Metadata Source": "PubMed/PMC", "PubMed ID": "12345"},
             "https://pubmed.ncbi.nlm.nih.gov/12345/"),
            ({"Metadata Source": "arXiv", "Metadata Record URL": "https://arxiv.org/abs/2401.00001"},
             "https://arxiv.org/abs/2401.00001"),
            ({"Extracted DOI": "10.1234/unverified", "Metadata Source": ""}, ""),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(retrieved_record_link(pd.Series(row)), expected)

    def test_trusted_source_prompt_follows_notes(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertLess(source.index('st.info(validator_notes'),
                        source.index('st.markdown("**Check trusted source here:**")'))


if __name__ == "__main__":
    unittest.main()
