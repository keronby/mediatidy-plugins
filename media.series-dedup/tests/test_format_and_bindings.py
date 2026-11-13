import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from series_core.matcher import match_duplicate_groups
from series_core.roots import authorized_bindings


class FormatAndBindings(unittest.TestCase):
    def test_formats_survive_matching_and_table_compaction(self):
        import plugin
        dirs = [dict(dir_name='Film (2000) {tmdb-123}', dir_path='/scan/' + name,
                     local_title='Film', year=2000, tmdb_id=123, link_type=link,
                     formats=formats, format_label=' / '.join(formats))
                for name, link, formats in [('a', 'http', ['ISO']), ('b', 'cloudnas', ['MKV'])]]
        source = [dict(tmdb_id=123, media_type='movie', title='Film', year=2000,
                       dirs=dirs, is_duplicate=True, is_mixed=True, group_link_type='mixed')]
        before = copy.deepcopy(source)
        groups = match_duplicate_groups(source, {('movie', 123): {'title':'Film', 'year':2000}})
        self.assertEqual([d['formats'] for d in groups[0]['dirs']], [['ISO'], ['MKV']])
        self.assertEqual([d['format_label'] for d in groups[0]['dirs']], ['ISO', 'MKV'])
        self.assertEqual([d['formats'] for d in source[0]['dirs']],
                         [d['formats'] for d in before[0]['dirs']])
        self.assertEqual(plugin._compact_for_storage(groups, [])[2][0]['format_label'], 'ISO / MKV')
        groups[0]['is_mixed'] = False
        for d in groups[0]['dirs']:
            d.update(need_organize=True, disabled=False)
        share, local, mixed = plugin._compact_for_storage(groups, [])
        self.assertEqual(share[0]['format_label'], 'ISO')
        self.assertEqual(local[0]['format_label'], 'MKV')

    def test_save_bindings_validates_before_persisting(self):
        import plugin
        sdk = NS(files=NS(roots=lambda: [{'id':'real', 'read':True}],
                          stat=lambda **kw: {'isDir':True}))
        ctx = NS(mt=sdk)
        with patch.object(plugin, '_get_config', return_value={'keep': 1}), patch.object(plugin, 'write_value') as write:
            result = plugin.Plugin().save_root_bindings({'bindingsJson':json.dumps([{'rootId':'real','mountPath':'/mount'}])}, ctx)
            self.assertTrue(result['ok'])
            self.assertEqual(write.call_args_list[0].args[2], {'keep':1,'rootBindings':[{'rootId':'real','mountPath':'/mount'}]})
        for value in ['{}', '[{"rootId":"fake","mountPath":"/mount"}]', '[{"rootId":"real","mountPath":"/mount","extra":1}]']:
            with patch.object(plugin, 'write_value') as write:
                with self.assertRaises(ValueError):
                    plugin.Plugin().save_root_bindings({'bindingsJson':value}, ctx)
                write.assert_not_called()

    def test_binding_stat_failure_propagates_without_save(self):
        import plugin
        def denied(**kw):
            raise PermissionError('PERMISSION_DENIED')
        ctx = NS(mt=NS(files=NS(roots=lambda:[{'id':'real','read':True}], stat=denied)))
        with patch.object(plugin, 'write_value') as write:
            with self.assertRaisesRegex(PermissionError, 'PERMISSION_DENIED'):
                plugin.Plugin().save_root_bindings({'bindingsJson':'[{"rootId":"real","mountPath":"/mount"}]'}, ctx)
            write.assert_not_called()

    def test_business_binding_overrides_host_even_when_cleared(self):
        import plugin
        ctx = NS(mt=NS(config=NS(get=lambda:{'rootBindings':[{'rootId':'real','mountPath':'/old'}]}),
                        files=NS(roots=lambda:[{'id':'real','read':True}])))
        self.assertEqual(plugin._known_roots_from_config(ctx, {'rootBindings':[]}), {})
        self.assertEqual(plugin._known_roots_from_config(ctx, {}), {'real':'/old'})
        self.assertEqual(plugin._known_roots_from_config(ctx, {'rootBindings':[{'rootId':'real','mountPath':'/new'}]}), {'real':'/new'})

    def test_invalid_mount_characters_rejected(self):
        sdk = NS(files=NS(roots=lambda:[{'id':'real','read':True}]))
        for path in ['/mount\\child', '/mount\x00child']:
            with self.assertRaises(ValueError):
                authorized_bindings(sdk, [{'rootId':'real','mountPath':path}])
