from __future__ import annotations

import unittest
from unittest.mock import patch

from core_loader import load_beta_core


core = load_beta_core()


STERN_REFERENCE = (
    "Stern, Claudio D, and Agnieszka M Piatkowska. "
    "'Multiple roles of timing in somite formation.' "
    "Seminars in cell & developmental biology vol. 42 (2015a): 134-9. "
    "doi:10.1016/j.semcdb.2015.06.002"
)

STERN_METADATA = {
    "DOI": "10.1016/j.semcdb.2015.06.002",
    "title": ["Multiple roles of timing in somite formation"],
    "container-title": ["Seminars in Cell & Developmental Biology"],
    "issued": {"date-parts": [[2015]]},
    "author": [{"family": "Stern"}, {"family": "Piatkowska"}],
    "volume": "42",
    "page": "134-139",
}


class FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {"message": STERN_METADATA}


class FullAuthorExtractionTests(unittest.TestCase):
    def test_harvard_given_first_and_vancouver_styles(self) -> None:
        cases = [
            (
                "Paisley, M.F., Trigg, D.J. and Walley, W.J. (2014) 'Revision'.",
                ["Paisley", "Trigg", "Walley"],
            ),
            (STERN_REFERENCE, ["Stern", "Piatkowska"]),
            (
                "Bekaii-Saab TS, Yaeger R, Spira AI, et al. Adagrasib in Advanced "
                "Solid Tumors. J Clin Oncol. 2023;41(25):4097-4106.",
                ["Bekaii-Saab", "Yaeger", "Spira"],
            ),
            (
                "Nijs, J., Van de Velde, B. and De Meirleir, K. (2008) 'Example'.",
                ["Nijs", "Van de Velde", "De Meirleir"],
            ),
            (
                "Kim, J., Kim, H. and Bang, D. (2025) 'OpenIDS2'.",
                ["Kim", "Kim", "Bang"],
            ),
        ]

        for reference, expected in cases:
            with self.subTest(reference=reference):
                self.assertEqual(core.extract_author_surnames(reference), expected)

    def test_article_locator_counts_as_bibliographic_shape(self) -> None:
        reference = (
            "Dagar, M. et al. (2025) 'Individual Botanicals Perform Better'. "
            "Alternative Therapies in Health and Medicine, p. AT11732."
        )

        self.assertTrue(core.has_bibliographic_shape(reference))

    def test_parenthesized_year_after_volume_is_not_an_issue(self) -> None:
        volume, issue, pages, page_start, page_end = core.extract_vol_issue_pages(
            STERN_REFERENCE
        )

        self.assertEqual(volume, "42")
        self.assertEqual(issue, "")
        self.assertEqual(pages, "134-9")
        self.assertEqual((page_start, page_end), ("134", "9"))

    def test_organisational_author_is_preserved(self) -> None:
        cases = [
            ("The Open University (2025a) '1.2 What are clouds?'.", ["The Open University"]),
            ("NHS Digital. Example dataset. https://digital.nhs.uk/example", ["NHS Digital"]),
            (
                "Centers for Disease Control and Prevention. Example report. https://cdc.gov/example",
                ["Centers for Disease Control and Prevention"],
            ),
        ]

        for reference, expected in cases:
            with self.subTest(reference=reference):
                self.assertEqual(core.extract_author_surnames(reference), expected)


class FullAuthorComparisonTests(unittest.TestCase):
    def test_complete_list_requires_broad_coverage(self) -> None:
        comparison = core.compare_author_lists(
            ["Paisley", "Trigg", "Walley"],
            ["Paisley", "Trigg", "Walley"],
        )
        wrong_middle = core.compare_author_lists(
            ["Paisley", "Invented", "Walley"],
            ["Paisley", "Trigg", "Walley"],
        )

        self.assertTrue(comparison["ok"])
        self.assertEqual(comparison["matched_count"], 3)
        self.assertFalse(wrong_middle["ok"])
        self.assertEqual(wrong_middle["matched_count"], 2)

    def test_et_al_checks_every_explicitly_named_author(self) -> None:
        metadata = ["Bekaii-Saab", "Yaeger", "Spira", "Johnson", "Lee"]

        accepted = core.compare_author_lists(
            ["Bekaii-Saab", "Yaeger", "Spira"],
            metadata,
            uses_et_al=True,
        )
        rejected = core.compare_author_lists(
            ["Bekaii-Saab", "Invented", "Spira"],
            metadata,
            uses_et_al=True,
        )

        self.assertTrue(accepted["ok"])
        self.assertEqual(accepted["method"], "et al. named-author prefix")
        self.assertFalse(rejected["ok"])

    def test_omitted_authors_without_et_al_do_not_count_as_full_match(self) -> None:
        comparison = core.compare_author_lists(
            ["Stern"],
            ["Stern", "Piatkowska"],
        )

        self.assertFalse(comparison["ok"])
        self.assertTrue(comparison["first_author_ok"])

    def test_pubmed_names_are_converted_to_surnames(self) -> None:
        self.assertEqual(
            core.metadata_author_surnames(
                [{"name": "Stern CD"}, {"name": "Piatkowska AM"}]
            ),
            ["Stern", "Piatkowska"],
        )

    def test_collective_credits_do_not_look_like_omitted_people(self) -> None:
        self.assertEqual(
            core.metadata_author_surnames(
                [
                    {"family": "Clarke"},
                    {"family": "on behalf of the PLOS Biology Staff Editors"},
                ]
            ),
            ["Clarke"],
        )


class FullAuthorPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def validate(self, reference: str) -> dict:
        async def fetch_crossref(_client, _doi):
            return FakeResponse()

        async def doi_resolves(_client, _doi):
            return True

        with (
            patch.object(core, "fetch_crossref_doi", fetch_crossref),
            patch.object(core, "check_doi_org_head", doi_resolves),
        ):
            return await core.validate_single_ref(None, reference)

    async def test_complete_author_list_passes_and_is_reported(self) -> None:
        result = await self.validate(STERN_REFERENCE)

        self.assertTrue(result["author_ok"])
        self.assertEqual(result["Extracted Authors"], "Stern, Piatkowska")
        self.assertEqual(result["Metadata Authors"], "Stern, Piatkowska")
        self.assertEqual(result["Author match method"], "full author list")
        self.assertEqual(result["Author match count"], 2)
        self.assertEqual(result["Validation Result"], "✅ Real")

    async def test_wrong_coauthor_prevents_real_even_when_first_author_matches(self) -> None:
        corrupted = STERN_REFERENCE.replace("Piatkowska", "Invented")

        result = await self.validate(corrupted)

        self.assertFalse(result["author_ok"])
        self.assertTrue(result["First author matches"])
        self.assertEqual(result["Author match count"], 1)
        self.assertEqual(result["Validation Result"], "⚠ Suspicious")
        self.assertIn("author list conflicts", result["Notes"])


if __name__ == "__main__":
    unittest.main()
