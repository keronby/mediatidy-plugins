import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.deletion import delete_local


class TestDeletion(unittest.TestCase):
    def test_exclusion_never_deletes_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'source.mkv'; media.write_text('media')
            strm = root / 'movie.strm'; strm.write_text(str(media))
            (root / 'movie.nfo').write_text('meta')
            result = delete_local(str(strm))
            self.assertTrue(result['ok'])
            self.assertTrue(media.exists())
            self.assertFalse(strm.exists())

    def test_wash_requires_source_success_before_index_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'source.mkv'; media.write_text('media')
            strm = root / 'movie.strm'; strm.write_text(str(media))
            with patch('core.deletion.os.remove', side_effect=PermissionError('denied')):
                result = delete_local(str(strm), follow_target=True)
            self.assertFalse(result['ok'])
            self.assertTrue(strm.exists())
            self.assertTrue(media.exists())

    def test_preview_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as folder:
            strm = Path(folder) / 'movie.strm'; strm.write_text('https://example.com/a.mkv')
            result = delete_local(str(strm), follow_target=True, dry_run=True)
            self.assertTrue(result['ok'])
            self.assertEqual(result['deleted'], 0)
            self.assertTrue(strm.exists())

    def test_missing_source_keeps_index(self):
        with tempfile.TemporaryDirectory() as folder:
            strm = Path(folder) / 'movie.strm'; strm.write_text(folder + '/missing.mkv')
            self.assertFalse(delete_local(str(strm), follow_target=True)['ok'])
            self.assertTrue(strm.exists())

    def test_checkpoint_allows_retry_after_partial_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'source.mkv'; media.write_text('media')
            strm = root / 'movie.strm'; strm.write_text(str(media))
            checkpoints = []
            remove = os.remove
            def fail_index(path):
                if path == str(strm):
                    raise PermissionError('denied')
                remove(path)
            with patch('core.deletion.os.remove', side_effect=fail_index):
                first = delete_local(str(strm), follow_target=True, checkpoint=checkpoints.append)
            self.assertFalse(first['ok'])
            self.assertFalse(media.exists())
            self.assertTrue(strm.exists())
            retry = delete_local(str(strm), follow_target=True, previous=checkpoints[-1])
            self.assertTrue(retry['ok'])
            self.assertFalse(strm.exists())
