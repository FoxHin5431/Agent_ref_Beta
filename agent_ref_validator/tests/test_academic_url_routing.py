from __future__ import annotations

import unittest
from unittest.mock import patch

from core_loader import load_beta_core


PUBMED_REFERENCE = (
    "Boutari, C., DeMarsilis, A. and Mantzoros, C.S. (2023) "
    "‘Obesity and diabetes’, Diabetes Research and Clinical Practice. "
    "Available at: https://pubmed.ncbi.nlm.nih.gov/37356727/ "
    "(Accessed: 12 May 2026)."
)

PMC_REFERENCE = (
    "Colberg, S.R., Sigal, R.J., Yardley, J.E., Riddell, M.C., "
    "Dunstan, D.W., Dempsey, P.C., Horton, E.S., Castorino, K. and "
    "Tate, D.F. (2016) ‘Physical activity/exercise and diabetes: A "
    "position statement of the American Diabetes Association’. Available "
    "at: https://pmc.ncbi.nlm.nih.gov/articles/PMC6908414/ "
    "(Accessed: 12 May 2026)."
)

WHO_REFERENCE = (
    "World Health Organization (WHO) (2024a) Diabetes. Available at: "
    "https://www.who.int/news-room/fact-sheets/detail/diabetes "
    "(Accessed: 12 May 2026)."
)

ONS_REFERENCE = (
    "Office for National Statistics. 2019registrations. "
    "https://www.ons.gov.uk/peoplepopulationandcommunity/"
    "birthsdeathsandmarriages/deaths/bulletins/"
    "dementiaandalzheimersdiseasedeathsincludingcomorbidities"
    "englandandwales/2019registrations"
)

CHEN_REFERENCE = (
    "Chen, J., Spracklen, C.N., Marenne, G. et al. (2021) "
    "‘The trans-ancestral genomic architecture of glycemic traits’, "
    "Nature Genetics, 53(6), pp. 840–860. Available at: "
    "https://pubmed.ncbi.nlm.nih.gov/34059833/ (Accessed: 10 August 2026)."
)

GRACNER_REFERENCE = (
    "Gracner, T., Boone, C. and Gertler, P.J. (2024) ‘Exposure to sugar "
    "rationing in the first 1000 days of life protected against chronic "
    "disease’, Science, 386(6725), pp. 1043–1048. Available at: "
    "https://pubmed.ncbi.nlm.nih.gov/39480913/ (Accessed: 10 August 2026"
)


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "result": {
                "37356727": {
                    "fulljournalname": "Diabetes Research and Clinical Practice",
                    "pubdate": "2023",
                    "title": "Obesity and diabetes",
                    "authors": [
                        {"name": "Boutari C"},
                        {"name": "DeMarsilis A"},
                        {"name": "Mantzoros CS"},
                    ],
                }
            }
        }


class FakePmcResponse:
    status_code = 200

    def json(self):
        return {
            "result": {
                "uids": ["PMC6908414"],
                "PMC6908414": {
                    "fulljournalname": "Diabetes Care",
                    "pubdate": "2016",
                    "title": (
                        "Physical activity/exercise and diabetes: A position "
                        "statement of the American Diabetes Association"
                    ),
                    "authors": [
                        {"name": "Colberg SR"},
                        {"name": "Sigal RJ"},
                        {"name": "Yardley JE"},
                        {"name": "Riddell MC"},
                        {"name": "Dunstan DW"},
                        {"name": "Dempsey PC"},
                        {"name": "Horton ES"},
                        {"name": "Castorino K"},
                        {"name": "Tate DF"},
                    ],
                    "articleids": [{"idtype": "pmid", "value": "27979891"}],
                },
            }
        }


class FakePubmedArticleResponse:
    status_code = 200

    def __init__(self, pmid, summary):
        self.pmid = pmid
        self.summary = summary

    def json(self):
        return {"result": {self.pmid: self.summary}}


class AcademicUrlRoutingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.core = load_beta_core()

    def test_pubmed_and_pmc_urls_are_identifiers_not_generic_web_sources(self) -> None:
        self.assertEqual(self.core.extract_pmid(PUBMED_REFERENCE), "37356727")
        self.assertEqual(self.core.extract_pmcid(PMC_REFERENCE), "PMC6908414")
        self.assertFalse(
            self.core.looks_like_org_web_source(
                PUBMED_REFERENCE,
                primary_domain="pubmed.ncbi.nlm.nih.gov",
            )
        )
        self.assertFalse(
            self.core.looks_like_org_web_source(
                PMC_REFERENCE,
                primary_domain="pmc.ncbi.nlm.nih.gov",
            )
        )

    def test_genuine_organisational_page_still_requires_manual_review(self) -> None:
        self.assertTrue(
            self.core.looks_like_org_web_source(
                WHO_REFERENCE,
                primary_domain="who.int",
            )
        )

    async def test_organisational_author_fallback_accepts_unicode_pattern(self) -> None:
        result = await self.core.validate_single_ref(None, ONS_REFERENCE)

        self.assertEqual(result["Validation Result"], self.core.MANUAL_REVIEW_LABEL)

    async def test_pubmed_url_continues_through_academic_validation(self) -> None:
        async def fetch_pubmed(_client, pmid):
            self.assertEqual(pmid, "37356727")
            return FakeResponse()

        with patch.object(self.core, "fetch_pubmed", fetch_pubmed):
            result = await self.core.validate_single_ref(None, PUBMED_REFERENCE)

        self.assertEqual(result["PubMed ID"], "37356727")
        self.assertEqual(result["Validation Result"], "✅ Real")
        self.assertNotEqual(result["Validation Result"], self.core.MANUAL_REVIEW_LABEL)

    async def test_pmc_article_without_submitted_journal_uses_title_identity(self) -> None:
        async def fetch_pmc(_client, pmcid):
            self.assertEqual(pmcid, "PMC6908414")
            return FakePmcResponse()

        with patch.object(self.core, "fetch_pmc", fetch_pmc):
            result = await self.core.validate_single_ref(None, PMC_REFERENCE)

        self.assertEqual(result["PubMed ID"], "PMC6908414")
        self.assertTrue(result["title_ok"])
        self.assertEqual(result["Validation Result"], "✅ Real")

    async def test_pubmed_volume_issue_and_pages_are_compared(self) -> None:
        cases = {
            "34059833": (
                CHEN_REFERENCE,
                {
                    "fulljournalname": "Nature Genetics",
                    "pubdate": "2021 Jun",
                    "title": "The trans-ancestral genomic architecture of glycemic traits",
                    "authors": [
                        {"name": "Chen J"},
                        {"name": "Spracklen CN"},
                        {"name": "Marenne G"},
                        {"name": "Varshney A"},
                    ],
                    "volume": "53",
                    "issue": "6",
                    "pages": "840-860",
                },
            ),
            "39480913": (
                GRACNER_REFERENCE,
                {
                    "fulljournalname": "Science (New York, N.Y.)",
                    "pubdate": "2024 Nov 29",
                    "title": (
                        "Exposure to sugar rationing in the first 1000 days of "
                        "life protected against chronic disease"
                    ),
                    "authors": [
                        {"name": "Gracner T"},
                        {"name": "Boone C"},
                        {"name": "Gertler PJ"},
                    ],
                    "volume": "386",
                    "issue": "6725",
                    "pages": "1043-1048",
                },
            ),
        }

        async def fetch_pubmed(_client, pmid):
            _reference, summary = cases[pmid]
            return FakePubmedArticleResponse(pmid, summary)

        with patch.object(self.core, "fetch_pubmed", fetch_pubmed):
            for pmid, (reference, _summary) in cases.items():
                with self.subTest(pmid=pmid):
                    result = await self.core.validate_single_ref(None, reference)
                    self.assertEqual(result["Extracted Volume"], result["Metadata Volume"])
                    self.assertEqual(result["Extracted Issue"], result["Metadata Issue"])
                    self.assertTrue(result["pages_ok"])
                    self.assertTrue(result["vol_ok"])
                    self.assertTrue(result["issue_ok"])
                    self.assertEqual(result["metadata_conflict_count"], 0)
                    self.assertEqual(result["Validation Result"], "✅ Real")


if __name__ == "__main__":
    unittest.main()
