"""Lossless snapshot publication and byte-bounded paging without any real SDK."""
import copy
from types import SimpleNamespace
import unittest
from series_core.storage import publish_snapshot, read_parts, read_page


class MemoryKV:
    def __init__(self):
        self.data = {}
        self.fail_part = False

    def get(self, *, key):
        return {'found': key in self.data, 'value': copy.deepcopy(self.data.get(key))}

    def set(self, *, key, value):
        if self.fail_part and key.startswith('scans/'):
            raise RuntimeError('QUOTA_EXCEEDED')
        self.data[key] = copy.deepcopy(value)


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.kv = MemoryKV()
        self.ctx = SimpleNamespace(mt=SimpleNamespace(storage=self.kv))

    def test_all_rows_survive_and_pages_are_complete(self):
        rows = [{'i': i, 'text': '中文' * 100} for i in range(501)]
        snapshot = publish_snapshot(self.ctx, 'current', {'status': 'done'}, {'groups': rows})
        descriptor = snapshot['datasets']['groups']
        self.assertGreater(len(descriptor['parts']), 1)
        self.assertEqual(read_parts(self.ctx, descriptor), rows)
        gathered, offset = [], 0
        while True:
            page = read_page(self.ctx, descriptor, offset)
            self.assertLessEqual(len(page['rows']), 20)
            gathered.extend(page['rows'])
            if page['next'] is None:
                break
            offset = page['next']
        self.assertEqual(gathered, rows)

    def test_failed_write_does_not_publish_or_destroy_old_result(self):
        old = publish_snapshot(self.ctx, 'current', {}, {'groups': [{'old': True}]})
        self.kv.fail_part = True
        with self.assertRaisesRegex(RuntimeError, 'QUOTA_EXCEEDED'):
            publish_snapshot(self.ctx, 'current', {}, {'groups': [{'new': True}]})
        self.assertEqual(self.kv.data['current'], old)
        self.assertEqual(read_parts(self.ctx, old['datasets']['groups']), [{'old': True}])

    def test_oversize_row_is_rejected_without_truncation(self):
        with self.assertRaises(ValueError):
            publish_snapshot(self.ctx, 'current', {}, {'groups': ['x' * 130000]})
        self.assertNotIn('current', self.kv.data)

    def test_metadata_update_reuses_unchanged_parts(self):
        old = publish_snapshot(self.ctx, 'current', {}, {'groups': list(range(200))})
        new = publish_snapshot(self.ctx, 'current', {'status': 'running'}, {})
        self.assertEqual(new['datasets'], old['datasets'])
        self.assertEqual(read_parts(self.ctx, new['datasets']['groups']), list(range(200)))

    def test_missing_part_is_not_reported_as_empty(self):
        snapshot = publish_snapshot(self.ctx, 'current', {}, {'groups': [1, 2]})
        del self.kv.data[snapshot['datasets']['groups']['parts'][0]]
        with self.assertRaises(ValueError):
            read_parts(self.ctx, snapshot['datasets']['groups'])


class GarbageCollection(unittest.TestCase):
    def test_only_old_unreferenced_revisioned_parts_deleted(self):
        from series_core.storage import collect_old_parts
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        pointer = {'datasets': {'rows': {'parts': ['scans/live'], 'count': 1}}}
        entries = [dict(key=key, updatedAt=at, revision=revision) for key, at, revision in [
            ('scans/live',old,'1'), ('scans/old',old,'2'), ('scans/recent',now,'3'),
            ('scans/no-date','bad','4'), ('scans/no-revision',old,''), ('organize-lock/x',old,'5')]]
        deleted = []
        ctx = SimpleNamespace(mt=SimpleNamespace(storage=SimpleNamespace(
            get=lambda **kw: {'found':True,'value':pointer},
            list=lambda **kw: {'items':entries,'nextCursor':'opaque'},
            delete=lambda **kw: deleted.append(kw))))
        result = collect_old_parts(ctx, 'current')
        self.assertEqual(deleted, [{'key':'scans/old','expected_revision':'2'}])
        self.assertEqual(result['nextCursor'], 'opaque')

    def test_rereads_pointer_before_delete(self):
        from series_core.storage import collect_old_parts
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        pointers = [{'datasets':{}}, {'datasets':{'rows':{'parts':['scans/new-live']}}}]
        deleted = []
        ctx = SimpleNamespace(mt=SimpleNamespace(storage=SimpleNamespace(
            get=lambda **kw: {'found':True,'value':pointers.pop(0)},
            list=lambda **kw: {'items':[{'key':'scans/new-live','updatedAt':old,'revision':'r'}]},
            delete=lambda **kw: deleted.append(kw))))
        self.assertEqual(collect_old_parts(ctx, 'current')['deleted'], 0)
        self.assertEqual(deleted, [])
