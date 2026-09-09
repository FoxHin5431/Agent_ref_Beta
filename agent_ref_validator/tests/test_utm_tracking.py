from __future__ import annotations

import unittest

from core_loader import load_beta_core


core = load_beta_core()


class ChatGptUtmDetectionTests(unittest.TestCase):
    def test_genuine_pilot_examples_are_flagged(self) -> None:
        references = [
            "https://journal.hep.com.cn/fcse/EN/10.1007/s11705-023-2355-3?utm_source=chatgpt.com",
            (
                "National Park Service (2024) Wolf & Moose Population Data. Available at: "
                "https://www.nps.gov/isro/learn/nature/wolf-moose-populations.htm?utm_source=chatgpt.com "
                "(Accessed 21 January 2026)."
            ),
            (
                "Taqueti, V.R. (2018) 'Sex Differences in the Coronary System'. Available at: "
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC6467060/?utm_source=chatgpt.com#R2"
            ),
        ]

        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue(core.has_chatgpt_utm_source(reference))

    def test_encoded_html_escaped_and_case_variants_are_flagged(self) -> None:
        references = [
            "https://example.org/article?x=1&amp;utm_source=ChatGPT.com",
            "https%3A%2F%2Fexample.org%2Farticle%3Futm_source%3Dchatgpt.com",
            "https://example.org/article?UTM_SOURCE=www.chatgpt.com",
            "https://example.org/article?utm_source=chatgpt",
        ]

        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue(core.has_chatgpt_utm_source(reference))

    def test_unrelated_or_lookalike_tracking_values_are_not_flagged(self) -> None:
        references = [
            "https://www.nature.com/article.pdf?utm_source=clarivate",
            "https://example.org/article?utm_medium=chatgpt.com",
            "https://example.org/article?utm_source=chatgptish.com",
            "https://example.org/article?utm_source=newsletter",
            "This prose mentions utm_source=chatgpt.com without a URL query.",
        ]

        for reference in references:
            with self.subTest(reference=reference):
                self.assertFalse(core.has_chatgpt_utm_source(reference))

    def test_tracking_query_is_not_part_of_extracted_doi(self) -> None:
        reference = (
            "https://journal.hep.com.cn/fcse/EN/"
            "10.1007/s11705-023-2355-3?utm_source=chatgpt.com"
        )

        self.assertEqual(
            core.extract_doi(reference),
            "10.1007/s11705-023-2355-3",
        )


class ChatGptUtmPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_does_not_change_reference_classification_or_score(self) -> None:
        clean = (
            "National Park Service (2024) Wolf & Moose Population Data. Available at: "
            "https://www.nps.gov/isro/learn/nature/wolf-moose-populations.htm"
        )
        tracked = clean + "?utm_source=chatgpt.com"

        clean_result = await core.validate_single_ref(None, clean)
        tracked_result = await core.validate_single_ref(None, tracked)

        self.assertFalse(clean_result["AI URL flag"])
        self.assertTrue(tracked_result["AI URL flag"])
        self.assertEqual(tracked_result["AL notes"], core.AI_URL_NOTE)
        self.assertEqual(
            tracked_result["Validation Result"],
            clean_result["Validation Result"],
        )
        self.assertEqual(tracked_result["Score"], clean_result["Score"])


if __name__ == "__main__":
    unittest.main()
