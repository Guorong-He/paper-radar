import unittest

from paper_radar.fulltext import extract_abstract_from_fulltext


class FulltextTests(unittest.TestCase):
    def test_extracts_missing_abstract_from_pdf_text(self):
        fulltext = (
            "Article title\nAbstract\n"
            "This study introduces a robust embodied sensing system and validates it in real robots. "
            "The system suppresses interference at the sensor and improves closed-loop control.\n"
            "Introduction\nThe rest of the paper."
        )

        abstract = extract_abstract_from_fulltext(fulltext)

        self.assertTrue(abstract.startswith("This study introduces"))
        self.assertNotIn("Introduction", abstract)


if __name__ == "__main__":
    unittest.main()
