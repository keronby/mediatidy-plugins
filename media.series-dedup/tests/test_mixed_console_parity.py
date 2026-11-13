"""Execute mt-console's actual score_group body with identical plugin inputs."""
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from series_core import mixed
from test_mixed_plan import RULE

CONSOLE = Path(os.environ.get('MT_CONSOLE_CORE', str(Path(__file__).resolve().parents[3] / 'mt-console-server/core'))) / 'mixed_link.py'

class ConsoleMixedParity(unittest.TestCase):
    def test_actual_console_bucket_decisions_match(self):
        if not CONSOLE.exists(): self.skipTest('mt-console reference checkout unavailable')
        source=ast.parse(CONSOLE.read_text())
        fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='score_group')
        # Replace only dependency and rule-loading prologue, retaining real bucket logic.
        start=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.For) and ast.unparse(n.target)=='e')
        body=fn.body[start:]
        from series_core.quality.scorer import QualityScorer
        for tv, names, rule in [
            (True,['Show.S01E01.1080p','Show.S01E01.1080p'],RULE),
            (True,['Show.S01E01.2160p','Show.S01E01.1080p','Show.S01E02.1080p'],RULE),
            (True,['Show.1080p','Show.2160p'],RULE),
            (False,['Movie.2160p','Movie.1080p'],RULE),
        ]:
            with self.subTest(tv=tv,names=names),tempfile.TemporaryDirectory() as root:
                dirs=[]
                for i,name in enumerate(names):
                    p=Path(root,str(i));p.mkdir();(p/(name+'.strm')).write_text('https://example/video.mkv' if i%2 else '/cloud/video.mkv')
                    dirs.append({'dir_path':str(p)})
                group={'media_type':'tv' if tv else 'movie','dirs':dirs}
                entries=[]
                for d in dirs:entries.extend(mixed.collect_entries(d['dir_path'],group['media_type']))
                f=ast.FunctionDef(name='reference',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body,decorator_list=[])
                scope={'entries':entries,'media_type':group['media_type'],'scorer':QualityScorer(rule,force_dedup=True),'rule':rule,'rules':{}}
                exec(compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),str(CONSOLE),'exec'),scope)
                expected=scope['reference']();actual=mixed.plan_group(group,rule)
                for key in ('chaos','remaining_link_types','buckets'):
                    self.assertEqual(expected[key],actual[key],key)
                for key in ('winners','losers','skipped'):
                    self.assertEqual([(e['strm_path'],e.get('score')) for e in expected[key]],[(e['strm_path'],e.get('score')) for e in actual[key]],key)
