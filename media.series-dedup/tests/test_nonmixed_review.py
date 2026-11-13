"""Non-mixed action guard regressions; no real filesystem or host mutation."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import plugin as p

class NonmixedReview(unittest.TestCase):
    def test_current_protection_blocks_all_organize_before_planning(self):
        row={'local_path':'/local/work','tmdb_id':42,'media_type':'movie','link_type':'http'}
        for protections in ({'movie:42':{}},{'/local/work':{}},
                            {'legacy':{'local_path':'/local/work'}}):
            with patch.object(p,'_get_protected_no_year',return_value=protections), \
                 patch('series_core.organizer.plan') as plan:
                with self.assertRaisesRegex(ValueError,'保护组'):
                    p.Plugin()._start_organize([row],NS())
                plan.assert_not_called()

    def test_current_protection_blocks_delete_before_runtime_or_submission(self):
        row={'local_path':'/local/work','tmdb_id':'42','media_type':'movie','link_type':'http'}
        with patch.object(p,'_get_protected_no_year',return_value={'movie:42':{}}), \
             patch.object(p,'_get_state',return_value={'share_links':[row]}), \
             patch.object(p,'_execute_delete_entry') as delete:
            with self.assertRaisesRegex(ValueError,'保护组'):
                p.Plugin()._start_delete(['/local/work'],'http',NS())
            delete.assert_not_called()

    def test_movie_protection_does_not_block_tv_same_id(self):
        with patch.object(p,'_get_protected_no_year',return_value={'movie:42':{}}):
            p._assert_unprotected_rows(NS(),[{'media_type':'tv','tmdb_id':'42','dir_path':'/tv/work'}])

    def test_later_invalid_row_prevents_all_submissions(self):
        from series_core import organizer
        rows=[{'local_path':'/local/a','link_type':'http','media_type':'movie','tmdb_id':42},
              {'local_path':'/local/b','link_type':'http','media_type':'movie','tmdb_id':0}]
        mt=NS(runtime=NS(info=lambda:{'limits':{'maxWorkflowFiles':100}}))
        with patch.object(organizer,'collect_files',return_value=[{'ref':'f','name':'a.strm'}]), \
             patch.object(organizer,'start') as start:
            with self.assertRaises(ValueError):
                organizer.plan(NS(mt=mt),rows,{'localProfileId':1},[{'id':'r','path':'/local'}])
            start.assert_not_called()

    def test_later_page_error_never_returns_partial_files(self):
        from series_core.organizer import collect_files
        from unittest.mock import Mock
        listing=Mock(side_effect=[{'items':[{'name':'a.strm','ref':'f'}],'hasMore':True},
                                  RuntimeError('PERMISSION_DENIED')])
        with self.assertRaisesRegex(RuntimeError,'PERMISSION_DENIED'):
            collect_files(NS(files=NS(list=listing)),[{'id':'r','path':'/local'}],
                          '/local/work',False,{'remaining':100,'list_calls':0})
