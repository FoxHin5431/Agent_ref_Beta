from __future__ import annotations

import unittest
from unittest.mock import patch

from core_loader import load_beta_core


core = load_beta_core()

R024_REFERENCE = (
    "Guazzo, A. et al. (2025) ‘Desmoplakin Cardiomyopathy: Gene Dose-Dependent "
    "Myocardial Remodeling, Arrhythmias, and Premature Death.’, "
    "JACC. Clinical electrophysiology, pp. S2405-500X(25)00920-X. "
    "Available at: https://doi.org/10.1016/j.jacep.2025.10.031.\\"
)

R024_METADATA = {
    "DOI": "10.1016/j.jacep.2025.10.031",
    "title": ["Desmoplakin Cardiomyopathy"],
    "container-title": ["JACC: Clinical Electrophysiology"],
    "issued": {"date-parts": [[2026]]},
    "published-print": {"date-parts": [[2025]]},
    "author": [{"family": "Guazzo"}],
    "volume": "12",
    "issue": "4",
    "page": "747-764",
}


class FakeResponse:
    status_code = 200

    def __init__(self, message: dict):
        self.message = message

    def json(self) -> dict:
        return {"message": self.message}


class TitleExtractionTests(unittest.TestCase):
    def test_single_and_double_quote_styles_extract_the_same_title(self) -> None:
        title = "A sufficiently distinctive example article title"
        quote_pairs = [
            ("'", "'"),
            ("‘", "’"),
            ('"', '"'),
            ("“", "”"),
            ("'", "’"),
            ("‘", "'"),
            ('"', "”"),
            ("“", '"'),
        ]

        for opening, closing in quote_pairs:
            with self.subTest(opening=opening, closing=closing):
                reference = f"Example, A. (2020) {opening}{title}{closing}, Journal of Examples."
                self.assertEqual(core.extract_title(reference), title)


class TitleBackValidationTests(unittest.TestCase):
    def test_metadata_main_title_can_match_student_title_with_subtitle(self) -> None:
        extracted = (
            "Desmoplakin Cardiomyopathy: Gene Dose-Dependent Myocardial "
            "Remodeling, Arrhythmias, and Premature Death."
        )

        ok, direct, method, confidence = core.evaluate_title_match(
            extracted,
            "Desmoplakin Cardiomyopathy",
            R024_REFERENCE,
        )

        self.assertTrue(ok)
        self.assertLess(direct, 0.75)
        self.assertEqual(method, "metadata title found in extracted title")
        self.assertEqual(confidence, 1.0)

    def test_full_reference_fallback_recovers_from_bad_regex_extraction(self) -> None:
        metadata_title = "A complete and distinctive metadata article title"
        full_reference = (
            "Example, A. (2020) badly parsed punctuation "
            f"{metadata_title}, Journal of Examples, 12(3), pp. 10-20."
        )

        ok, _direct, method, confidence = core.evaluate_title_match(
            "badly parsed punctuation",
            metadata_title,
            full_reference,
        )

        self.assertTrue(ok)
        self.assertEqual(method, "metadata title found in full reference")
        self.assertEqual(confidence, 1.0)

    def test_short_generic_metadata_title_cannot_pass_by_containment(self) -> None:
        ok, _direct, method, _confidence = core.evaluate_title_match(
            "incorrect extracted text",
            "Annual report",
            "Example Organisation (2020) Annual report. Example Publisher.",
        )

        self.assertFalse(ok)
        self.assertEqual(method, "no title match")


class TitleBackValidationPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_r024_short_crossref_title_is_back_validated(self) -> None:
        async def fetch_metadata(_client, _doi):
            return FakeResponse(R024_METADATA)

        async def doi_resolves(_client, _doi):
            return True

        with (
            patch.object(core, "fetch_crossref_doi", fetch_metadata),
            patch.object(core, "check_doi_org_head", doi_resolves),
        ):
            result = await core.validate_single_ref(None, R024_REFERENCE)

        self.assertEqual(result["Validation Result"], "✅ Real")
        self.assertTrue(result["title_ok"])
        self.assertLess(result["title_similarity"], 0.75)
        self.assertEqual(
            result["title_match_method"],
            "metadata title found in extracted title",
        )
        self.assertEqual(result["title_match_confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
