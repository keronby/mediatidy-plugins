import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import plugin

class ClearProtection(unittest.TestCase):
    def test_clear_removes_auto_no_year_but_preserves_manual_and_job_records(self):
        protected={'auto':{'auto_added':True},'manual':{'auto_added':False},'legacy':{}}
        with patch.object(plugin,'_get_protected_no_year',return_value=protected), \
             patch.object(plugin,'_save_protected_no_year') as save, \
             patch.object(plugin,'_save_state_split') as snapshot:
            result=plugin.Plugin().clear({},NS())
        save.assert_called_once_with(NS(),{'manual':protected['manual'],'legacy':protected['legacy']})
        self.assertEqual(result['protected_links_count'],2)
        self.assertEqual(snapshot.call_args.kwargs['full_groups'],[])
