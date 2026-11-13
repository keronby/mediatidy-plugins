import copy
import unittest
from types import SimpleNamespace as NS
from series_core.organizer import collect_files, start, collect


class OrganizeTests(unittest.TestCase):
    def context(self, fail=False):
        data, calls = {}, []
        def get(**kw):
            return {'found': kw['key'] in data, 'value': copy.deepcopy(data.get(kw['key']))}
        def set_(**kw):
            data[kw['key']] = copy.deepcopy(kw['value'])
        def run(payload):
            calls.append('run')
            if fail:
                raise TimeoutError('outcome_unknown')
            return {'ref': 'task', 'status': 'queued'}
        def wait(**kw):
            calls.append('wait')
        ctx = NS(flow_id='flow', mt=NS(storage=NS(get=get, set=set_),
                  organize=NS(manual=NS(run=run)), tasks=NS(wait=wait,
                  get=lambda **kw: {'status': 'done', 'failed': 0})))
        return ctx, data, calls

    def item(self):
        return {'path':'/work', 'lock_key':'lock/file', 'status':'pending',
                'payload':{'fileRef':'f','profileId':1,'tmdbId':2,'mediaType':'tv','idempotencyKey':'stable'}}

    def test_wait_then_confirm_and_block_cross_flow(self):
        ctx, data, calls = self.context()
        result = start(ctx, [self.item()])
        self.assertEqual(calls, ['run', 'wait'])
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(data[result['recordKey']]['items'][0]['status'], 'waiting')
        self.assertEqual(collect(ctx, result['recordKey'])['status'], 'done')
        ctx.flow_id = 'another'
        with self.assertRaises(ValueError):
            start(ctx, [self.item()])
        self.assertEqual(calls, ['run', 'wait'])

    def test_unknown_stops_following_submissions(self):
        ctx, data, calls = self.context(fail=True)
        item = self.item(); item['lock_key'] = 'second'
        result = start(ctx, [self.item(), item])
        self.assertEqual(result['status'], 'outcome_unknown')
        self.assertEqual(calls, ['run'])

    def test_missing_failure_count_not_success(self):
        ctx, data, calls = self.context()
        result = start(ctx, [self.item()])
        ctx.mt.tasks.get = lambda **kw: {'status':'done'}
        self.assertEqual(collect(ctx, result['recordKey'])['status'], 'needs_review')

    def test_cloud_excludes_strm_and_paging_errors_propagate(self):
        rows = [{'name':'x.strm','ref':'s'}, {'name':'x.mkv','ref':'v'}]
        sdk = NS(files=NS(list=lambda **kw: {'items':rows, 'hasMore':False}))
        budget = {'remaining':100, 'list_calls':0}
        refs = collect_files(sdk, [{'id':'r','path':'/mount'}], '/mount/work', True, budget)
        self.assertEqual([r['ref'] for r in refs], ['v'])
        sdk.files.list = lambda **kw: {'items':[], 'hasMore':True}
        with self.assertRaises(ValueError):
            collect_files(sdk, [{'id':'r','path':'/mount'}], '/mount/work', True, budget)

    def test_file_limit_checked_before_submit(self):
        sdk = NS(files=NS(list=lambda **kw: {'items':[{'name':f'{i}.strm','ref':str(i)} for i in range(3)],'hasMore':False}))
        with self.assertRaises(ValueError):
            collect_files(sdk, [{'id':'r','path':'/mount'}], '/mount/work', False, {'remaining':2,'list_calls':0})

    def test_sdk_contract_end_to_end(self):
        try:
            from mt_plugin import Context
            from mt_plugin.testing import ContractTransport
        except ImportError:
            self.skipTest('SDK development environment required')
        values = {}
        def get(value):
            key = value['key']
            return {'key':key,'found':key in values,'bytes':0, **({'value':values[key]} if key in values else {})}
        def set_(value):
            values[value['key']] = copy.deepcopy(value['value'])
            return {'key':value['key'],'found':True,'bytes':0,'value':value['value']}
        task = {'ref':'t','runId':'run','kind':'organize','status':'queued', 'total':1,'completed':0,'failed':0,'updatedAt':''}
        permissions = ['storage','organize.manual.run','tasks.wait','tasks.read']
        transport = ContractTransport({'storage.get':get,'storage.set':set_,
                     'organize.manual.run':task,'tasks.wait':{'waiting':True}},
                     manifest={'permissions':permissions})
        ctx = Context({'instanceId':'offline','flowId':'flow'}, transport, 'test')
        result = start(ctx, [self.item()])
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(transport.calls[-1][0], 'tasks.wait')

    def test_write_budget_rejected_before_locks(self):
        ctx, data, calls = self.context()
        with self.assertRaises(ValueError):
            start(ctx, [self.item()] * 151)
        self.assertEqual(data, {})
        self.assertEqual(calls, [])
