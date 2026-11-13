import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from series_core.mixed_flow import assess, cleanup, collect
from test_mixed_plan import RULE

class MixedFlow(unittest.TestCase):
    def fixture(self, root, cloud_wins=False, chaos=False):
        dirs=[]
        for name,kind,res in [('http','https://example/video.mkv','1080p'),('cloud','/must-not-be-used/video.mkv','2160p' if cloud_wins else '1080p')]:
            p=Path(root,name);p.mkdir()
            (p/f'Show.S01E01.{res}.strm').write_text(kind)
            if chaos and name=='cloud': (p/'Show.S01E02.1080p.strm').write_text(kind)
            dirs.append({'dir_path':str(p),'naming_need_organize':True})
        return {'media_type':'tv','tmdb_id':1,'dirs':dirs}

    def context(self):
        data, calls={},[]
        def get(**kw):return {'found':kw['key'] in data,'value':copy.deepcopy(data.get(kw['key']))}
        def set_(**kw):data[kw['key']]=copy.deepcopy(kw['value'])
        def delete(**kw):
            calls.append(kw)
            return {'ref':'task'}
        ctx=NS(flow_id='flow',mt=NS(storage=NS(get=get,set=set_),runtime=NS(info=lambda:{'limits':{'maxWorkflowFiles':100}}),
            files=NS(stat=lambda **kw:{'isDir':False,'ref':'http-file'},delete=delete),
            tasks=NS(wait=lambda **kw:calls.append('wait'),get=lambda **kw:{'status':'done','failed':0})))
        return ctx,data,calls

    def test_assess_not_blanket_locked_and_chaos_only_after_remaining(self):
        with tempfile.TemporaryDirectory() as root:
            group=self.fixture(root)
            result=assess(group,RULE)
            self.assertFalse(result['chaos']);self.assertFalse(group['dirs'][0]['disabled'])
            self.assertTrue(group['dirs'][0]['need_organize']);self.assertEqual(group['dirs'][1]['loser_count'],1)
        with tempfile.TemporaryDirectory() as root:
            group=self.fixture(root,chaos=True);assess(group,RULE)
            self.assertTrue(group['is_chaos_group']);self.assertTrue(all(d['disabled'] for d in group['dirs']))

    def test_http_loser_task_confirmation_and_no_local_deletion(self):
        with tempfile.TemporaryDirectory() as root:
            group=self.fixture(root,cloud_wins=True);ctx,data,calls=self.context()
            result=cleanup(ctx,group,RULE,{},[{'id':'local','path':root}])
            self.assertEqual(result['status'],'waiting');self.assertEqual(calls[-1],'wait')
            self.assertFalse(calls[0]['recursive']);self.assertEqual(calls[0]['file_ref'],'http-file')
            self.assertEqual(collect(ctx,result['recordKey'])['status'],'done')
            self.assertEqual(len(list(Path(root).rglob('*.strm'))),2)
            ctx.flow_id='other'
            with self.assertRaises(ValueError):cleanup(ctx,group,RULE,{},[{'id':'local','path':root}])

    def test_missing_failed_and_unknown_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            group=self.fixture(root,cloud_wins=True);ctx,data,calls=self.context()
            result=cleanup(ctx,group,RULE,{},[{'id':'local','path':root}])
            ctx.mt.tasks.get=lambda **kw:{'status':'done'}
            self.assertEqual(collect(ctx,result['recordKey'])['status'],'needs_review')

    def test_cloud_exact_relative_not_suffix_or_strm_target(self):
        with tempfile.TemporaryDirectory() as root:
            group=self.fixture(root);ctx,data,calls=self.context()
            name='Show.S01E01.1080p.mkv'
            ctx.mt.files.list=lambda **kw:{'items':[{'name':name,'path':'cloud/'+name,'ref':'real'},
                {'name':name,'path':'other/cloud/'+name,'ref':'wrong'}],'hasMore':False}
            result=cleanup(ctx,group,RULE,{'strmLocalPrefix':root,'strmCloudPrefix':'/remote'},[{'id':'remote','path':'/remote'}])
            self.assertEqual(calls[0]['file_ref'],'real')
            self.assertEqual(result['status'],'waiting')

    def test_winner_routes_reuse_regular_profiles_independent_of_quality(self):
        from unittest.mock import patch
        from series_core.organizer import plan
        cfg={'localProfileId':1359,'cloudProfileId':1148,'localDestDir':'/local-out',
             'cloudDestDir':'/cloud-out','strmLocalPrefix':'/local','strmCloudPrefix':'/cd2'}
        for cloud_wins, expected, source in [(False,1359,'/local/work'),(True,1148,'/cd2/work')]:
            with tempfile.TemporaryDirectory() as root:
                group=self.fixture(root,cloud_wins=cloud_wins)
                assess(group,RULE,cfg)
                winner=next(d for d in group['dirs'] if d['winner_count'])
                loser=next(d for d in group['dirs'] if not d['winner_count'])
                self.assertEqual(winner['assigned_profile_id'],expected)
                self.assertIsNone(loser['assigned_profile_id'])
                # Simulate the post-cleanup rescan row at the normal entry.
                row=dict(winner,dir_path='/local/work',tmdb_id=1,media_type='tv')
                ctx=NS(mt=NS(runtime=NS(info=lambda:{'limits':{}})))
                with patch('series_core.organizer.collect_files',return_value=[{'ref':'f','name':'S01E01.mkv'}]) as collect_files:
                    items=plan(ctx,[row],cfg,[])
                self.assertEqual(items[0]['payload']['profileId'],expected)
                self.assertEqual(collect_files.call_args.args[2],source)
                self.assertEqual(collect_files.call_args.args[3],cloud_wins)

    def test_batch_groups_preflight_all_before_delete(self):
        with tempfile.TemporaryDirectory() as root:
            first=Path(root,'one');first.mkdir()
            second=Path(root,'two');second.mkdir()
            groups=[self.fixture(first,cloud_wins=True),self.fixture(second,cloud_wins=True)]
            groups[1]['tmdb_id']=2
            ctx,data,calls=self.context()
            def stat(**kw):
                if kw['path'].startswith('two/'):
                    raise ValueError('second source changed')
                return {'isDir':False,'ref':kw['path']}
            ctx.mt.files.stat=stat
            with self.assertRaisesRegex(ValueError,'second source changed'):
                cleanup(ctx,groups,RULE,{},[{'id':'local','path':root}])
            self.assertEqual(calls,[])
            self.assertEqual(data,{})
            ctx.mt.files.stat=lambda **kw:{'isDir':False,'ref':kw['path']}
            result=cleanup(ctx,groups,RULE,{},[{'id':'local','path':root}])
            self.assertEqual(result['files'],2)
            self.assertEqual(calls[-1],'wait')
