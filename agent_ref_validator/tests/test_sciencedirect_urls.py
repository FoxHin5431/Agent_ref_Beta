from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from core_loader import load_beta_core


REFERENCE = (
    "Brunnström, H., Gastafson, L., Passant, U. and Englund, E. (2009) "
    "‘Prevalence of Dementia Subtypes: A 30-year Retrospective Survey of "
    "Neuropathological Reports’, Archives of Gerontology and Geriatrics, "
    "49(1), pp. 146-149."
)
URL = "https://www.sciencedirect.com/science/article/pii/S0167494308001246"
TRACKED_URL = URL + "?getft_integrator=clarivate&pes=vor&utm_source=clarivate"
SUFFIXES = (
    "",
    f" Available at: {URL} (Accessed 22 August 2026)",
    f" Available at: {TRACKED_URL} (Accessed 22 August 2026)",
    f" Available at: [{TRACKED_URL}]({TRACKED_URL}) (Accessed 22 August 2026)",
    " Available at: [" + TRACKED_URL.replace("_", r"\_") + "]("
    + TRACKED_URL.replace("&", r"\&") + ") (Accessed 22 August 2026)",
    f" Available at: {URL.replace('/article/pii/', '/article/abs/pii/')} "
    "(Accessed 22 August 2026)",
)

# Bibliographic fields verified against PubMed PMID 18692255.
ARTICLE = {
    "DOI": "10.1016/j.archger.2008.06.005",
    "title": ["Prevalence of dementia subtypes: A 30-year retrospective survey "
              "of neuropathological reports"],
    "container-title": ["Archives of Gerontology and Geriatrics"],
    "issued": {"date-parts": [[2009]]},
    "author": [{"family": surname} for surname in
               ("Brunnström", "Gustafson", "Passant", "Englund")],
    "volume": "49",
    "issue": "1",
    "page": "146-149",
}


class ScienceDirectUrlTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_beta_core()

    async def validate(self, reference):
        response = unittest.mock.Mock(status_code=200)
        response.json.return_value = {"message": {"items": [ARTICLE]}}
        missing_doi = unittest.mock.Mock(status_code=404)
        with (
            patch.object(self.core, "fetch_crossref_bib", AsyncMock(return_value=response)) as search,
            patch.object(self.core, "fetch_crossref_doi", AsyncMock(return_value=missing_doi)) as doi_lookup,
            patch.object(self.core, "check_doi_org_head", AsyncMock(return_value=False)),
        ):
            result = await self.core.validate_single_ref(None, reference)
        search.assert_awaited_once()
        doi_lookup.assert_not_awaited()
        self.assertEqual(result["Crossref DOI"], ARTICLE["DOI"])
        self.assertEqual(result["Extracted DOI"], "")
        self.assertFalse(result["doi_explicit_ok"])
        return result

    async def test_url_does_not_change_validation_or_metadata(self):
        for reference in (REFERENCE, REFERENCE.replace("Gastafson", "Gustafson")):
            baseline = await self.validate(reference)
            for suffix in SUFFIXES[1:]:
                with self.subTest(reference=reference, suffix=suffix):
                    result = await self.validate(reference + suffix)
                    for field in ("Validation Result", "Score", "Metadata Authors",
                                  "author_ok", "title_ok", "year_ok", "journal_ok",
                                  "vol_ok", "issue_ok", "pages_ok"):
                        self.assertEqual(result[field], baseline[field], field)
                    self.assertFalse(result["AI URL flag"])
            if "Gustafson" in reference:
                self.assertEqual(baseline["Validation Result"], "✅ Real")

    async def test_url_does_not_hide_a_conflicting_author(self):
        result = await self.validate(REFERENCE.replace("Gastafson", "Unrelated") + SUFFIXES[2])
        self.assertFalse(result["author_ok"])
        self.assertNotEqual(result["Validation Result"], "✅ Real")


if __name__ == "__main__":
    unittest.main()
