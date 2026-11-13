import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from series_core.mixed_pipeline import prepare, advance
from test_mixed_plan import RULE
import test_mixed_flow as fixtures

class FullPipeline(unittest.TestCase):
    def fixture(self, root):
        group=fixtures.MixedFlow().fixture(root,cloud_wins=True)
        ctx,data,calls=fixtures.MixedFlow().context()
        cfg={'localScanPath':root,'localProfileId':1,'cloudProfileId':2,
             'strmLocalPrefix':root,'strmCloudPrefix':'/mount','sourceBackend':'mt'}
        cloud='/mount/cloud/Show.S01E01.2160p.mkv'
        class Cloud:
            def list_files(self,path):
                if path=='/mount':return [{'name':'cloud','path':'/mount/cloud','isDir':True}]
                if path=='/mount/cloud':return [{'name':cloud.rsplit('/',1)[1],'path':cloud,'isDir':False}]
                raise ValueError(path)
        return group,ctx,data,cfg,Cloud()

    def test_complete_http_loser_companions_empty_shell_and_exact_winner(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            loser=next(Path(root,'http').glob('*.strm'))
            loser.with_suffix('.nfo').write_text('companion')
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            with patch('series_core.mixed_pipeline.organizer.plan',return_value=[{'payload':{}}]) as plan, \
                 patch('series_core.mixed_pipeline.organizer.start',return_value={'status':'waiting'}) as start:
                result=advance(ctx,key,cloud)
            self.assertEqual(result['status'],'waiting')
            self.assertFalse(Path(root,'http').exists())
            row=plan.call_args.args[1][0]
            self.assertEqual(row['link_type'],'cloudnas')
            self.assertEqual(len(row['allowed_winner_paths']),1)
            self.assertEqual(start.call_args.kwargs['parent_key'],key)

    def test_loser_only_artwork_is_preflighted_and_removed_before_winner(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            Path(root,'http','poster.jpg').write_bytes(b'poster')
            Path(root,'http','fanart.jpg').write_bytes(b'fanart')
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            self.assertEqual(len(data[key]['items'][0]['local']),3)
            with patch('series_core.mixed_pipeline.organizer.plan',return_value=[{}]), \
                 patch('series_core.mixed_pipeline.organizer.start',return_value={'status':'waiting'}):
                self.assertEqual(advance(ctx,key,cloud)['status'],'waiting')
            self.assertFalse(Path(root,'http').exists())

    def test_artwork_in_winner_work_is_never_added_to_deletions(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            Path(root,'cloud','poster.jpg').write_bytes(b'winner')
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            self.assertFalse(any(x['path'].endswith('poster.jpg')
                for item in data[key]['items'] for x in item['local']))

    def test_changed_loser_stops_before_unlink_or_organize(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            loser=next(Path(root,'http').glob('*.strm'));loser.write_text('changed')
            with patch('series_core.mixed_pipeline.organizer.start') as start:
                with self.assertRaises(ValueError):advance(ctx,key,cloud)
                start.assert_not_called()
            self.assertTrue(loser.exists())

    def test_cloud_unknown_is_not_retried_and_index_preserved_until_absent(self):
        with tempfile.TemporaryDirectory() as root:
            group=fixtures.MixedFlow().fixture(root)
            ctx,data,calls=fixtures.MixedFlow().context()
            cfg={'localScanPath':root,'localProfileId':1,'cloudProfileId':2,
                 'strmLocalPrefix':root,'strmCloudPrefix':'/mount','sourceBackend':'mt'}
            class Cloud:
                gone=False
                deletes=0
                def list_files(self,path):
                    if path=='/mount':return [{'name':'cloud','path':'/mount/cloud','isDir':True}]
                    if path=='/mount/cloud':
                        return [] if self.gone else [{'name':'Show.S01E01.1080p.mkv','path':path+'/Show.S01E01.1080p.mkv','isDir':False}]
                    raise ValueError(path)
                def delete_files(self,paths):
                    self.deletes+=1
                    raise TimeoutError('unknown')
            cloud=Cloud()
            roots=[{'id':'l','path':root},{'id':'r','path':'/mount'}]
            key,_=prepare(ctx,[group],RULE,cfg,roots,cloud)
            with self.assertRaises(TimeoutError):advance(ctx,key,cloud)
            self.assertEqual(advance(ctx,key,cloud)['status'],'waiting_sync')
            self.assertEqual(cloud.deletes,1)
            self.assertEqual(len(list(Path(root,'cloud').glob('*.strm'))),1)

    def test_partial_lock_failure_cannot_resume(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            original=ctx.mt.storage.set
            def failing(**kw):
                if kw['key'].startswith('mixed-path-lock/'):
                    raise RuntimeError('lock conflict')
                return original(**kw)
            ctx.mt.storage.set=failing
            with self.assertRaisesRegex(RuntimeError,'lock conflict'):
                prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            with self.assertRaisesRegex(ValueError,'全部加锁'):
                advance(ctx,'mixed-pipelines/flow',cloud)
            self.assertEqual(len(list(Path(root).rglob('*.strm'))),2)

    def test_changed_winner_blocks_before_loser_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            next(Path(root,'cloud').glob('*.strm')).write_text('changed winner')
            with self.assertRaisesRegex(ValueError,'胜者或跳过项已变化'):
                advance(ctx,key,cloud)
            self.assertTrue(Path(root,'http').exists())

    def test_loser_shell_without_media_deletes_sidecars_and_directory(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            side=Path(root,'http','unrelated.sub','sub.nfo')
            side.parent.mkdir(); side.write_text('sidecar')
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            with patch('series_core.mixed_pipeline.organizer.plan',return_value=[{}]), \
                 patch('series_core.mixed_pipeline.organizer.start',return_value={'status':'waiting'}):
                result=advance(ctx,key,cloud)
            self.assertEqual(result['status'],'waiting')
            self.assertFalse(Path(root,'http').exists())

    def test_regular_media_in_loser_shell_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            (Path(root,'http')/'stray.iso').write_bytes(b'media')
            with patch('series_core.mixed_pipeline.organizer.start') as start:
                with self.assertRaisesRegex(ValueError,'仍有STRM或正规媒体文件'):
                    advance(ctx,key,cloud)
                start.assert_not_called()
            self.assertTrue((Path(root,'http')/'stray.iso').exists())

    def test_cloud_companions_confirmed_before_local_index_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            group=fixtures.MixedFlow().fixture(root)
            ctx,data,calls=fixtures.MixedFlow().context()
            cfg={'localScanPath':root,'localProfileId':1,'cloudProfileId':2,
                 'strmLocalPrefix':root,'strmCloudPrefix':'/mount','sourceBackend':'mt'}
            stem='/mount/cloud/Show.S01E01.1080p'
            class Cloud:
                paths={stem+'.mkv',stem+'.nfo'}
                deleted=[]
                def list_files(self,path):
                    if path=='/mount':return [{'name':'cloud','path':'/mount/cloud','isDir':True}]
                    return [{'name':p.rsplit('/',1)[1],'path':p,'isDir':False} for p in sorted(self.paths)]
                def delete_files(self,paths):
                    self.deleted.append(paths)
                    # Source video gone, companion sync is still pending.
                    self.paths.discard(stem+'.mkv')
            cloud=Cloud()
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'l','path':root},{'id':'c','path':'/mount'}],cloud)
            result=advance(ctx,key,cloud)
            self.assertEqual(result['status'],'waiting_sync')
            self.assertEqual(set(cloud.deleted[0]),{stem+'.mkv',stem+'.nfo'})
            self.assertTrue(next(Path(root,'cloud').glob('*.strm')).exists())
            advance(ctx,key,cloud)
            self.assertEqual(len(cloud.deleted),1)

    def test_real_organizer_parent_wait_and_completion(self):
        from series_core import organizer
        from series_core.mixed_pipeline import collect
        with tempfile.TemporaryDirectory() as root:
            group,ctx,data,cfg,cloud=self.fixture(root)
            calls = []
            ctx.mt.tasks.wait=lambda **kw: calls.append('wait')
            ctx.mt.organize=NS(manual=NS(run=lambda payload:{'ref':'winner-task'}))
            key,_=prepare(ctx,[group],RULE,cfg,[{'id':'r','path':'/mount'}],cloud)
            item={'payload':{'fileRef':'winner'},'lock_key':'organize-lock/winner','status':'pending'}
            with patch('series_core.mixed_pipeline.organizer.plan',return_value=[item]):
                result=advance(ctx,key,cloud)
            self.assertEqual(result['status'],'waiting')
            self.assertEqual(data[key]['phase'],'organizing')
            self.assertEqual(calls[-1],'wait')
            self.assertEqual(collect(ctx,key)['status'],'done')
            self.assertEqual(data[key]['phase'],'done')

class OrganizerAllowlist(unittest.TestCase):
    def test_exact_winner_filter_excludes_skipped_file(self):
        from series_core.organizer import plan
        ctx=NS(mt=NS(runtime=NS(info=lambda:{'limits':{}})))
        row={'dir_path':'/local/work','link_type':'http','tmdb_id':1,'media_type':'tv',
             'allowed_winner_paths':['/local/work/S01E01.strm']}
        files=[{'ref':'win','path':'work/S01E01.strm','name':'S01E01.strm'},
               {'ref':'skip','path':'work/unknown.strm','name':'unknown.strm'}]
        with patch('series_core.organizer.collect_files',return_value=files):
            result=plan(ctx,[row],{'localProfileId':1},[{'id':'r','path':'/local'}])
        self.assertEqual([i['payload']['fileRef'] for i in result],['win'])
