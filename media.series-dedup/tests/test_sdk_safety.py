"""Offline safety tests; never accesses a media library or running host."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from series_core import deleter

SOURCE = Path(__file__).resolve().parents[1] / 'plugin.py'


def load_helpers():
    tree = ast.parse(SOURCE.read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and
                 n.name in {'_storage_get_safe', '_remove_entry_from_state'}]
    from typing import Any
    from series_core.storage import read_value
    env = {'Context': object, 'Any': Any, 'Dict': dict, 'logger': Mock(), 'read_value': read_value}
    exec(compile(tree, str(SOURCE), 'exec'), env)
    return env


def load_plugin_methods():
    tree = ast.parse(SOURCE.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Plugin')
    cls.bases = []
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and
                n.name in {'_start_delete', 'collect_delete'}]
    for n in cls.body:
        n.decorator_list = []
    tree.body = [cls]
    from typing import Any, Dict, List
    env = {'Context': object, 'Any': Any, 'Dict': Dict, 'List': List,
           '_assert_unprotected_rows': Mock(), '_execute_delete_entry': Mock(), '_remove_entry_from_state': Mock(),
           '_storage_get_safe': load_helpers()['_storage_get_safe'],
           '_get_state': lambda ctx: {'share_links':[{'local_path':'/library/movie','link_type':'http'}]}}
    exec(compile(tree, str(SOURCE), 'exec'), env)
    return env


class StorageSafety(unittest.TestCase):
    def test_found_distinguishes_all_empty_values(self):
        get = load_helpers()['_storage_get_safe']
        ctx = SimpleNamespace(mt=SimpleNamespace(storage=Mock()))
        for value in (None, False, 0, '', [], {}):
            ctx.mt.storage.get.return_value = {'found': True, 'value': value}
            self.assertEqual(get(ctx, 'x', 'default'), value)
        ctx.mt.storage.get.return_value = {'found': False}
        self.assertEqual(get(ctx, 'x', 'default'), 'default')

    def test_read_failure_is_not_empty(self):
        get = load_helpers()['_storage_get_safe']
        ctx = SimpleNamespace(mt=SimpleNamespace(storage=Mock()))
        error = RuntimeError('PERMISSION_DENIED')
        ctx.mt.storage.get.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            get(ctx, 'x', [])
        self.assertIs(caught.exception, error)


class DeleteSafety(unittest.TestCase):
    def test_native_entry_disabled(self):
        with self.assertRaisesRegex(RuntimeError, '原生删除入口已停用'):
            deleter.delete_media_entry('/anything')

    def test_stat_permission_failure_never_success(self):
        sdk = SimpleNamespace(files=Mock())
        error = RuntimeError('PERMISSION_DENIED')
        sdk.files.stat.side_effect = error
        with patch('series_core.submitter.get_roots', return_value=[{'id': 'real', 'path': '/library'}]):
            with self.assertRaises(RuntimeError) as caught:
                deleter._sdk_delete_path(sdk, '/library/电影/作品 {tmdb-1}')
        self.assertIs(caught.exception, error)
        sdk.files.delete.assert_not_called()

    def test_wait_has_no_followup_sdk_and_does_not_prune(self):
        env = load_plugin_methods()
        mt = SimpleNamespace(storage=Mock(), runtime=Mock(), tasks=Mock())
        mt.storage.get.return_value = {'found': False}
        mt.runtime.info.return_value = {'limits': {'maxWorkflowFiles': 500}}
        ctx = SimpleNamespace(mt=mt, flow_id='offline')
        env['_execute_delete_entry'].return_value = {'status': 'queued', 'ref': 'host-ref'}
        # Any call after tasks.wait would violate FLOW_WAITING.
        def waiting(**kwargs):
            mt.storage.set.side_effect = AssertionError('SDK after wait')
        mt.tasks.wait.side_effect = waiting
        result = env['Plugin']()._start_delete(['/library/movie'], 'http', ctx)
        self.assertEqual(result['status'], 'waiting')
        env['_remove_entry_from_state'].assert_not_called()
        mt.tasks.wait.assert_called_once_with(refs=['host-ref'], completion={
            'action': 'collect-delete', 'input': {'key': 'deletions/offline'}})

    def test_collect_prunes_only_confirmed_done(self):
        env = load_plugin_methods()
        record = {'link_type': 'http', 'items': [
            {'path': '/a', 'task_ref': 'a'}, {'path': '/b', 'task_ref': 'b'},
            {'path': '/c', 'task_ref': 'c'}, {'path':'/d','task_ref':'d'}]}
        mt = SimpleNamespace(storage=Mock(), tasks=Mock())
        mt.storage.get.return_value = {'found': True, 'value': record}
        mt.tasks.get.side_effect = [
            {'status': 'done', 'failed': 0}, {'status': 'outcome_unknown'},
            {'status': 'done', 'failed': 1}, {'status':'done'}]
        result = env['Plugin']().collect_delete({'key': 'deletions/offline'}, SimpleNamespace(mt=mt))
        env['_remove_entry_from_state'].assert_called_once_with(unittest.mock.ANY, '/a', 'http')
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['status'], 'needs_review')

    def test_duplicate_flow_never_resubmits(self):
        env = load_plugin_methods()
        mt = SimpleNamespace(storage=Mock(), runtime=Mock())
        mt.storage.get.return_value = {'found': True, 'value': {'status': 'submitting'}}
        mt.runtime.info.return_value = {}
        with self.assertRaises(ValueError):
            env['Plugin']()._start_delete(['/a'], 'http', SimpleNamespace(mt=mt, flow_id='x'))
        env['_execute_delete_entry'].assert_not_called()


class StrictWrite(unittest.TestCase):
    def test_oversize_is_not_truncated(self):
        from series_core.storage import write_value
        ctx = SimpleNamespace(mt=SimpleNamespace(storage=Mock()))
        values = ['x' * 100] * 40
        with self.assertRaises(ValueError):
            write_value(ctx, 'results', values, 1000)
        self.assertEqual(len(values), 40)
        ctx.mt.storage.set.assert_not_called()

    def test_storage_failure_propagates(self):
        from series_core.storage import write_value
        ctx = SimpleNamespace(mt=SimpleNamespace(storage=Mock()))
        error = RuntimeError('PERMISSION_DENIED')
        ctx.mt.storage.set.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            write_value(ctx, 'results', [])
        self.assertIs(caught.exception, error)


class CrossFlowDelete(unittest.TestCase):
    def test_previous_path_lock_blocks_new_flow(self):
        env = load_plugin_methods()
        mt = SimpleNamespace(storage=Mock(), runtime=Mock())
        mt.runtime.info.return_value = {}
        mt.storage.get.side_effect = [{'found': False}, {'found': True, 'value': {'recordKey': 'old'}}]
        with self.assertRaisesRegex(ValueError, '跨流程'):
            env['Plugin']()._start_delete(['/library/movie'], 'http', SimpleNamespace(mt=mt, flow_id='new'))
        env['_execute_delete_entry'].assert_not_called()
        mt.storage.set.assert_not_called()
