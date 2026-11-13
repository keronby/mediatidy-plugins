"""Construct the real page offline and validate picker and action payload schemas."""
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class PageContract(unittest.TestCase):
    def test_page_and_actions_match_current_manifest(self):
        try:
            import plugin
            from mt_plugin.schema import validate
        except ImportError:
            self.skipTest('SDK development environment required')
        ctx = SimpleNamespace(mt=SimpleNamespace())
        with patch.object(plugin, '_get_config', return_value={}), patch.object(plugin, '_get_state', return_value={}):
            page = plugin.Plugin().get_page(ctx)
        manifest = json.loads(Path('plugin.json').read_text())
        schemas = {a['id']: a.get('inputSchema') for a in manifest['actions']}
        registry = json.loads((Path(__import__('mt_plugin').__file__).parent / 'ui-registry.json').read_text())
        pickers = []
        def walk(value):
            if isinstance(value, dict):
                if 'component' in value:
                    name = value['component']
                    self.assertIn(name, registry['components'])
                    self.assertFalse(set(value['props']) - set(registry['components'][name]), name)
                    if name == 'DirectoryPicker':
                        pickers.append(value)
                if value.get('type') == 'invoke' and schemas.get(value['actionId']):
                    inputs = value.get('input', {})
                    schema = schemas[value['actionId']]
                    self.assertFalse(set(inputs) - set(schema['properties']), value['actionId'])
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(page)
        self.assertEqual(pickers, [])
        for action in ('scan', 'save-config'):
            validate(schemas[action], {'scanPath':'/local/library'})
            with self.assertRaises(Exception):
                validate(schemas[action], {'scanPath': {'rootId':'real', 'path':'.'}})
        self.assertLess(len(json.dumps(page).encode()), 256 * 1024)

    def test_mixed_table_selection_and_nearby_controls(self):
        import plugin
        with patch.object(plugin,'_get_config',return_value={}),patch.object(plugin,'_get_state',return_value={}):
            page=plugin.Plugin().get_page(SimpleNamespace(mt=SimpleNamespace()))
        nodes=[]
        def walk(value):
            if isinstance(value,dict):
                nodes.append(value)
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(page)
        table=next(n for n in nodes if n.get('component')=='Table' and n['props'].get('selectionBind')=='input.selectedMixedGroups')
        self.assertEqual(table['props']['rowKey'],'group_key')
        self.assertTrue(any(n.get('actionId')=='batch-cleanup-mixed' for n in nodes))
        self.assertEqual(sum(n.get('component')=='Select' and n['props'].get('bind')=='state.qualityRuleId' for n in nodes),1)
