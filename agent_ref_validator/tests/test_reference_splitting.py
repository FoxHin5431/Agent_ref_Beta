from __future__ import annotations

import unittest

from core_loader import load_beta_core


core = load_beta_core()


class ReferenceSplittingTests(unittest.TestCase):
    def test_inline_numbered_vancouver_references_split_without_splitting_citation_numbers(self) -> None:
        pasted = (
            "Bekaii-Saab TS, Yaeger R, Spira AI, et al. Adagrasib in Advanced Solid Tumors "
            "Harboring a KRAS(G12C) Mutation. J Clin Oncol. 2023;41(25):4097- 4106. "
            "6 Strickler JH, Satake H, George TJ, et al. Sotorasib in KRAS p.G12C-Mutated "
            "Advanced Pancreatic Cancer. N Engl J Med. 2023;388(1):33-43. "
            "7 Wolpin BM, Park W, Garrido-Laguna I, et al. Daraxonrasib in Previously Treated "
            "Advanced RAS-Mutated Pancreatic Cancer. N Engl J Med. 2026;394(18):1790-1802. "
            "8 O'Reilly EM, Wainberg ZA, Hendifar AE, et al. Daraxonrasib or Chemotherapy in "
            "Previously Treated Metastatic Pancreatic Cancer. N Engl J Med. "
            "2026;395(4):325-337. "
            "9 Mateo J, Chakravarty D, Dienstmann R, et al. A framework to rank genomic "
            "alterations as targets for cancer precision medicine: the ESMO Scale for "
            "Clinical Actionability of molecular Targets (ESCAT). Ann Oncol. "
            "2018;29(9):1895-1902."
        )

        for pdf_text in (pasted, pasted.replace(". 6 Strickler", ".\n6 Strickler")):
            with self.subTest(has_actual_newline="\n" in pdf_text):
                references = core.split_references(pdf_text)

                self.assertEqual(len(references), 5)
                self.assertTrue(references[0].startswith("Bekaii-Saab TS"))
                self.assertTrue(references[1].startswith("Strickler JH"))
                self.assertTrue(references[2].startswith("Wolpin BM"))
                self.assertTrue(references[3].startswith("O'Reilly EM"))
                self.assertTrue(references[4].startswith("Mateo J"))
                self.assertIn("2023;41(25):4097- 4106", references[0])
                self.assertIn("2026;395(4):325-337", references[3])

    def test_vancouver_year_volume_issue_and_pages_do_not_create_boundaries(self) -> None:
        pasted = (
            "Bekaii-Saab TS, Yaeger R, Spira AI, et al. Adagrasib in Advanced Solid Tumors "
            "Harboring a KRAS(G12C) Mutation. J Clin Oncol. 2023;41(25):4097- 4106."
        )

        references = core.split_references(pasted)

        self.assertEqual(len(references), 1)
        self.assertIn("2023;41(25):4097- 4106", references[0])

    def test_internal_title_and_journal_line_breaks_become_spaces(self) -> None:
        pasted = """Stern, Claudio D, and Agnieszka M Piatkowska. (2015a).
'Multiple roles of timing
in somite formation.' Seminars in cell &
developmental biology vol. 42: 134-139. doi:10.1016/j.semcdb.2015.06.002"""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 1)
        self.assertNotIn("\n", references[0])
        self.assertIn("Multiple roles of timing in somite formation", references[0])
        self.assertIn("Seminars in cell & developmental biology", references[0])
        self.assertIn("Multiple roles of timing in somite formation", core.extract_title(references[0]))

    def test_adjacent_wrapped_references_split_without_blank_line(self) -> None:
        pasted = """Stern, Claudio D, and Agnieszka M Piatkowska. (2015a).
'Multiple roles of timing in somite formation.'
Seminars in cell & developmental biology vol. 42: 134-139.
doi:10.1016/j.semcdb.2015.06.002
Stern, Claudio. (2015b). 'The embryo reunited with its membranes in Göttingen.'
Development vol. 142(16): 2727-2729.
doi:10.1242/dev.124719"""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 2)
        self.assertIn("2015a", references[0])
        self.assertIn("2015b", references[1])
        self.assertIn("10.1016/j.semcdb.2015.06.002", references[0])
        self.assertIn("10.1242/dev.124719", references[1])

    def test_blank_line_inside_reference_is_soft(self) -> None:
        pasted = """Stern, Claudio. (2015b). 'The embryo reunited

with its membranes in Göttingen.' Development vol. 142(16),
pp. 2727-2729. doi:10.1242/dev.124719"""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 1)
        self.assertIn("The embryo reunited with its membranes", references[0])

    def test_numbered_references_can_split_author_and_year_across_lines(self) -> None:
        pasted = """1. Stern, Claudio D, and Agnieszka M Piatkowska.
(2015a). 'Multiple roles of timing in somite formation.'
Seminars in cell & developmental biology vol. 42: 134-139.
2. Stern, Claudio.
(2015b). 'The embryo reunited with its membranes in Göttingen.'
Development vol. 142(16): 2727-2729."""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 2)
        self.assertTrue(references[0].startswith("Stern, Claudio D"))
        self.assertTrue(references[1].startswith("Stern, Claudio."))
        self.assertEqual(core.extract_year(references[0]), "2015a")
        self.assertEqual(core.extract_year(references[1]), "2015b")

    def test_ou_references_on_adjacent_lines_remain_separate(self) -> None:
        pasted = """The Open University (2025a) '1.2 What are clouds?'. S111: Questions in science. Available at: https://learn2.open.ac.uk/mod/oucontent/view.php?id=2409631&section=3 (Accessed: 19 August 2025).
The Open University (2025b) '1.3.1 Snow and ice'. S111: Questions in science. Available at: https://learn2.open.ac.uk/mod/oucontent/view.php?id=2409631&section=4.1 (Accessed: 19 August 2025)."""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 2)
        self.assertEqual(core.extract_year(references[0]), "2025a")
        self.assertEqual(core.extract_year(references[1]), "2025b")

    def test_blank_line_before_title_continuation_does_not_create_reference(self) -> None:
        pasted = """Example, A. (2020). 'Studies of change:

Development, 2015 and beyond.' Journal of Examples, 4(2), pp. 10-20."""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 1)
        self.assertIn("Development, 2015 and beyond", references[0])

    def test_incomplete_reference_does_not_swallow_next_strong_paragraph(self) -> None:
        pasted = """Ben-Zeev T, Ratzon R, Sivan M, Sadeh. The effect of exercise on neurogenesis in the brain.

Ratey, J.J. and Loehr, J.E. (2011). The power of exercise. New York: Penguin."""

        references = core.split_references(pasted)

        self.assertEqual(len(references), 2)
        self.assertTrue(references[0].startswith("Ben-Zeev"))
        self.assertTrue(references[1].startswith("Ratey"))

    def test_carriage_returns_and_available_at_lines_are_joined(self) -> None:
        pasted = (
            "World Health Organization (2024) Example report.\r"
            "Available at: https://www.who.int/example\r"
            "(Accessed: 1 January 2025)."
        )

        references = core.split_references(pasted)

        self.assertEqual(len(references), 1)
        self.assertNotIn("\r", references[0])
        self.assertIn("Available at: https://www.who.int/example", references[0])


if __name__ == "__main__":
    unittest.main()
