import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from mt_plugin.core import SDKError
import plugin

class MappingErrorTest(unittest.TestCase):
    def ctx(self):
        return NS(flow_id='test',mt=NS(config=NS(get=lambda:{})))

    def test_missing_binding_is_specific_and_non_mutating(self):
        ctx=self.ctx()
        with patch.object(plugin,'_get_config',return_value={'strmCloudPrefix':'/cloud'}), \
             patch.object(plugin,'_known_roots_from_config',return_value={}), \
             patch.object(plugin,'write_value') as write:
            result=plugin.Plugin().test_mt_mapping({},ctx)
        self.assertEqual(result['code'],'MAPPING_BINDING_REQUIRED')
        self.assertIn('未配置授权根绑定',result['message'])
        write.assert_called_once()

    def test_structured_sdk_error_preserved_but_path_not_in_message(self):
        ctx=self.ctx();err=SDKError('Resource denied','PERMISSION_DENIED',retryable=False,path='input.rootId')
        class Client:
            def __init__(self,*a):pass
            def list_files(self,path):raise err
        with patch.object(plugin,'_get_config',return_value={'strmCloudPrefix':'/secret/mount'}), \
             patch.object(plugin,'_known_roots_from_config',return_value={'id':'/secret/mount'}), \
             patch('series_core.mt_files.MTFiles',Client), patch.object(plugin,'write_value') as write:
            with self.assertRaises(SDKError) as raised:plugin.Plugin().test_mt_mapping({},ctx)
        self.assertIs(raised.exception,err)
        saved=write.call_args.args[2]
        self.assertEqual(saved['code'],'PERMISSION_DENIED')
        self.assertEqual(saved['path'],'input.rootId')
        self.assertNotIn('/secret/mount',saved['message'])
