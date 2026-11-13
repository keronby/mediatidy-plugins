import json
from pathlib import Path
import tempfile
import unittest
from series_core.mixed import plan_group

RULE = {'entry_id': 'explicit-test-rule', 'scoring': {'resolution': {'weight': 20,
         'priority': ['2160p','1080p','720p']}}, 'unmatched': 'best', 'min_score_diff': 9999}

class MixedPlan(unittest.TestCase):
    def run_plan(self, root, files, media_type='tv', rule=None):
        dirs=[]
        for dirname, name, target in files:
            path=Path(root, dirname); path.mkdir(exist_ok=True)
            (path/name).write_text(target)
            if str(path) not in [x['dir_path'] for x in dirs]:
                dirs.append({'dir_path':str(path)})
        return plan_group({'media_type':media_type, 'dirs':dirs}, RULE if rule is None else rule)

    def test_equal_score_prefers_http(self):
        with tempfile.TemporaryDirectory() as root:
            result=self.run_plan(root,[('a','Show.S01E01.1080p.strm','/mount/a.mkv'),
                                       ('b','Show.S01E01.1080p.strm','https://example/b.mkv')])
            self.assertEqual(result['winners'][0]['link_kind'],'http')
            self.assertEqual(len(result['losers']),1)
            self.assertFalse(result['chaos'])
            json.dumps(result)
            self.assertEqual(len(list(Path(root).rglob('*.strm'))),2)

    def test_unknown_episode_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            result=self.run_plan(root,[('a','Show.1080p.strm','/mount/a.mkv'),
                                       ('b','Show.2160p.strm','https://example/b.mkv')])
            self.assertEqual(len(result['skipped']),2)
            self.assertEqual(result['losers'],[])
            self.assertTrue(result['chaos'])

    def test_quality_wins_without_minimum_gap_gate(self):
        with tempfile.TemporaryDirectory() as root:
            result=self.run_plan(root,[('a','Show.S01E01.2160p.strm','/mount/a.mkv'),
                                       ('b','Show.S01E01.1080p.strm','https://example/b.mkv')])
            self.assertEqual(result['winners'][0]['link_kind'],'cloudnas')
            self.assertEqual(len(result['losers']),1)

    def test_different_slots_never_compete(self):
        rule=dict(RULE,slots=[{'name':'iso','match':{'file_extension':['iso']}},
                              {'name':'standard','match':{'file_extension':['mkv']}}])
        with tempfile.TemporaryDirectory() as root:
            result=self.run_plan(root,[('a','Movie.2160p.strm','/mount/a.iso'),
                                       ('b','Movie.1080p.strm','https://example/b.mkv')], 'movie', rule)
            self.assertEqual(len(result['winners']),2)
            self.assertEqual(result['losers'],[])
            self.assertTrue(result['chaos'])

    def test_empty_rule_rejected(self):
        with self.assertRaises(ValueError):
            plan_group({'dirs':[]}, {})

    def test_parser_fallback_keeps_special_season_zero(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from series_core.mixed import collect_entries
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'Special.1080p.strm').write_text('https://example/video.mkv')
            with patch('series_core.quality.parser.MediaParser.parse',return_value=NS(season=0,episode=1)):
                entries=collect_entries(root,'tv')
            self.assertEqual(entries[0]['episode'],'S00E01')
