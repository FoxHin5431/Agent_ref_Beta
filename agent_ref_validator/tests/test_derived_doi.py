from __future__ import annotations

import unittest
from unittest.mock import patch

from core_loader import load_beta_core


core = load_beta_core()

PAISLEY_REFERENCE = (
    "Paisley, M.F., Trigg, D.J. and Walley, W.J. (2014) "
    "‘Revision of the Biological Monitoring Working Party (BMWP) score system: "
    "derivation of present-only and abundance-related scores from field data’, "
    "Hydrobiologia, 722(1), pp. 61–70."
)

PAISLEY_WRONG_JOURNAL_CANDIDATE = {
    "DOI": "10.9999/paisley-derived-candidate",
    "title": [
        "Revision of the Biological Monitoring Working Party (BMWP) score system: "
        "derivation of present-only and abundance-related scores from field data"
    ],
    "container-title": ["Different Journal"],
    "issued": {"date-parts": [[2014]]},
    "author": [
        {"family": "Paisley"},
        {"family": "Trigg"},
        {"family": "Walley"},
    ],
    "volume": "722",
    "issue": "1",
    "page": "61-70",
}

PAISLEY_REAL_WORK_CANDIDATE = {
    "DOI": "10.1002/rra.2686",
    "title": [
        "Revision of the Biological Monitoring Working Party (BMWP) score system: "
        "derivation of present-only and abundance-related scores from field data"
    ],
    "container-title": ["River Research and Applications"],
    # Crossref records the online date as 2013; the submitted 2014 date is
    # within the validator tolerance and is also the publication's print year.
    "issued": {"date-parts": [[2013, 7, 30]]},
    "published-print": {"date-parts": [[2014, 9]]},
    "author": [
        {"family": "Paisley"},
        {"family": "Trigg"},
        {"family": "Walley"},
    ],
    "volume": "30",
    "issue": "7",
    "page": "887-904",
}

VALID_DOI_LESS_REFERENCE = (
    "Example, A. (2020) ‘A complete and verifiable example article’, "
    "Journal of Examples, 12(3), pp. 10–20."
)

VALID_DOI_LESS_CANDIDATE = {
    "DOI": "10.9999/valid-derived-candidate",
    "title": ["A complete and verifiable example article"],
    "container-title": ["Journal of Examples"],
    "issued": {"date-parts": [[2020]]},
    "author": [{"family": "Example"}],
    "volume": "12",
    "issue": "3",
    "page": "10-20",
}


class FakeResponse:
    def __init__(self, items: list[dict]):
        self.status_code = 200
        self._items = items

    def json(self) -> dict:
        return {"message": {"items": self._items}}


class DerivedDoiScoringTests(unittest.TestCase):
    def test_markdown_transport_backslash_is_removed_from_doi(self) -> None:
        reference = "Available at: https://doi.org/10.1371/journal.pone.0333668.\\"

        self.assertEqual(
            core.extract_doi(reference),
            "10.1371/journal.pone.0333668",
        )

    def test_derived_doi_never_adds_weighted_score_points(self) -> None:
        fields = {
            "doi_explicit_ok": False,
            "author_ok": True,
            "title_ok": True,
            "year_ok": True,
            "journal_ok": False,
            "vol_ok": True,
            "issue_ok": True,
            "pages_ok": True,
            "shape_ok": True,
        }

        without_derived = core.compute_weighted_score(
            doi_derived_ok=False,
            **fields,
        )
        with_derived = core.compute_weighted_score(
            doi_derived_ok=True,
            **fields,
        )

        self.assertEqual(with_derived, without_derived)
        self.assertEqual(with_derived, 10)

    def test_missing_values_are_not_counted_as_conflicts(self) -> None:
        conflicts = core.collect_confirmed_metadata_conflicts(
            {
                "journal": (True, True, False),
                "volume": (False, True, False),
                "issue": (True, False, False),
                "pages": (True, True, True),
            }
        )

        self.assertEqual(conflicts, ["journal"])

    def test_two_confirmed_conflicts_override_a_real_rule(self) -> None:
        result, reason, _score = core.score_result(
            True,
            True,
            True,
            title_ok=True,
            strong_identity_match=True,
            metadata_conflicts=["volume", "pages"],
        )

        self.assertEqual(result, "⚠ Suspicious")
        self.assertIn("multiple metadata conflicts", reason)


class DerivedDoiPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_with_candidate(self, reference: str, candidate: dict) -> dict:
        async def fetch_candidate(_client, _reference):
            return FakeResponse([candidate])

        async def doi_resolves(_client, _doi):
            return True

        with (
            patch.object(core, "fetch_crossref_bib", fetch_candidate),
            patch.object(core, "check_doi_org_head", doi_resolves),
        ):
            return await core.validate_single_ref(None, reference)

    async def test_paisley_wrong_journal_is_not_real(self) -> None:
        result = await self.run_with_candidate(
            PAISLEY_REFERENCE,
            PAISLEY_WRONG_JOURNAL_CANDIDATE,
        )

        self.assertTrue(result["doi_derived_ok"])
        self.assertFalse(result["doi_explicit_ok"])
        self.assertFalse(result["journal_ok"])
        self.assertNotEqual(result["Validation Result"], "✅ Real")
        self.assertEqual(result["Score"], 10)
        self.assertIn("derived DOI used for metadata only", result["Notes"])
        self.assertIn("journal mismatch", result["Notes"])

    async def test_paisley_major_conflicts_override_score_based_suspicious_result(self) -> None:
        result = await self.run_with_candidate(
            PAISLEY_REFERENCE,
            PAISLEY_REAL_WORK_CANDIDATE,
        )

        self.assertEqual(result["Score"], 7)
        self.assertTrue(result["strong_identity_match"])
        # Single-word journal extraction now also exposes the wrong journal.
        self.assertEqual(result["metadata_conflict_count"], 4)
        self.assertEqual(result["metadata_conflicts"], "journal, volume, issue, pages")
        self.assertEqual(result["Validation Result"], "❌ possible falsification")
        self.assertIn("major metadata conflict", result["Notes"])
        self.assertIn("derived DOI used for metadata only", result["Notes"])

    async def test_valid_doi_less_reference_can_pass_from_metadata_fields(self) -> None:
        result = await self.run_with_candidate(
            VALID_DOI_LESS_REFERENCE,
            VALID_DOI_LESS_CANDIDATE,
        )

        self.assertTrue(result["doi_derived_ok"])
        self.assertFalse(result["doi_explicit_ok"])
        self.assertTrue(result["year_ok"])
        self.assertTrue(result["author_ok"])
        self.assertTrue(result["journal_ok"])
        self.assertEqual(result["Validation Result"], "✅ Real")
        self.assertIn("derived DOI used for metadata only", result["Notes"])


if __name__ == "__main__":
    unittest.main()
