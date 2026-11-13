"""Official SDK offline contracts; never connects to a host or touches media."""
import copy
import json
from pathlib import Path
import unittest
from mt_plugin import Context
from mt_plugin.core import SDKError
from mt_plugin.testing import ContractTransport
from series_core.mt_files import MTFiles


class MTFilesContract(unittest.TestCase):
    def setup_client(self):
        self.values = {}
        self.task = dict(ref='task', runId='run', kind='delete', status='queued', total=1,
                         completed=0, failed=0, updatedAt='')
        self.file = dict(rootId='root', path='work/a.iso', ref='file', version='v1',
                         name='a.iso', size=1, isDir=False, modifiedAt='')
        def get(v):
            return dict(key=v['key'], found=v['key'] in self.values, bytes=0,
                        **({'value':copy.deepcopy(self.values[v['key']])} if v['key'] in self.values else {}))
        def put(v):
            self.values[v['key']] = copy.deepcopy(v['value'])
            return dict(key=v['key'], found=True, bytes=0)
        responses = {
            'runtime.info':dict(sdkVersion='2',instanceId='offline',runId='run',action='test',
                                pluginVersion='1.4.5',scheduleId='',parentRunId='',limits={'maxWorkflowFiles':100}),
            'storage.get':get, 'storage.set':put,
            'files.stat':self.file, 'files.list':{'items':[self.file],'hasMore':False},
            'files.delete':lambda v:copy.deepcopy(self.task),
            'tasks.get':lambda v:copy.deepcopy(self.task),
        }
        permissions = json.loads(Path('plugin.json').read_text())['permissions']
        self.transport = ContractTransport(responses, manifest={'permissions':permissions},
            grants={'capabilities':permissions,'roots':[{'id':'root','read':True,'delete':True}]})
        ctx = Context({'instanceId':'offline','flowId':'flow'},self.transport,'test')
        return MTFiles(ctx,[{'id':'root','path':'/mount'}],'offline/delete')

    def test_pagination_and_exact_relative_request(self):
        client = self.setup_client()
        self.transport.responses['files.list'] = lambda v: {'items':[self.file] if v['page']==1 else [], 'hasMore':v['page']==1}
        result = client.list_files('/mount/work')
        self.assertEqual(result[0]['path'],'/mount/work/a.iso')
        self.assertEqual([v for n,v in self.transport.calls if n=='files.list'],
                         [{'rootId':'root','path':'work','page':1},{'rootId':'root','path':'work','page':2}])

    def test_list_authorized_root_for_readonly_empty_directory_probe(self):
        client=self.setup_client()
        self.transport.responses['files.list']={'items':[],'hasMore':False}
        self.assertEqual(client.list_files('/mount'),[])
        self.assertEqual([v for n,v in self.transport.calls if n=='files.list'],
                         [{'rootId':'root','path':'.','page':1}])

    def test_queued_then_done_no_replay(self):
        client = self.setup_client()
        with self.assertRaisesRegex(ValueError,'尚未明确成功'):
            client.delete_files(['/mount/work/a.iso'])
        delete = [v for n,v in self.transport.calls if n=='files.delete']
        self.assertEqual(len(delete),1)
        self.assertEqual(delete[0]['fileRef'],'file')
        self.assertFalse(delete[0]['recursive'])
        self.assertTrue(delete[0]['idempotencyKey'])
        self.task.update(status='done',completed=1)
        client.list_files('/mount/work')
        with self.assertRaisesRegex(ValueError,'禁止重放'):
            client.delete_files(['/mount/work/a.iso'])
        self.assertEqual(sum(n=='files.delete' for n,v in self.transport.calls),1)

    def test_read_denied_preserves_sdk_error_and_no_mutation(self):
        client = self.setup_client()
        error = SDKError('Root denied','PERMISSION_DENIED',path='input.rootId',retryable=False)
        self.transport.responses['files.stat'] = error
        with self.assertRaises(SDKError) as raised:
            client.delete_files(['/mount/work/a.iso'])
        self.assertIs(raised.exception,error)
        self.assertFalse(any(n=='files.delete' for n,v in self.transport.calls))
        self.assertEqual(self.values,{})

    def test_unknown_submission_persists_intent_without_replay(self):
        client = self.setup_client()
        self.transport.responses['files.delete'] = SDKError('Unknown outcome','TIMEOUT',retryable=True)
        with self.assertRaises(SDKError):
            client.delete_files(['/mount/work/a.iso'])
        self.assertIsNone(self.values['offline/delete'][0]['ref'])
        with self.assertRaisesRegex(ValueError,'禁止重放'):
            client.delete_files(['/mount/work/a.iso'])
        self.assertEqual(sum(n=='files.delete' for n,v in self.transport.calls),1)

    def test_revoked_root_and_invalid_response_fail_closed(self):
        client = self.setup_client()
        self.transport.grants['roots'] = []
        with self.assertRaises(SDKError) as raised:
            client.list_files('/mount/work')
        self.assertEqual(raised.exception.code,'PERMISSION_DENIED')
        self.transport.grants['roots'] = [{'id':'root','read':True}]
        self.transport.responses['files.list'] = {'items':[]}
        with self.assertRaises(SDKError) as raised:
            client.list_files('/mount/work')
        self.assertEqual(raised.exception.code,'INVALID_RESPONSE')

    def test_empty_directory_requires_recursive_true_and_rechecks_empty(self):
        client=self.setup_client()
        self.file.update(path='work/empty',name='empty',isDir=True)
        self.transport.responses['files.list']={'items':[],'hasMore':False}
        self.task.update(status='done',completed=1)
        def delete(payload):
            if not payload['recursive']:
                raise SDKError('删除目录必须显式设置 recursive','INVALID_ARGUMENT')
            return dict(self.task)
        self.transport.responses['files.delete']=delete
        client.delete_files(['/mount/work/empty'])
        calls=self.transport.calls
        self.assertEqual(sum(n=='files.list' for n,v in calls),2)
        self.assertTrue(next(v for n,v in calls if n=='files.delete')['recursive'])

    def test_nonempty_or_changed_directory_never_deleted(self):
        for changed in (False,True):
            client=self.setup_client()
            self.file.update(path='work/empty',name='empty',isDir=True)
            count=[0]
            child=dict(self.file,path='work/empty/film.strm',name='film.strm',isDir=False)
            def listing(payload):
                count[0]+=1
                return {'items':[child] if not changed or count[0]>1 else [],'hasMore':False}
            self.transport.responses['files.list']=listing
            with self.assertRaises(ValueError):client.delete_files(['/mount/work/empty'])
            self.assertFalse(any(n=='files.delete' for n,v in self.transport.calls))
            self.assertEqual(self.values,{})
