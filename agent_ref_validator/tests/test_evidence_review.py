"""Reported false alarms paired with material alterations and lookup failures."""
import copy
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core_loader import load_beta_core
from reported_cases import CASES

core = load_beta_core()


def response(message, status=200):
    value = Mock(status_code=status)
    value.json.return_value = {"message": message}
    return value


async def validate_case(module, reference, metadata, *, resolver=True):
    async def doi_lookup(_client, doi):
        return response(metadata) if doi.lower() == metadata["DOI"].lower() else response({}, 404)
    with (
        patch.object(module, "fetch_crossref_doi", doi_lookup),
        patch.object(module, "fetch_crossref_bib", AsyncMock(return_value=response({"items": [metadata]}))),
        patch.object(module, "fetch_crossref_fielded", AsyncMock(return_value=response({"items": [metadata]}))),
        patch.object(module, "check_doi_org_head", AsyncMock(return_value=resolver)),
    ):
        return await module.validate_single_ref(None, reference)


def paired_cases():
    """Same inputs and frozen lookup records for baseline/candidate replay."""
    rows = []
    for name, case in CASES.items():
        rows.append((name, "genuine" if name != "breuer" else "uncertain", case["reference"], case["metadata"]))
    for name in ("bucher", "hogervorst"):
        case = CASES[name]
        first = "Bucher" if name == "bucher" else "Hogervorst"
        coauthor = "Menzel" if name == "bucher" else "Richards"
        for label, old, new in (("first_author", first, "Invented"), ("coauthor", coauthor, "Invented")):
            rows.append((name + "_" + label, "altered", case["reference"].replace(old, new), case["metadata"]))
    b = CASES["bucher"]
    for explicit in (False, True):
        suffix = " doi:" + b["metadata"]["DOI"] if explicit else ""
        rows.append((f"publication_conflicts_{explicit}", "altered",
            b["reference"].replace("Vol 8, Is 2, Pg 1147-1158", "Vol 99, Is 9, Pg 2000-2050") + suffix,
            b["metadata"]))
        rows.append((f"wrong_journal_{explicit}", "altered",
            b["reference"].replace("Ecology and Evolution", "Journal of Invented Medicine") + suffix,
            b["metadata"]))
        rows.append((f"wrong_title_{explicit}", "altered",
            b["reference"].replace("Traits and climate are associated with first flowering day in herbaceous species along elevational gradients",
                "Hospital administration and surgical patient satisfaction across urban healthcare networks") + suffix,
            b["metadata"]))
    # DOI pairings must stay flagged even if lookup yields a real record.
    h = CASES["hogervorst"]
    rows.append(("borrowed_doi", "altered", h["reference"].replace(
        "10.1002/14651858.CD003799.pub2/full", b["metadata"]["DOI"]), b["metadata"]))
    rows.append(("bucher_accents", "genuine", b["reference"].replace("Konig", "König").replace("Romermann", "Römermann"), b["metadata"]))
    rows.append(("bucher_commas", "genuine", b["reference"].replace(". SF", ", SF").replace(". P,", ", P,")
        .replace(". A,", ", A,").replace(". M,", ", M,").replace(". J,", ", J,").replace(". C,", ", C,"), b["metadata"]))
    # Well-parsed controls expose scoring holes independently of the reported
    # punctuation bug: old author coverage/DOI rules could clear these hybrids.
    conventional = rows[-1][2]
    rows.append(("parsed_invented_coauthor", "altered", conventional.replace("Menzel", "Invented"), b["metadata"]))
    rows.append(("parsed_wrong_title", "altered", conventional.replace(
        "Traits and climate are associated with first flowering day in herbaceous species along elevational gradients",
        "Hospital administration and surgical patient satisfaction across urban healthcare networks"), b["metadata"]))
    rows.append(("parsed_doi_publication_conflicts", "altered", conventional.replace(
        "Vol 8, Is 2, Pg 1147-1158", "99(9), pp. 2000-2050") + " doi:" + b["metadata"]["DOI"], b["metadata"]))
    return rows


class EvidenceReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_paired_genuine_and_altered_references(self):
        for name, truth, reference, metadata in paired_cases():
            with self.subTest(case=name):
                result = await validate_case(core, reference, metadata)
                if truth == "genuine":
                    self.assertEqual(result["Validation Result"], "✅ Real", result["Notes"])
                elif truth == "uncertain":
                    self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL, result["Notes"])
                    self.assertEqual(result["Comparison evidence"]["authors"]["status"], "unknown")
                else:
                    self.assertIn(result["Validation Result"], {"⚠ Suspicious", "❌ possible falsification"}, result["Notes"])

    async def test_bucher_all_fields_extracted_and_matched(self):
        case = CASES["bucher"]
        result = await validate_case(core, case["reference"], case["metadata"])
        self.assertEqual(result["Submitted author count"], 6)
        self.assertEqual(result["Author match count"], 6)
        self.assertEqual(result["Extracted Volume"], "8")
        self.assertEqual(result["Extracted Issue"], "2")
        self.assertEqual(result["metadata_conflict_count"], 0)

    async def test_partial_parse_is_unknown_not_complete_author_conflict(self):
        case = CASES["bucher"]
        ref = case["reference"].replace("SF, Konig", "SF; Konig")
        result = await validate_case(core, ref, case["metadata"])
        self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL)
        self.assertIn("incomplete author extraction", result["Notes"])

    async def test_timeout_and_service_errors_are_reviewable(self):
        case = CASES["hogervorst"]
        for status in (403, 429, 500, 503, 404):
            with self.subTest(status=status), patch.object(core, "fetch_crossref_doi", AsyncMock(return_value=response({}, status))), patch.object(core, "check_doi_org_head", AsyncMock(return_value=None)):
                result = await core.validate_single_ref(None, case["reference"])
                self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL)
                self.assertEqual(result["metadata_conflict_count"], 0)
                self.assertNotIn("invalid DOI", result["Notes"])
        with patch.object(core, "fetch_crossref_doi", AsyncMock(side_effect=TimeoutError)):
            result = await core.validate_single_ref(None, case["reference"])
        self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL)
        self.assertIn("TimeoutError", result["Lookup issue"])

    async def test_completed_not_found_is_not_falsification_evidence(self):
        with patch.object(core, "fetch_crossref_doi", AsyncMock(return_value=response({}, 404))), patch.object(core, "check_doi_org_head", AsyncMock(return_value=False)):
            result = await core.validate_single_ref(None, CASES["hogervorst"]["reference"])
        self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL)
        self.assertIn("not found", result["Notes"])

    async def test_missing_metadata_authors_are_unknown(self):
        case = CASES["hogervorst"]
        metadata = copy.deepcopy(case["metadata"])
        metadata.pop("author")
        result = await validate_case(core, case["reference"], metadata)
        self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL)
        self.assertNotIn("authors", result["metadata_conflicts"])

    async def test_resolver_failure_does_not_erase_known_wrong_authors(self):
        case = CASES["hogervorst"]
        result = await validate_case(core, case["reference"].replace("Richards", "Invented"), case["metadata"], resolver=None)
        self.assertEqual(result["Validation Result"], "⚠ Suspicious")
        self.assertIn("author list conflicts", result["Notes"])

    async def test_optional_review_lookup_failure_preserves_conflicts(self):
        case = CASES["hogervorst"]
        # Force a publication-type lookup after bibliographic comparisons.
        metadata = {"result": {"12345678": {"title": "A sufficiently distinctive reference title",
            "authors": [{"name": "Example A"}], "pubdate": "2020", "fulljournalname": "Journal of Examples"}}}
        resp = Mock(status_code=200)
        resp.json.return_value = metadata
        with patch.object(core, "fetch_pubmed", AsyncMock(return_value=resp)), patch.object(core, "fetch_pubmed_efetch_xml", AsyncMock(side_effect=TimeoutError)):
            result = await core.validate_single_ref(None, "Invented, A. (2020) ‘A sufficiently distinctive reference title’, Journal of Examples. PMID:12345678", check_reviews=True)
        self.assertEqual(result["Validation Result"], "⚠ Suspicious")


class ExtractionBoundaryTests(unittest.TestCase):
    def test_title_numbers_cannot_be_publication_fields(self):
        reference = "Example, A. (2025) ‘Comparison of protease 1 (LEGU-1) in cells’, Acta Parasitologica, 71(1), p. 8."
        self.assertEqual(core.extract_vol_issue_pages(reference)[:3], ("71", "1", "8"))

    def test_publisher_identifier_cannot_be_volume_issue_or_page_range(self):
        reference = "Example, A. (2025) ‘A sufficiently distinctive title’, Gastroenterology, pp. S0016-5085(25)06096–2. doi:10.1053/j.gastro.2025.09.042"
        self.assertEqual(core.extract_vol_issue_pages(reference)[:3], ("", "", ""))
        self.assertEqual(core.extract_journal(reference), "gastroenterology")

    def test_journal_commas_preprint_notes_and_title_words_stay_separate(self):
        cases = [
            ("Example, A. (2025) ‘Cancer research and its role in society’, Cancer immunology, immunotherapy : CII, 75(1), p. 6.", "cancer immunology, immunotherapy : cii"),
            ("Example, A. (2025) ‘Cancer research and its role in society’, JAMA oncology [Preprint]. Available at: https://doi.org/10.1234/test", "jama oncology"),
            ("Example, A. (2025) ‘The chemistry of 3,3,3-trifluoro compounds’, Journal of the American Chemical Society [Preprint].", "journal of the american chemical society"),
        ]
        for reference, expected in cases:
            with self.subTest(reference=reference):
                self.assertEqual(core.extract_journal(reference), expected)
        self.assertTrue(core.journals_match("FASEB journal : official publication of the Federation of American Societies for Experimental Biology", "The FASEB Journal"))

    def test_doi_url_route_is_narrow_and_preserves_identifier_slashes(self):
        self.assertEqual(core.extract_doi(CASES["hogervorst"]["reference"]), "10.1002/14651858.CD003799.pub2")
        for ref in ("doi:10.1234/example/full", "https://doi.org/10.1234/example/full",
                    "https://unrelated.example/cdsr/doi/10.1234/example/full"):
            self.assertEqual(core.extract_doi(ref), "10.1234/example/full")
        self.assertEqual(core.extract_doi("doi:10.1016/0006-3207(95)00013-T"), "10.1016/0006-3207(95)00013-T")

    def test_journal_uncertainty_requires_plausible_token_alignment(self):
        self.assertTrue(core.journal_comparison_issue("J Exp Bot", "Journal of Experimental Botany", False))
        self.assertFalse(core.journal_comparison_issue("Journal of Surgery", "Ecology and Evolution", False))
        self.assertTrue(core.journals_match("Nat Rev Neurosci", "Nature Reviews Neuroscience"))


if __name__ == "__main__":
    unittest.main()
