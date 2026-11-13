import tempfile
import unittest
from pathlib import Path
from series_core.scanner import analyze_strm_files

class ISOVisibility(unittest.TestCase):
    def test_all_formats_after_mixed_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p/'a.strm').write_text('https://example/a.mkv?token=x')
            (p/'b.strm').write_text('/cloud/b.mp4')
            (p/'c.strm').write_text('https://example/c%2EISO?download=1')
            formats = set()
            result = analyze_strm_files(tmp, formats)
            self.assertEqual(result[0], 'mixed')
            self.assertEqual(formats, {'MKV', 'MP4', 'ISO'})
            self.assertEqual(result[2], 3)
    def test_physical_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'movie.ISO').touch()
            formats = set()
            analyze_strm_files(tmp, formats)
            self.assertEqual(formats, {'ISO'})
