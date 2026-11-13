"""Local scans must remain independent of MT file limits and root grants."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from series_core.local_source import scan_path, saved_path
from series_core.scanner import scan_media_library


class LocalScan(unittest.TestCase):
    def test_more_than_500_works_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            for i in range(601):
                work = Path(temp, 'Work%d {tmdb-%d}' % (i, i + 1))
                work.mkdir()
                (work / 'film.strm').write_text('https://example.invalid/video.mkv')
                duplicate = Path(temp, 'Other%d {tmdb-%d}' % (i, i + 1))
                duplicate.mkdir()
                (duplicate / 'film.strm').write_text('https://example.invalid/other.mkv')
            result = scan_media_library([temp])
            self.assertEqual(result['recognized_works'], 1202)
            self.assertEqual(len(result['groups']), 601)
            self.assertEqual(result['scan_errors'], [])

    def test_local_path_compatibility_does_not_guess_prefixes(self):
        self.assertEqual(scan_path('/mount/library/../films'), '/mount/films')
        self.assertEqual(saved_path({'rootPath': '/old/library'}), '/old/library')
        self.assertEqual(saved_path({'scanDirectory': {'rootId': 'r', 'path': '.'}}), '')
        for invalid in ['', 'relative/path', {'rootId': 'r', 'path': '.'}]:
            with self.assertRaises(ValueError):
                scan_path(invalid)

    def test_scan_action_never_uses_host_files_or_mtc(self):
        try:
            import plugin
        except ImportError:
            self.skipTest('SDK development environment required')
        with tempfile.TemporaryDirectory() as temp:
            # Any unplanned host access (files, config, rules, secrets) fails.
            ctx = NS(mt=NS(log=lambda **kwargs: None))
            with patch.object(plugin, '_get_config', return_value={'tmdb_style':'style_curly'}), \
                 patch.object(plugin, '_get_state', return_value={}), \
                 patch.object(plugin, '_save_state_split'), \
                 patch.object(plugin, '_get_protected_no_year', return_value={}), \
                 patch.object(plugin, '_save_protected_no_year'), \
                 patch.object(plugin, 'query_mt_tmdb_caches', return_value={}):
                result = plugin.Plugin().scan({'scanPath':temp}, ctx)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['recognized_works'], 0)

    def test_single_work_mixed_contents_is_outside_media_dedup(self):
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp,'Movie (2020) {tmdb-123}');work.mkdir()
            (work/'Movie.1080p.strm').write_text('https://example/video.mkv')
            (work/'Movie.2160p.strm').write_text('/cloud/video.mkv')
            result=scan_media_library([temp])
            self.assertEqual(result['groups'],[])
            # A compliant non-mixed singleton remains outside attention results.
            (work/'Movie.2160p.strm').unlink()
            self.assertEqual(scan_media_library([temp])['groups'],[])
