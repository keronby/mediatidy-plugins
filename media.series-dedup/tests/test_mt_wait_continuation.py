import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from series_core.mt_files import MTFiles, MTTasksPending
from series_core import mixed_pipeline

class PendingContinuationTest(unittest.TestCase):
    def test_nonterminal_delete_task_is_waited_and_resumed_not_failed(self):
        import copy
        key='mixed-pipelines/run'
        data={key:{'phase':'sources','status':'prepared','locks_ready':True,'locks':[],
            'cloud_dirs':[], 'cfg':{'sourceBackend':'mt','strmLocalPrefix':'/local','strmCloudPrefix':'/mount'},'roots':[], 'retained':[], 'works':[],
            'boundary':'/local', 'items':[{'status':'pending','cloud':True,'work':'/local/work',
                'cloud_sources':['/mount/work/a.mkv'],'local':[]}]}}
        calls=[]
        def read(ctx,k,*args):return copy.deepcopy(data.get(k))
        def write(ctx,k,value):data[k]=copy.deepcopy(value);calls.append('save')
        class Cloud:
            def delete_files(self,paths):
                calls.append('delete')
                raise MTTasksPending(['task-ref'])
            def list_files(self,path):return []
        def wait(**kw):calls.append(('wait',kw))
        ctx=NS(mt=NS(tasks=NS(wait=wait)))
        with patch.object(mixed_pipeline,'read_value',side_effect=read), \
             patch.object(mixed_pipeline,'write_value',side_effect=write):
            result=mixed_pipeline.advance(ctx,key,Cloud())
        self.assertEqual(result['status'],'waiting_tasks')
        self.assertEqual(data[key]['items'][0]['status'],'source_unknown')
        self.assertEqual(data[key]['status'],'waiting_tasks')
        self.assertEqual(calls[-1],('wait',{'refs':['task-ref'],
            'completion':{'action':'collect-mixed','input':{'key':key}}}))
        # Resume verifies absence; it must never invoke delete for source_unknown.
        with patch.object(mixed_pipeline,'read_value',side_effect=read), \
             patch.object(mixed_pipeline,'write_value',side_effect=write):
            result=mixed_pipeline.advance(ctx,key,Cloud())
        self.assertEqual(result['status'],'done')
        self.assertEqual(calls.count('delete'),1)

    def test_repeated_waits_use_distinct_registered_continuations(self):
        from series_core.mixed_pipeline import wait_pending
        waits=[]
        ctx=NS(mt=NS(tasks=NS(wait=lambda **kw: waits.append(kw)), storage=NS(set=lambda **kw:None)))
        record={'wait_step':0}
        for expected in ('collect-mixed','collect-mixed-1','collect-mixed-2'):
            result=wait_pending(ctx,'mixed-pipelines/x',record,['r'])
            self.assertEqual(result['status'],'waiting_tasks')
            self.assertEqual(waits[-1]['completion']['action'],expected)
        self.assertEqual(record['wait_step'],3)
        self.assertEqual(len(set(w['completion']['action'] for w in waits)),3)

    def test_wait_budget_stops_without_registering_another_action(self):
        from series_core.mixed_pipeline import wait_pending
        waits=[]
        ctx=NS(mt=NS(tasks=NS(wait=lambda **kw: waits.append(kw)),storage=NS(set=lambda **kw:None)))
        result=wait_pending(ctx,'mixed-pipelines/x',{'wait_step':20},['r'])
        self.assertEqual(result['status'],'waiting_manual')
        self.assertEqual(waits,[])

    def test_old_record_collect_reserves_already_used_action(self):
        record={'phase':'shells'}
        with patch.object(mixed_pipeline,'read_value',return_value=record), \
             patch.object(mixed_pipeline,'write_value'), \
             patch.object(mixed_pipeline,'advance',return_value={'status':'waiting'}) as advance:
            mixed_pipeline.collect(NS(),'mixed-pipelines/old')
        self.assertEqual(record['wait_step'],1)
        advance.assert_called_once()

    def test_mtfiles_raises_typed_pending_for_queued_and_keeps_ref(self):
        data={'deletes':[{'path':'/mount/a','ref':'task'}]}
        ctx=NS(mt=NS(runtime=NS(info=lambda:{'limits':{}}),
            storage=NS(get=lambda **kw:{'found':True,'value':data['deletes']}),
            tasks=NS(get=lambda **kw:{'status':'queued','failed':0})))
        client=MTFiles(ctx,[{'id':'root','path':'/mount'}],'deletes')
        with self.assertRaises(MTTasksPending) as raised:client.check_tasks()
        self.assertEqual(raised.exception.refs,['task'])

    def test_official_transport_wait_and_resume_never_reissues_delete(self):
        import copy
        from test_mt_files_contract import MTFilesContract
        fixture = MTFilesContract()
        client = fixture.setup_client()
        key = 'mixed-pipelines/offline'
        fixture.values[key] = {'phase':'sources','status':'prepared','locks_ready':True,
            'locks':[], 'cloud_dirs':[], 'cfg':{'sourceBackend':'mt',
            'strmLocalPrefix':'/local','strmCloudPrefix':'/mount'}, 'roots':[],
            'retained':[], 'works':[], 'boundary':'/local', 'items':[{
                'status':'pending','cloud':True,'work':'/local/work',
                'cloud_sources':['/mount/work/a.iso'],'local':[]}]}
        # Response shape comes from the bundled official contract.
        fixture.transport.responses['tasks.wait'] = {'waiting':True}
        with patch.object(mixed_pipeline,'write_value',wraps=mixed_pipeline.write_value):
            result = mixed_pipeline.advance(client.ctx,key,client)
        self.assertEqual(result['status'],'waiting_tasks')
        self.assertEqual(fixture.transport.calls[-1][0],'tasks.wait')
        fixture.task.update(status='done',completed=1)
        fixture.transport.responses['files.list'] = {'items':[], 'hasMore':False}
        self.assertEqual(mixed_pipeline.advance(client.ctx,key,client)['status'],'done')
        self.assertEqual(sum(n=='files.delete' for n,v in fixture.transport.calls),1)
