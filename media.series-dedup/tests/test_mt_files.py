import unittest
from types import SimpleNamespace
from series_core.mt_files import MTFiles

class MTFilesTest(unittest.TestCase):
    def setUp(self):
        self.values = {}
        def get(**kw):
            return {'found':kw['key'] in self.values, 'value':self.values.get(kw['key'])}
        def put(**kw):
            self.values[kw['key']] = kw['value']
        self.deleted = []
        self.task = {'status':'running','failed':0}
        def delete(**kw):
            self.deleted.append(kw)
            return {'ref':'task-1'}
        self.ctx = SimpleNamespace(mt=SimpleNamespace(
            storage=SimpleNamespace(get=get,set=put),
            runtime=SimpleNamespace(info=lambda:{'limits':{'maxWorkflowFiles':100}}),
            files=SimpleNamespace(stat=lambda **kw:{'ref':'file-1'},delete=delete,
                list=lambda **kw:{'items':[],'hasMore':False}),
            tasks=SimpleNamespace(get=lambda **kw:self.task)))
        self.client = MTFiles(self.ctx,[{'id':'authorized','path':'/mount'}],'run/mt-deletes')
    def test_queued_is_not_success_and_never_resubmit(self):
        with self.assertRaises(ValueError):
            self.client.delete_files(['/mount/movies/work/a.iso'])
        self.assertEqual(len(self.deleted),1)
        self.assertFalse(self.deleted[0]['recursive'])
        self.assertEqual(self.deleted[0]['file_ref'],'file-1')
        self.task = {'status':'done','failed':0}
        self.assertEqual(self.client.list_files('/mount/movies/work'),[])
        with self.assertRaises(ValueError):
            self.client.delete_files(['/mount/movies/work/a.iso'])
        self.assertEqual(len(self.deleted),1)
    def test_unknown_submission_blocks_verification(self):
        self.ctx.mt.files.delete=lambda **kw: {}
        with self.assertRaises(ValueError):self.client.delete_files(['/mount/movies/work/a.iso'])
        with self.assertRaises(ValueError):self.client.list_files('/mount/movies/work')
    def test_permission_error_is_not_absence(self):
        def denied(**kw):raise RuntimeError('PERMISSION_DENIED')
        self.ctx.mt.files.list=denied
        with self.assertRaisesRegex(RuntimeError,'PERMISSION_DENIED'):
            self.client.list_files('/mount/movies/work')
    def test_no_root_guess(self):
        with self.assertRaises(ValueError):self.client.list_files('/other/movies/work')
    def test_failed_must_be_explicit_integer_zero(self):
        self.values['run/mt-deletes']=[{'path':'/mount/a','ref':'task'}]
        for task in ({'status':'done'}, {'status':'done','failed':False}, {'status':'failed','failed':1}):
            self.task=task
            with self.assertRaises(ValueError):self.client.list_files('/mount/movies')
    def test_mapping_uses_mt_prefix(self):
        from series_core.mixed_pipeline import mapped_work
        self.assertEqual(mapped_work('/strm/movies/work',{'sourceBackend':'mt',
            'strmLocalPrefix':'/strm','strmCloudPrefix':'/mount','cd2VirtualPrefix':'/wrong'}),
            '/mount/movies/work')
