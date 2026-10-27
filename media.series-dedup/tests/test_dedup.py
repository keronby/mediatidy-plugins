import os
import sys
import unittest

# 将插件目录加入路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.scanner import clean_title_invisible_chars, parse_media_dir, analyze_strm_files
from core.matcher import evaluate_dir_match, match_duplicate_groups

class TestSeriesDedup(unittest.TestCase):

    def test_clean_title_invisible_chars(self):
        t1 = "凡人修仙传\u200b"
        t2 = "凡人修仙传\ufeff"
        t3 = "凡人修仙传"
        self.assertEqual(clean_title_invisible_chars(t1), clean_title_invisible_chars(t3))
        self.assertEqual(clean_title_invisible_chars(t2), clean_title_invisible_chars(t3))

    def test_parse_media_dir(self):
        d1 = "茶之味 (2004) {tmdb-12345}"
        info1 = parse_media_dir(d1)
        self.assertIsNotNone(info1)
        self.assertEqual(info1["local_title"], "茶之味")
        self.assertEqual(info1["year"], 2004)
        self.assertEqual(info1["tmdb_id"], 12345)

        d2 = "凡人修仙传 [tmdbid:67890]"
        info2 = parse_media_dir(d2)
        self.assertIsNotNone(info2)
        self.assertEqual(info2["local_title"], "凡人修仙传")
        self.assertEqual(info2["tmdb_id"], 67890)

    def test_evaluate_dir_match_equal(self):
        item = {
            "local_title": "凡人修仙传",
            "year": 2020,
            "tmdb_id": 1001,
        }
        is_matched, need_org, diff = evaluate_dir_match(item, "凡人修仙传", 2020)
        self.assertTrue(is_matched)
        self.assertFalse(need_org)
        self.assertEqual(diff, "")

    def test_evaluate_dir_match_diff_title(self):
        item = {
            "local_title": "凡人修仙传动画版",
            "year": 2020,
            "tmdb_id": 1001,
        }
        is_matched, need_org, diff = evaluate_dir_match(item, "凡人修仙传", 2020)
        self.assertFalse(is_matched)
        self.assertTrue(need_org)
        self.assertIn("片名不符", diff)

    def test_evaluate_dir_match_diff_year(self):
        item = {
            "local_title": "茶之味",
            "year": 2003,
            "tmdb_id": 2002,
        }
        is_matched, need_org, diff = evaluate_dir_match(item, "茶之味", 2004)
        self.assertFalse(is_matched)
        self.assertTrue(need_org)
        self.assertIn("年份不符", diff)

if __name__ == "__main__":
    unittest.main()
