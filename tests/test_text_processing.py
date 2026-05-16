from __future__ import annotations

import unittest

from second_brain.text_processing import (
    chunk_text_by_units,
    contains_cjk,
    count_chunk_units,
    cjk_ngrams,
    prepare_fts_text,
    query_terms,
)


class TextProcessingTest(unittest.TestCase):
    def test_cjk_detection_and_ngrams(self) -> None:
        self.assertTrue(contains_cjk("系统支持中文检索和嵌入"))
        terms = cjk_ngrams("系统支持中文检索和嵌入", min_n=1, max_n=3)
        self.assertIn("中文", terms)
        self.assertIn("支持", terms)
        self.assertIn("检索", terms)
        self.assertIn("中文检", terms)

    def test_query_terms_keep_cjk_short_terms(self) -> None:
        self.assertEqual(query_terms("中文 支持 检索"), ["中", "文", "中文", "支", "持", "支持", "检", "索", "检索"])
        mixed = query_terms("embedding layer 支持中文吗")
        self.assertIn("embedding", mixed)
        self.assertIn("layer", mixed)
        self.assertIn("中文", mixed)
        self.assertIn("支持", mixed)

    def test_prepare_fts_text_adds_search_terms_without_replacing_original(self) -> None:
        text = prepare_fts_text(["系统支持中文检索和嵌入"])
        self.assertIn("系统支持中文检索和嵌入", text)
        self.assertIn("中文", text)
        self.assertIn("检索", text)

    def test_chunk_text_by_units_preserves_original_text(self) -> None:
        chunks = chunk_text_by_units("系统支持中文检索和嵌入", chunk_units=4, overlap_units=1)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0], "系统支持")
        self.assertNotIn("系 统", chunks[0])
        self.assertEqual(count_chunk_units("hello 中文"), 3)


if __name__ == "__main__":
    unittest.main()
