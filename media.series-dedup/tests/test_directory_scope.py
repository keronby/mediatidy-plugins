import tempfile
import unittest
from pathlib import Path
from series_core.scanner import scan_media_library
from series_core.mixed import plan_group
from test_mixed_plan import RULE

class DirectoryScope(unittest.TestCase):
    def make(self,root,category,name,files):
        work=Path(root,category,name);work.mkdir(parents=True)
        for filename,target in files:
            (work/filename).write_text(target)
        return {'dir_path':str(work)}

    def test_duplicate_root_selection_never_makes_singleton_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.make(root,'电影','Movie (2020) {tmdb-123}',[
                ('Movie.1080p.strm','https://example/a.mkv'),('Movie.2160p.strm','/cloud/b.iso')])
            self.assertEqual(scan_media_library([root,root,d['dir_path']])['groups'],[])
            with self.assertRaisesRegex(ValueError,'不同作品目录'):
                plan_group({'media_type':'movie','dirs':[d,d]},RULE)

    def test_distinct_dirs_same_tmdb_and_media_type_are_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            for category in ('电影/A','电影/B'):
                self.make(root,category,'Movie (2020) {tmdb-123}',[('Movie.strm','https://example/a.mkv')])
            result=scan_media_library(root)
            self.assertEqual(len(result['groups']),1)
            self.assertEqual(result['groups'][0]['dirs_count'],2)
            self.assertTrue(result['groups'][0]['is_duplicate'])

    def test_one_directory_per_slot_never_deletes_internal_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            a=self.make(root,'电影/A','Movie (2020) {tmdb-123}',[
                ('Movie.1080p.strm','https://example/a.mkv'),('Movie.2160p.strm','https://example/b.mkv')])
            b=self.make(root,'电影/B','Movie (2020) {tmdb-123}',[('Movie.1080p.strm','/cloud/b.iso')])
            rule=dict(RULE,slots=[{'name':'iso','match':{'file_extension':['iso']}},
                                  {'name':'standard','match':{'file_extension':['mkv']}}])
            result=plan_group({'media_type':'movie','dirs':[a,b]},rule)
            self.assertEqual(result['losers'],[])
            self.assertEqual(len(result['winners']),3)
            self.assertEqual({e['slot'] for e in result['entries']},{'iso','standard'})

    def test_rule_slots_applied_to_distinct_work_directories(self):
        with tempfile.TemporaryDirectory() as root:
            a=self.make(root,'电影/A','Movie (2020) {tmdb-123}',[('Movie.1080p.strm','https://example/a.mkv')])
            b=self.make(root,'电影/B','Movie (2020) {tmdb-123}',[('Movie.2160p.strm','/cloud/b.mkv')])
            rule=dict(RULE,slots=[{'name':'HD','match':{'resolution':['1080p']}},
                                  {'name':'UHD','match':{'resolution':['2160p']}}])
            result=plan_group({'media_type':'movie','dirs':[a,b]},rule)
            self.assertEqual(result['losers'],[])
            self.assertEqual({e['slot'] for e in result['entries']},{'hd','uhd'})
