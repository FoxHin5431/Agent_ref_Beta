import copy
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core_loader import load_beta_core

core = load_beta_core()
REFERENCE = "Smith, A., Jones, B. and Brown, C. et al. (2024) ‘Quantum transport in layered materials’. arXiv. https://arxiv.org/abs/2401.12345"
RECORD = {"id": "2401.12345v2", "source": "arXiv", "type": "preprint",
    "title": "Quantum transport in layered materials", "authors": ["Smith", "Jones", "Brown", "Taylor"],
    "years": ["2024", "2025"], "journal": "arXiv", "doi": "10.48550/arXiv.2401.12345",
    "url": "https://arxiv.org/abs/2401.12345v2"}
ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2401.12345v2</id><title>Quantum transport in layered materials</title>
<published>2024-01-10T00:00:00Z</published><updated>2025-01-10T00:00:00Z</updated>
<author><name>A. Smith</name></author><author><name>B. Jones</name></author>
</entry></feed>'''


class PhysicsParsingTests(unittest.TestCase):
    def test_identifiers_and_urls(self):
        for source, expected in [
            ("arXiv:2401.12345", "2401.12345"), ("arXiv:hep-th/9711200v3", "hep-th/9711200v3"),
            ("https://arxiv.org/pdf/2401.12345v2.pdf", "2401.12345v2"),
            ("https://arxiv.org/abs/2401.12345?context=quant-ph", "2401.12345"),
            ("https://doi.org/10.48550/arXiv.2401.12345", "2401.12345"),
            ("arXiv:2401.1234567", ""), ("DOI:10.1000/2401.12345", "")]:
            with self.subTest(source=source):
                self.assertEqual(core.extract_arxiv_id(source), expected)

    def test_atom_keeps_arxiv_dates_and_collective_authors(self):
        record = core.parse_arxiv_feed(ATOM)[0]
        self.assertEqual(record["years"], ["2024", "2025"])
        self.assertEqual(record["authors"], ["Smith", "Jones"])
        self.assertEqual(record["journal"], "arXiv")
        self.assertEqual(core.arxiv_author_surname("The ATLAS Collaboration"), "The ATLAS Collaboration")
        self.assertEqual(core.arxiv_author_surname("Ludwig van Beethoven"), "van Beethoven")

    def test_api_error_is_not_a_record(self):
        self.assertEqual(core.parse_arxiv_feed('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title></entry></feed>'), [])

    def test_version_and_ambiguous_search_are_not_substituted(self):
        self.assertIsNone(core.select_physics_record([RECORD], "2401.12345v1", "", [], False))
        self.assertIsNone(core.select_physics_record([RECORD, RECORD], "", RECORD["title"], ["Smith"], True))

    def test_collaboration_followed_by_personal_authors(self):
        reference = "Planck Collaboration, Aghanim, N., Akrami, Y. et al. (2018) ‘Cosmological parameters’. arXiv:1807.06209"
        self.assertEqual(core.extract_author_surnames(reference), ["Planck Collaboration", "Aghanim", "Akrami"])
        self.assertEqual(core.extract_author_surnames("Planck Collaboration et al. (2018) ‘Title’."), ["Planck Collaboration"])

    def test_arxiv_identifier_finishes_reference_for_splitting(self):
        text = "Jones, M. (2024) ‘Quantum transport’. arXiv:2401.12345\n\nSmith, A. (2024) ‘Another title’. arXiv:2401.54321"
        self.assertEqual(len(core.split_references(text)), 2)


class PhysicsPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def validate(self, reference=REFERENCE, record=RECORD):
        with patch.object(core, "fetch_arxiv_records", AsyncMock(return_value=([record], ""))), \
             patch.object(core, "fetch_crossref_doi", AsyncMock(side_effect=AssertionError("arXiv sent to Crossref"))), \
             patch.object(core, "check_doi_org_head", AsyncMock(return_value=True)):
            return await core.validate_single_ref(None, reference, ads_token="")

    async def test_preprint_passes_without_journal_publication_fields(self):
        result = await self.validate()
        self.assertEqual(result["Validation Result"], "✅ Real", result["Notes"])
        self.assertEqual(result["Metadata Source"], "arXiv")
        self.assertEqual(result["Crossref DOI"], "")
        self.assertEqual(result["Comparison evidence"]["volume"]["status"], "unknown")
        self.assertNotIn("published", result["Notes"])

    async def test_first_author_et_al_and_spacing(self):
        for author in ["Smith, A. et al.", "Smith, A. et\u00a0al."]:
            result = await self.validate(REFERENCE.replace("Smith, A., Jones, B. and Brown, C. et al.", author))
            self.assertTrue(result["author_ok"])

    async def test_deliberate_conflicts_remain_visible(self):
        for old, new in [("Smith", "Inventedperson"), (RECORD["title"], "Orchard irrigation methods and apple harvesting"), ("(2024)", "(1990)")]:
            result = await self.validate(REFERENCE.replace(old, new))
            self.assertEqual(result["Validation Result"], "⚠ Suspicious", result["Notes"])

    async def test_arxiv_doi_uses_correct_registration_source(self):
        result = await self.validate(REFERENCE.replace("https://arxiv.org/abs/2401.12345", "https://doi.org/10.48550/arXiv.2401.12345"))
        self.assertTrue(result["doi_explicit_ok"])
        self.assertEqual(result["Validation Result"], "✅ Real")

    async def test_resolver_failure_does_not_destroy_arxiv_record(self):
        with patch.object(core, "fetch_arxiv_records", AsyncMock(return_value=([RECORD], ""))), \
             patch.object(core, "check_doi_org_head", AsyncMock(return_value=None)):
            result = await core.validate_single_ref(None, REFERENCE + " doi:10.48550/arXiv.2401.12345", ads_token="")
        self.assertTrue(result["author_ok"])
        self.assertFalse(result["doi_explicit_ok"])
        self.assertIn("DOI resolver confirmation unavailable", result["Notes"])

    async def test_additional_unverified_or_conflicting_doi(self):
        for doi in ["10.48550/arXiv.2402.54321", "10.1234/unconfirmed"]:
            result = await self.validate(REFERENCE + " doi:" + doi)
            self.assertNotEqual(result["Validation Result"], "✅ Real")

    async def test_unavailable_sources_require_review(self):
        with patch.object(core, "fetch_arxiv_records", AsyncMock(return_value=([], "arXiv lookup unavailable: HTTP 503"))), \
             patch.object(core, "fetch_arxiv_datacite", AsyncMock(return_value=(None, "DataCite lookup unavailable: HTTP 503"))):
            result = await core.validate_single_ref(None, REFERENCE, ads_token="")
        self.assertEqual(result["Validation Result"], core.UNVERIFIED_LABEL)
        self.assertNotIn("mismatch", result["Notes"])

    async def test_title_only_arxiv_search_requires_unique_identity(self):
        result = await self.validate(REFERENCE.replace("https://arxiv.org/abs/2401.12345", ""))
        self.assertEqual(result["Validation Result"], "✅ Real", result["Notes"])

    async def test_ads_record_uses_same_evidence_pipeline(self):
        record = dict(RECORD, id="2024PhRvD.109a2345S", source="NASA ADS", type="article",
                      journal="Physical Review D", doi="10.1234/example")
        reference = REFERENCE.replace("arXiv. https://arxiv.org/abs/2401.12345",
            "Physical Review D. https://ui.adsabs.harvard.edu/abs/2024PhRvD.109a2345S/abstract")
        with patch.object(core, "fetch_ads_record", AsyncMock(return_value=(record, ""))) as lookup:
            result = await core.validate_single_ref(None, reference, ads_token="test-token")
        self.assertEqual(result["Validation Result"], "✅ Real", result["Notes"])
        self.assertEqual(result["Metadata Source"], "NASA ADS")
        self.assertEqual(lookup.call_args.kwargs["token"], "test-token")

    async def test_ads_discovery_after_crossref_has_no_candidate(self):
        record = dict(RECORD, source="NASA ADS", journal="Physical Review D")
        response = Mock(status_code=200)
        response.json.return_value = {"message": {"items": []}}
        reference = REFERENCE.replace("arXiv. https://arxiv.org/abs/2401.12345", "Physical Review D.")
        with patch.object(core, "fetch_crossref_bib", AsyncMock(return_value=response)), \
             patch.object(core, "fetch_crossref_fielded", AsyncMock(return_value=response)), \
             patch.object(core, "fetch_ads_record", AsyncMock(return_value=(record, ""))):
            result = await core.validate_single_ref(None, reference, ads_token="test-token")
        self.assertEqual(result["Validation Result"], "✅ Real", result["Notes"])

    async def test_arxiv_word_in_journal_article_title_does_not_reroute(self):
        reference = "Smith, A. (2024) ‘Studies of arXiv submissions’. Journal of Research. doi:10.1234/example"
        response = Mock(status_code=404)
        with patch.object(core, "fetch_arxiv_records", AsyncMock(side_effect=AssertionError("incorrect route"))) as arxiv, \
             patch.object(core, "fetch_crossref_doi", AsyncMock(return_value=response)) as crossref, \
             patch.object(core, "check_doi_org_head", AsyncMock(return_value=False)):
            await core.validate_single_ref(None, reference, ads_token="")
        arxiv.assert_not_awaited()
        crossref.assert_awaited_once()


class PhysicsNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_datacite_refuses_version_substitution(self):
        client = Mock(get=AsyncMock())
        record, issue = await core.fetch_arxiv_datacite(client, "2401.12345v1")
        self.assertIsNone(record)
        client.get.assert_not_awaited()

    async def test_successful_arxiv_queries_are_cached(self):
        core._ARXIV_CACHE.clear()
        core._ARXIV_LAST_REQUEST = 0
        client = Mock(get=AsyncMock(return_value=Mock(status_code=200, text=ATOM)))
        await core.fetch_arxiv_records(client, identifier="2401.12345")
        await core.fetch_arxiv_records(client, identifier="2401.12345")
        self.assertEqual(client.get.await_count, 1)
        core._ARXIV_CACHE.clear()

    async def test_ads_token_missing_and_http_errors_are_not_mismatches(self):
        client = Mock(get=AsyncMock(return_value=Mock(status_code=429)))
        record, issue = await core.fetch_ads_record(client, token="", identifier="test")
        self.assertIn("not configured", issue)
        client.get.assert_not_awaited()
        record, issue = await core.fetch_ads_record(client, token="secret-fixture", identifier="test")
        self.assertIn("HTTP 429", issue)
        self.assertNotIn("secret-fixture", issue)

    async def test_ads_exact_record_and_wrong_identifier(self):
        doc = {"bibcode": "2024PhRvD.109a2345S", "identifier": ["2024PhRvD.109a2345S"],
               "title": [RECORD["title"]], "author": ["Smith, A.", "Jones, B."], "year": "2024", "pub": "Physical Review D"}
        response = Mock(status_code=200)
        response.json.return_value = {"response": {"docs": [doc]}}
        client = Mock(get=AsyncMock(return_value=response))
        record, issue = await core.fetch_ads_record(client, token="secret-fixture", identifier=doc["bibcode"])
        self.assertEqual(record["authors"], ["Smith", "Jones"])
        self.assertEqual(record["source"], "NASA ADS")
        self.assertEqual(client.get.call_args.kwargs["headers"]["Authorization"], "Bearer secret-fixture")
        record, issue = await core.fetch_ads_record(client, token="secret-fixture", identifier="another")
        self.assertIsNone(record)


if __name__ == "__main__":
    unittest.main()
