"""Console rule parity and scan portability regressions; no real media changes."""
import ast
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from series_core import scanner, matcher, console_rules

CONSOLE = Path(os.environ.get('MT_CONSOLE_CORE', str(Path(__file__).resolve().parents[3] / 'mt-console-server/core')))


def console_reference():
    """Compile original pure functions, without console I/O/config imports."""
    namespace = dict(console_rules.__dict__)
    names = {'parse_media_dir', 'clean_title_invisible_chars', 'evaluate_dir_match',
             'reevaluate_group_dirs'}
    tree = ast.parse((CONSOLE / 'dedup_parse.py').read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(tree, 'console-reference', 'exec'), namespace)
    return namespace


class ConsoleParity(unittest.TestCase):
    def test_parser_matches_original(self):
        ref = console_reference()['parse_media_dir']
        for name in ['忠烈图 (1975) [tmdbid=76293]', '忠烈图.tmdb-76293',
                     '[douban=123][tmdbid=76293] 忠烈图 (1975)',
                     '{tmdb-76293}忠烈图', 'Avatar.The.Way.of.Water.2022.[tmdbid=76600]',
                     '忠烈图 TMDB 76293', '凡人修仙传\u200b (2020) {tmdb-1}', '未标注']:
            with self.subTest(name=name):
                expected = ref(name)
                actual = scanner.parse_media_dir(name)
                self.assertEqual(actual['is_valid'], expected is not None)
                if expected:
                    for field in ('title', 'year', 'tmdb_id'):
                        self.assertEqual(actual[field], expected[field])

    def test_matcher_matches_original(self):
        ref = console_reference()['evaluate_dir_match']
        for style in matcher.STYLE_IDS:
            for title, year in [('忠烈图', 1975), ('错误标题', 1975), ('忠烈图', None)]:
                item = dict(local_title='忠烈图', year=1974, tmdb_id=76293,
                            dir_name='忠烈图 (1974) {tmdb-76293}', category='华语电影')
                expected = ref(copy.deepcopy(item), title, year, matcher.STYLE_IDS[style])
                self.assertEqual(matcher.evaluate_dir_match(copy.deepcopy(item), title, year, style), expected)

    def test_no_invented_year(self):
        self.assertEqual(matcher.format_target_name('片名', None, 1), '')
        self.assertEqual(matcher.format_target_name('片名', None, 1, 'style_no_year'), '片名 {tmdb-1}')

    def test_category_difference_is_not_wrong_category(self):
        dirs = [dict(dir_name='忠烈图 (1975) {tmdb-1}', local_title='忠烈图', year=1975,
                     dir_path='/library/'+cat, category=cat, link_type='http')
                for cat in ['华语电影', '经典电影']]
        groups = [dict(tmdb_id=1, title='忠烈图', media_type='movie', dirs=dirs)]
        result = matcher.match_duplicate_groups(groups, {('movie', 1): dict(title='忠烈图', year=1975)})
        self.assertTrue(all(not d['need_organize'] for d in result[0]['dirs']))
        self.assertTrue(all(d['cat_status']=='unknown' for d in result[0]['dirs']))


class ScannerPortability(unittest.TestCase):
    def make_work(self, root, relative, link='https://example.org/video.mkv'):
        directory = Path(root, relative)
        directory.mkdir(parents=True)
        (directory / 'video.strm').write_text(link)
        return directory

    def test_scan_work_root_itself(self):
        with tempfile.TemporaryDirectory() as root:
            work=self.make_work(root, '电影/片名 tmdb-123')
            result=scanner.scan_media_library(str(work))
            self.assertEqual(result['recognized_works'], 1)
            self.assertEqual(result['groups'], [])

    def test_arbitrary_mount_deep_layout_and_unbracketed_tag(self):
        with tempfile.TemporaryDirectory(prefix='someone-else-') as root:
            self.make_work(root, 'a/b/c/d/e/f/g/电影/片名 tmdb-123')
            result=scanner.scan_media_library(root)
            self.assertEqual(result['recognized_works'], 1)
            self.assertGreater(result['visited_dirs'], 6)
            self.assertEqual(result['scan_errors'], [])

    def test_unlabelled_work_root_counted(self):
        with tempfile.TemporaryDirectory() as root:
            work=self.make_work(root, '无标签')
            result=scanner.scan_media_library(str(work))
            self.assertEqual(result['recognized_works'], 1)
            self.assertEqual(result['unidentified_count'], 1)

    def test_permission_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(scanner.os, 'listdir', side_effect=PermissionError('denied')):
                result=scanner.scan_media_library(root)
            self.assertIn('PermissionError', result['scan_errors'][0])
            self.assertEqual(result['visited_dirs'], 0)

    def test_missing_directory_and_depth_limit_are_reported(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(scanner.scan_media_library(str(Path(root, 'missing')))['scan_errors'])
            self.make_work(root, 'a/b/c/作品 {tmdb-123}')
            result=scanner.scan_media_library(root, max_depth=1)
            self.assertEqual(result['depth_limited_dirs'], 1)

    def test_ignore_paths_and_symlink_cycle(self):
        with tempfile.TemporaryDirectory() as root:
            work=self.make_work(root, '电影/作品 tmdb-123')
            Path(root, 'cycle').symlink_to(root, target_is_directory=True)
            result=scanner.scan_media_library(root, ignore_paths=[str(work)])
            self.assertEqual(result['recognized_works'], 0)
            self.assertFalse(result['scan_errors'])

    def test_movie_tv_same_id_separate(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_work(root, '电影/电影 tmdb-123')
            self.make_work(root, '电视剧/剧集 tmdb-123')
            self.make_work(root, '电影/第二目录 tmdb-123')
            self.make_work(root, '电视剧/第二目录 tmdb-123')
            result=scanner.scan_media_library(root)
            self.assertEqual({g['media_type'] for g in result['groups']}, {'movie', 'tv'})

    def test_season_and_extras_not_separate_works(self):
        with tempfile.TemporaryDirectory() as root:
            work=self.make_work(root, '电视剧/作品/Season 1')
            (work.parent / 'Extras').mkdir()
            result=scanner.scan_media_library(root)
            self.assertEqual(result['recognized_works'], 1)
            self.assertEqual(result['failed_dirs'][0]['dir_name'], '作品')

if __name__=='__main__':
    unittest.main()
