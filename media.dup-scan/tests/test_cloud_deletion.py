"""Only confirmed per-item completion may remove a cloud result."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'mediatidy-plugin-development' / 'references' / 'sdk'))
from plugin import Plugin


class CloudDeletionTests(unittest.TestCase):
    def context(self, items):
        self.state = {'recordKey': 'scans/run/groups/0001', 'taskRef': 'opaque',
                      'plan': [{'operation': 'delete'}], 'indexPlan': [], 'phase': 0}
        storage = SimpleNamespace(get=Mock(return_value={'found': True, 'value': self.state}),
                                  set=Mock(), delete=Mock())
        tasks = SimpleNamespace(items=Mock(return_value={'items': items}),
                                get=Mock(return_value={'status': 'done'}))
        return SimpleNamespace(mt=SimpleNamespace(storage=storage, tasks=tasks,
                                                  runtime=SimpleNamespace(progress=Mock())))

    def test_failed_item_keeps_record(self):
        ctx = self.context([{'index': 0, 'status': 'failed', 'error': {'message': 'denied'}}])
        result = Plugin().delete_collect({'operation': 'delete-operation'}, ctx)
        self.assertFalse(result['ok'])
        ctx.mt.storage.delete.assert_not_called()

    def test_missing_item_keeps_record(self):
        ctx = self.context([])
        self.assertFalse(Plugin().delete_collect({'operation': 'delete-operation'}, ctx)['ok'])
        ctx.mt.storage.delete.assert_not_called()

    def test_confirmed_success_removes_record(self):
        ctx = self.context([{'index': 0, 'status': 'done'}])
        self.assertTrue(Plugin().delete_collect({'operation': 'delete-operation'}, ctx)['ok'])
        ctx.mt.storage.delete.assert_called_once_with(key='scans/run/groups/0001')

    def test_companions_finish_before_index_submission(self):
        ctx = self.context([{'index': 0, 'status': 'done'}])
        self.state['indexPlan'] = [{'operation': 'delete', 'fileRef': 'index'}]
        plugin = Plugin()
        plugin._submit_delete = Mock(return_value={'waiting': True})
        result = plugin.delete_collect({'operation': 'delete-operation'}, ctx)
        self.assertTrue(result['waiting'])
        self.assertEqual(self.state['plan'], [{'operation': 'delete', 'fileRef': 'index'}])
        self.assertEqual(self.state['phase'], 1)
        ctx.mt.storage.delete.assert_not_called()
