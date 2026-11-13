import json
import unittest
from pathlib import Path
from series_core.mixed import slot_label
from series_core.mt_files import mapped_path

ROOT = Path(__file__).resolve().parents[1]
class ReleaseCleanup(unittest.TestCase):
    def test_manifest_no_external_credentials(self):
        m=json.loads((ROOT/'plugin.json').read_text())
        self.assertNotIn('secrets.use',m['permissions'])
        self.assertFalse(m.get('secrets'))
        self.assertNotIn('cd2Url',m['configSchema']['properties'])
        self.assertNotIn('cd2VirtualPrefix',m['configSchema']['properties'])
        self.assertIn('test-mt-mapping',[a['id'] for a in m['actions']])
        self.assertFalse((ROOT/'series_core/cd2.py').exists())
    def test_public_slot_display_only(self):
        self.assertEqual(slot_label('unmatched'),'公共槽')
        self.assertEqual(slot_label('unmatched-skip'),'未命中槽位（跳过）')
        self.assertEqual(slot_label('ISO'),'ISO')
    def test_reject_unsafe_mapped_paths(self):
        for p in ('/', '/cloud/../other', '/cloud/./work', '/cloud//work', '/cloud/\\work', 'relative'):
            with self.subTest(p=p),self.assertRaises(ValueError):mapped_path(p)
        self.assertEqual(mapped_path('/cloud/movies/work'),'/cloud/movies/work')
