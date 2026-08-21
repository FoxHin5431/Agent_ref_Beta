from __future__ import annotations

import unittest
from unittest.mock import patch

from core_loader import load_beta_core


core = load_beta_core()

OU_REFERENCES = [
    (
        "The Open University (2025a) '1.2 What are clouds?'. "
        "S111: Questions in science. Available at: "
        "https://learn2.open.ac.uk/mod/oucontent/view.php?id=2409631&section=3 "
        "(Accessed: 19 August 2025)."
    ),
    (
        "The Open University (2025b) '1.3.1 Snow and ice'. "
        "S111: Questions in science. Available at: "
        "https://learn2.open.ac.uk/mod/oucontent/view.php?id=2409631&section=4.1 "
        "(Accessed: 19 August 2025)."
    ),
]

STERN_CASES = [
    (
        "Stern, Claudio D, and Agnieszka M Piatkowska. "
        "'Multiple roles of timing in somite formation.' "
        "Seminars in cell & developmental biology vol. 42 (2015a): 134-9. "
        "doi:10.1016/j.semcdb.2015.06.002",
        13,
        {
            "DOI": "10.1016/j.semcdb.2015.06.002",
            "title": ["Multiple roles of timing in somite formation"],
            "container-title": ["Seminars in Cell &amp; Developmental Biology"],
            "issued": {"date-parts": [[2015]]},
            "created": {"date-parts": [[2015]]},
            "author": [{"family": "Stern"}, {"family": "Piatkowska"}],
            "volume": "42",
            "page": "134-139",
        },
    ),
    (
        "Stern, Claudio. 'The embryo reunited with its membranes in Göttingen.' "
        "Development (Cambridge, England) vol. 142,16 (2015b): 2727-9. "
        "doi:10.1242/dev.124719",
        12,
        {
            "DOI": "10.1242/dev.124719",
            "title": ["The embryo reunited with its membranes in Göttingen"],
            "container-title": ["Development"],
            "issued": {"date-parts": [[2015]]},
            "created": {"date-parts": [[2015]]},
            "author": [{"family": "Stern"}],
            "volume": "142",
            "issue": "16",
            "page": "2727-2729",
        },
    ),
]


class FakeResponse:
    def __init__(self, message: dict):
        self.status_code = 200
        self._message = message

    def json(self) -> dict:
        return {"message": self._message}


class RawJsonResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class YearParsingTests(unittest.TestCase):
    def test_extracts_year_token_and_numeric_value(self) -> None:
        cases = {
            "(2025)": ("2025", 2025, ""),
            "(2025a)": ("2025a", 2025, "a"),
            "(2025b)": ("2025b", 2025, "b"),
            "(2025A)": ("2025A", 2025, "a"),
        }

        for text, expected in cases.items():
            with self.subTest(text=text):
                token = core.extract_year(text)
                number, suffix = core.parse_year_token(token)
                self.assertEqual((token, number, suffix), expected)

    def test_crossref_candidate_chooser_accepts_suffixed_year(self) -> None:
        item = {
            "title": ["Multiple roles of timing in somite formation"],
            "container-title": ["Seminars in Cell & Developmental Biology"],
            "issued": {"date-parts": [[2015]]},
            "author": [{"family": "Stern"}],
        }

        best, reason = core.choose_best_crossref_item(
            STERN_CASES[0][0],
            [item],
            "Stern",
            "2015a",
            "Seminars in Cell & Developmental Biology",
            "Multiple roles of timing in somite formation",
        )

        self.assertIs(best, item)
        self.assertIn("year_ok=True", reason)


class YearSuffixPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_ou_material_is_genuine_but_not_externally_validated_as_real(self) -> None:
        async def no_crossref_matches(*_args, **_kwargs):
            return FakeResponse({"items": []})

        for reference in OU_REFERENCES:
            with self.subTest(reference=reference):
                with (
                    patch.object(core, "fetch_crossref_bib", no_crossref_matches),
                    patch.object(core, "fetch_crossref_fielded", no_crossref_matches),
                ):
                    result = await core.validate_single_ref(None, reference)

                self.assertNotIn("Real", result["Validation Result"])
                self.assertNotIn("lookup error", result["Notes"].lower())
                self.assertIn("no crossref items", result["Notes"].lower())
                self.assertEqual(core.parse_year_token(core.extract_year(reference))[0], 2025)

    async def test_explicit_doi_references_keep_expected_scores(self) -> None:
        async def doi_resolves(_client, _doi):
            return True

        for reference, expected_score, metadata in STERN_CASES:
            async def fetch_metadata(_client, _doi, item=metadata):
                return FakeResponse(item)

            with self.subTest(reference=reference):
                with (
                    patch.object(core, "fetch_crossref_doi", fetch_metadata),
                    patch.object(core, "check_doi_org_head", doi_resolves),
                ):
                    result = await core.validate_single_ref(None, reference)

                self.assertEqual(result["Validation Result"], "✅ Real")
                self.assertEqual(result["Score"], expected_score)
                self.assertTrue(result["year_ok"])
                self.assertNotIn("lookup error", result["Notes"].lower())

    async def test_double_quoted_titles_keep_expected_scores(self) -> None:
        async def doi_resolves(_client, _doi):
            return True

        for reference, expected_score, metadata in STERN_CASES:
            double_quoted_reference = reference.replace("'", '"')

            async def fetch_metadata(_client, _doi, item=metadata):
                return FakeResponse(item)

            with self.subTest(reference=double_quoted_reference):
                with (
                    patch.object(core, "fetch_crossref_doi", fetch_metadata),
                    patch.object(core, "check_doi_org_head", doi_resolves),
                ):
                    result = await core.validate_single_ref(None, double_quoted_reference)

                self.assertEqual(result["Validation Result"], "✅ Real")
                self.assertEqual(result["Score"], expected_score)
                self.assertTrue(result["title_ok"])

    async def test_pubmed_compares_suffixed_year_numerically(self) -> None:
        reference = "Stern, Claudio. (2015a). Example article. PMID: 123456"

        async def fetch_pubmed(_client, _pmid):
            return RawJsonResponse(
                {
                    "result": {
                        "123456": {
                            "fulljournalname": "Development",
                            "pubdate": "2015 Aug",
                            "authors": [{"name": "Stern C"}],
                        }
                    }
                }
            )

        with patch.object(core, "fetch_pubmed", fetch_pubmed):
            result = await core.validate_single_ref(None, reference)

        self.assertTrue(result["year_ok"])
        self.assertNotIn("lookup error", result["Notes"].lower())

    async def test_pmc_compares_suffixed_year_numerically(self) -> None:
        reference = "Stern, Claudio. (2015b). Example article. PMCID: PMC123456"

        async def fetch_pmc(_client, _pmcid):
            return RawJsonResponse(
                {
                    "result": {
                        "uids": ["PMC123456"],
                        "PMC123456": {
                            "fulljournalname": "Development",
                            "pubdate": "2015 Aug",
                            "authors": [{"name": "Stern C"}],
                            "articleids": [],
                        },
                    }
                }
            )

        with patch.object(core, "fetch_pmc", fetch_pmc):
            result = await core.validate_single_ref(None, reference)

        self.assertTrue(result["year_ok"])
        self.assertNotIn("lookup error", result["Notes"].lower())


if __name__ == "__main__":
    unittest.main()
