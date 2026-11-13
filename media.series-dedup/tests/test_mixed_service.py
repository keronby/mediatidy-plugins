import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from series_core.mixed_service import preview, selected_rule
from test_storage_snapshot import MemoryKV

class MixedService(unittest.TestCase):
    def test_selected_rule_requires_identity_and_nonempty_scoring(self):
        sdk = NS(rules=NS(get=Mock(return_value={'id':'q','family':'quality','enabled':True,'definition':{'scoring':{}}})))
        with self.assertRaises(ValueError): selected_rule(sdk, 'q')
        sdk.rules.get.return_value['definition']['scoring']={'resolution':{'weight':20}}
        sdk.rules.get.return_value['definition']['version']={'unmatched':'best','slots':[{'name':'HD'}]}
        self.assertEqual(selected_rule(sdk,'q')['slots'][0]['name'], 'HD')
        with self.assertRaises(ValueError): selected_rule(sdk,'other')

    def test_preview_persists_only_plan_and_never_mutates_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            dirs=[]
            for n, line in [('a','https://test/a.mkv'),('b','/cloud/a.mkv')]:
                p=Path(temp,n);p.mkdir();(p/'Show.S01E01.1080p.strm').write_text(line)
                dirs.append({'dir_path':str(p)})
            kv=MemoryKV()
            mt=NS(storage=kv, config=NS(get=lambda:{'rootBindings':[{'rootId':'r','mountPath':temp}]}),
                  files=NS(roots=lambda:[{'id':'r','read':True}],stat=Mock(return_value={'isDir':True})),
                  rules=NS(get=Mock(return_value={'id':'q','family':'quality','enabled':True,'revision':'v1',
                    'definition':{'scoring':{'resolution':{'weight':20,'priority':['1080p']}},'version':{'unmatched':'best'}}})))
            result=preview(NS(mt=mt),{'media_type':'tv','tmdb_id':1,'dirs':dirs},'q')
            self.assertTrue(result['planning_only']);self.assertEqual(result['losers'],1)
            self.assertIn(result['recordKey'],kv.data)
            self.assertFalse(any(k.startswith('scans/') for k in kv.data))
            self.assertEqual(len(list(Path(temp).rglob('*.strm'))),2)

class ConfiguredRule(unittest.TestCase):
    def test_profile_selected_common_rule_without_guessed_default(self):
        from series_core.mixed_service import configured_rule
        sdk=NS(rules=NS(effective=Mock(return_value={'selection':{'qualityRuleId':'q'}}),
            get=Mock(return_value={'id':'q','family':'quality','enabled':True,'definition':{'scoring':{'resolution':{'weight':20}}}})))
        self.assertEqual(configured_rule(sdk, {'localProfileId':1,'cloudProfileId':2})['entry_id'],'q')
        sdk.rules.effective.side_effect=[{'selection':{'qualityRuleId':'q'}},{'selection':{'qualityRuleId':'different'}}]
        with self.assertRaises(ValueError):configured_rule(sdk, {'localProfileId':1,'cloudProfileId':2})

    def test_missing_profile_rule_cannot_silently_use_other_profile(self):
        from series_core.mixed_service import configured_rule
        sdk=NS(rules=NS(effective=Mock(side_effect=[{'selection':{'qualityRuleId':'q'}},{'selection':{}}]),get=Mock()))
        with self.assertRaises(ValueError):configured_rule(sdk,{'localProfileId':1,'cloudProfileId':2})
        sdk.rules.get.assert_not_called()

    def test_advanced_rule_selection_overrides_profiles(self):
        from series_core.mixed_service import configured_rule
        sdk=NS(rules=NS(effective=Mock(side_effect=AssertionError('must use selected rule')),
            get=Mock(return_value={'id':'selected','family':'quality','enabled':True,
                'definition':{'scoring':{'resolution':{'weight':20}},
                    'min_score_diff':9999,'version':{'slots':[{'name':'HD'},{'name':'UHD'}]}}})))
        rule=configured_rule(sdk,{'qualityRuleId':'selected'})
        self.assertEqual(len(rule['slots']),2)
        self.assertEqual(rule['entry_id'],'selected')
        sdk.rules.effective.assert_not_called()
