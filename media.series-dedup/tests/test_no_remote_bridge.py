"""mt-console means a source reference, not an extra runtime service."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class NoRemoteBridge(unittest.TestCase):
    def test_release_has_no_extra_http_service_or_secret(self):
        manifest = json.loads((ROOT / 'plugin.json').read_text())
        self.assertNotIn('http.request', manifest['permissions'])
        self.assertNotIn('secrets.use', manifest['permissions'])
        self.assertNotIn('mtcBaseUrl', manifest['configSchema']['properties'])
        self.assertFalse(manifest.get('secrets'))
        self.assertFalse((ROOT / 'series_core/cd2.py').exists())
        self.assertFalse((ROOT / 'series_core/mtc.py').exists())
        for p in [ROOT / 'plugin.py', * (ROOT / 'series_core').rglob('*.py')]:
            text = p.read_text()
            for token in ('mtcToken', 'mtcBaseUrl', 'mtc_from_context'):
                self.assertNotIn(token, text, str(p))

    def test_query_action_does_not_claim_force_refresh(self):
        try:
            import plugin
        except ImportError:
            self.skipTest('SDK required')
        ctx = NS(mt=NS())
        with patch.object(plugin, '_get_full_groups', return_value=[{'tmdb_id':1,'media_type':'tv'}]), \
             patch.object(plugin, 'query_mt_tmdb_caches', return_value={('tv',1):{'title':'test'}}) as read:
            result = plugin.Plugin().refresh_tmdb({}, ctx)
        self.assertFalse(result['force_refresh'])
        self.assertEqual(result['resolved'], 1)
        read.assert_called_once_with([('tv',1)],ctx=ctx)
