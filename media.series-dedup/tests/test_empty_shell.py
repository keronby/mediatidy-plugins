import tempfile
import unittest
from pathlib import Path
from series_core.empty_shell import clean_local, cloud_inventory

class EmptyShellPolicy(unittest.TestCase):
    def test_recursive_media_and_symlinks_preserved(self):
        for name in ('keep.STRM','keep.m4v','keep.ISO'):
            with tempfile.TemporaryDirectory() as base:
                work=Path(base,'work');work.mkdir()
                nested=work/'season';nested.mkdir();(nested/name).write_text('keep')
                (work/'poster.jpg').write_text('art')
                with self.assertRaises(ValueError):clean_local(str(work),base)
                self.assertTrue((work/'poster.jpg').exists())
        with tempfile.TemporaryDirectory() as base:
            work=Path(base,'work');work.mkdir();(work/'link').symlink_to('/tmp')
            with self.assertRaises(ValueError):clean_local(str(work),base)
            self.assertTrue(work.exists())

    def test_cloud_recursive_sidecars_allowed_and_media_blocks(self):
        class Client:
            media=False
            def list_files(self,path):
                if path=='/mount/work':return [{'path':path+'/sub','isDir':True}]
                return [{'path':path+('/film.mkv' if self.media else '/any.subtitle.srt'),'isDir':False}]
        client=Client()
        self.assertEqual(cloud_inventory(client,'/mount/work')[0],['/mount/work/sub/any.subtitle.srt'])
        client.media=True
        with self.assertRaises(ValueError):cloud_inventory(client,'/mount/work')

    def test_read_failure_keeps_local_files(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as base:
            work=Path(base,'work');work.mkdir();(work/'poster.jpg').write_text('keep')
            with patch('series_core.empty_shell.os.walk',side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):clean_local(str(work),base)
            self.assertTrue((work/'poster.jpg').exists())
