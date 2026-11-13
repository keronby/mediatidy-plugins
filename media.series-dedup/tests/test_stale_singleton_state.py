import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import plugin

class SnapshotRead(unittest.TestCase):
    def test_legacy_singleton_rows_hidden_by_authoritative_full_groups(self):
        state={'share_links':[{'local_path':'/one'}],
               'local_links':[{'local_path':'/two'}],
               'mixed_links':[{'media_type':'movie','tmdb_id':'9','all_paths_text':'/one'}],
               'share_links_count':1,'local_links_count':1,'mixed_links_count':1}
        def read(ctx,key,default=None):
            if key==plugin.STORAGE_KEY_STATE:return {'share_links_count':1,'local_links_count':1,'mixed_links_count':1}
            if key==plugin.STORAGE_KEY_SHARE:return state['share_links']
            if key==plugin.STORAGE_KEY_LOCAL:return state['local_links']
            if key==plugin.STORAGE_KEY_MIXED:return state['mixed_links']
            return default
        with patch.object(plugin,'read_value',side_effect=read), \
             patch.object(plugin,'_get_full_groups',return_value=[]), \
             patch.object(plugin,'_get_protected_no_year',return_value={}):
            result=plugin._get_state(NS(),pages={})
        self.assertEqual(result['share_links'],[])
        self.assertEqual(result['local_links'],[])
        self.assertEqual(result['mixed_links'],[])
        self.assertEqual(result['mixed_links_count'],0)
