"""Regression: matching episode/movie names in different folders are not duplicates."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'mediatidy-plugin-development' / 'references' / 'sdk'))
from plugin import Plugin, _work_dir_of, _evaluate_works


class SameDirectoryTests(unittest.TestCase):
    def test_tmdb_parent_does_not_merge_subdirectories(self):
        a = _work_dir_of('Show (2024) [tmdbid-1]/Season A/Show.S01E01.1080p.strm')
        b = _work_dir_of('Show (2024) [tmdbid-1]/Season B/Show.S01E01.2160p.strm')
        self.assertNotEqual(a, b)
        self.assertEqual(a, 'Show (2024) [tmdbid-1]/Season A')
        self.assertEqual(_work_dir_of('movie.strm'), '.')

    def test_different_directories_never_form_candidate_bucket(self):
        works = {}
        for rel in ['Movie [tmdbid-1]/A/Movie.1080p.strm', 'Movie [tmdbid-1]/B/Movie.2160p.strm']:
            works.setdefault(_work_dir_of(rel), []).append({'rel': rel, 'name': rel.rsplit('/', 1)[-1]})
        with patch('plugin._evaluate_prepared', return_value=[]) as evaluate:
            _evaluate_works(None, works, {})
        self.assertEqual(evaluate.call_args.args[1], {})

    def test_page_has_no_search_pagination_iso_or_extra_sources(self):
        source = (Path(__file__).resolve().parents[1] / 'plugin.py').read_text()
        for label in ['搜索作品', '结果页码', 'ISO 模式', '附加扫描目录', 'state.search', 'state.page']:
            self.assertNotIn(label, source)

    def test_only_duplicate_candidates_have_content_read(self):
        works = {
            'Movie': [{'rel': 'Movie/Movie.1080p.strm', 'name': 'Movie.1080p.strm'},
                      {'rel': 'Movie/Movie.2160p.strm', 'name': 'Movie.2160p.strm'}],
            'Single': [{'rel': 'Single/Single.1080p.strm', 'name': 'Single.1080p.strm'}],
        }
        with patch('plugin._read_first_line', side_effect=['https://example.com/a.mkv', '/CloudNAS/b.mkv']) as read:
            groups = _evaluate_works(None, works, {})
        self.assertEqual(read.call_count, 2)
        self.assertEqual({c.args[1]['rel'] for c in read.call_args_list},
                         {'Movie/Movie.1080p.strm', 'Movie/Movie.2160p.strm'})
        self.assertEqual(len(groups), 1)
        self.assertEqual({f['strmKind'] for f in groups[0]['files']}, {'http', 'local'})
