"""Regression tests for a second end-to-end code review; no real mutations."""
import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from series_core.submitter import to_mt_path

class Review(unittest.TestCase):
    def plugin(self):
        try:
            import plugin
        except ImportError:
            self.skipTest('SDK required')
        return plugin

    def test_cloud_mapping_only_uses_explicit_prefix(self):
        self.assertEqual(to_mt_path('/local/影视/作品',link_type='cloudnas',strm_local_prefix='/local',strm_cloud_prefix='/remote'),'/remote/影视/作品')
        for path in ['/strm/作品','/local-other/作品','/CloudNAS/作品']:
            with self.assertRaises(ValueError):
                to_mt_path(path,link_type='cloudnas',strm_local_prefix='/local',strm_cloud_prefix='/remote')
        with self.assertRaises(ValueError): to_mt_path('/local/作品',link_type='cloudnas')

    def test_organize_adapter_passes_root_objects_not_dictionary_keys(self):
        p=self.plugin();ctx=NS(mt=NS())
        with patch.object(p,'_get_protected_no_year',return_value={}), patch.object(p,'_get_config',return_value={}), \
             patch.object(p,'_known_roots_from_config',return_value={'real':'/local'}), \
             patch('series_core.organizer.plan',return_value=[]) as plan, \
             patch('series_core.organizer.start',return_value={'status':'simulated'}):
            p.Plugin()._start_organize([],ctx)
        self.assertEqual(plan.call_args.args[3],[{'id':'real','path':'/local'}])

    def test_delete_rejects_forged_or_protected_rows_before_host_calls(self):
        p=self.plugin();ctx=NS(mt=NS())
        for rows in [[],[{'local_path':'/x','link_type':'cloudnas'}],
                     [{'local_path':'/x','link_type':'http','disabled':True}]]:
            with patch.object(p,'_get_protected_no_year',return_value={}), patch.object(p,'_get_state',return_value={'share_links':rows}):
                with self.assertRaises(ValueError):p.Plugin()._start_delete(['/x'],'http',ctx)

    def test_prune_updates_full_groups_and_exact_path_only(self):
        p=self.plugin();ctx=NS()
        state={'share_links':[{'local_path':'/a/same'},{'local_path':'/b/same'}], 'local_links':[]}
        groups=[{'tmdb_id':1,'dirs':[{'dir_path':'/a/same'},{'dir_path':'/b/same'}]}]
        with patch.object(p,'_get_state',return_value=copy.deepcopy(state)), \
             patch.object(p,'_get_full_groups',return_value=groups),patch.object(p,'_save_state_split') as save:
            p._remove_entry_from_state(ctx,'/a/same','http')
        self.assertEqual(save.call_args.kwargs['full_groups'][0]['dirs'],[{'dir_path':'/b/same'}])
        self.assertEqual(save.call_args.kwargs['share_links'],[{'local_path':'/b/same'}])

    def test_delete_budget_rejects_before_writes(self):
        p=self.plugin();paths=['/work/'+str(i) for i in range(151)]
        rows=[{'local_path':path,'link_type':'http'} for path in paths]
        mt=NS(runtime=NS(info=lambda:{'limits':{'maxWorkflowFiles':500}}),storage=Mock())
        with patch.object(p,'_get_protected_no_year',return_value={}), patch.object(p,'_get_state',return_value={'share_links':rows}):
            with self.assertRaises(ValueError):p.Plugin()._start_delete(paths,'http',NS(mt=mt))
        mt.storage.set.assert_not_called()

    def test_old_mixed_snapshot_has_selectable_typed_keys_without_rescan(self):
        p=self.plugin()
        state={'mixed_links':[{'media_type':'movie','tmdb_id':'123','all_paths_text':'/a/movie\n/b/movie'},
                              {'media_type':'tv','tmdb_id':'123','all_paths_text':'/a/tv\n/b/tv'}]}
        with patch.object(p,'read_value',side_effect=lambda ctx,key,default=None: state if key==p.STORAGE_KEY_STATE else default), \
             patch.object(p,'_get_protected_no_year',return_value={}):
            # Legacy mixed array key is loaded separately from state meta.
            def read(ctx,key,default=None):
                return state['mixed_links'] if key==p.STORAGE_KEY_MIXED else default
            with patch.object(p,'read_value',side_effect=read):
                result=p._get_state(NS(),pages={})
        self.assertEqual([r['group_key'] for r in result['mixed_links']],['movie:123','tv:123'])
