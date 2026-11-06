# -*- coding: utf-8 -*-
import ast, json, pathlib, sys, types, unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# stub mt_plugin so plugin.py can import without SDK installed
stub = types.ModuleType("mt_plugin")
class _Dummy: 
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return lambda f: f
    def __getattr__(self, n): return _Dummy()
def _dec(name):
    def wrap(fn): fn.__mt_handler__ = ("action", name); return fn
    return wrap
stub.action = _dec; stub.query = _dec; stub.continuation = _dec
stub.Plugin = object
stub.ui = types.SimpleNamespace()
sys.modules.setdefault("mt_plugin", stub)
sys.path.insert(0, str(ROOT))

import plugin as plug

class TestSanitize(unittest.TestCase):
    def test_no_forbidden_local_calls(self):
        src = (ROOT / "plugin.py").read_text()
        # 本地快路径（1.2.0）允许 os.walk/os.path.isdir/open 受信任原生行为；
        # 仍禁止 os.remove/os.scandir（删除走宿主 files.batch）。
        for bad in ("os.scandir(", "os.remove("):
            self.assertNotIn(bad, src, bad)
        self.assertNotIn("dup_scan_user_config.json", src)
    def test_local_fast_path_helpers(self):
        self.assertTrue(callable(plug._resolve_local_base))
        self.assertTrue(callable(plug._local_scan_strm))
        self.assertTrue(callable(plug._read_first_line))
    def test_auto_rule_priority(self):
        self.assertEqual(plug._pick_auto_rule([{"id":"built-in-default"},{"id":"quality-movie"}])["id"], "quality-movie")
        self.assertEqual(plug._pick_auto_rule(list(plug.DEFAULT_FALLBACK_RULES))["id"], "quality-strm")
        self.assertEqual(plug._pick_auto_rule([{"id":"quality-strm"},{"id":"quality-movie"}])["id"], "quality-strm")
        self.assertEqual(plug._pick_auto_rule([{"id":"x"}])["id"], "x")
    def test_clean_source(self):
        self.assertEqual(plug._clean_source({"rootId":"r1","path":"a/b"}), {"rootId":"r1","path":"a/b"})
        self.assertIsNone(plug._clean_source({"rootId":"r1","path":"/abs"}))
        self.assertIsNone(plug._clean_source({"rootId":"r1","path":"../x"}))
        self.assertIsNone(plug._clean_source({"path":"a"}))
        self.assertIsNone(plug._clean_source("nope"))
    def test_storage_get_envelope(self):
        class Ctx:
            class mt:
                class storage:
                    @staticmethod
                    def get(key): return {"found": False, "value": None}
        self.assertIsNone(plug._storage_get(Ctx(), "config", default=None))
        class Ctx2:
            class mt:
                class storage:
                    @staticmethod
                    def get(key): return {"found": True, "value": {"ruleId":"x"}}
        self.assertEqual(plug._storage_get(Ctx2(), "config"), {"ruleId":"x"})
    def test_default_config_has_sources(self):
        self.assertIn("scanSource", plug.DEFAULT_CONFIG)
        self.assertIn("toolSource", plug.DEFAULT_CONFIG)
        self.assertIn("scanSource", plug.CONFIG_KEYS)
        self.assertIsNone(plug.DEFAULT_CONFIG["scanSource"])
    def test_manifest_handlers_match(self):
        manifest = json.loads((ROOT / "plugin.json").read_text())
        declared = {(a.get("kind") or "action", a["id"]) for a in manifest["actions"]} | {("query", q["id"]) for q in manifest["queries"]}
        tree = ast.parse((ROOT / "plugin.py").read_text())
        registered = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for d in node.decorator_list:
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("action","query","continuation"):
                        registered.add((d.func.id, d.args[0].value))
        self.assertEqual(declared, registered, f"missing={declared-registered} extra={registered-declared}")
    def test_manifest_config_schema(self):
        manifest = json.loads((ROOT / "plugin.json").read_text())
        props = manifest["configSchema"]["properties"]
        self.assertIn("scanSource", props); self.assertIn("toolSource", props); self.assertIn("ruleId", props)
        self.assertNotIn("debugMode", props)
        for v in props.values():
            self.assertNotIn("default", v)

if __name__ == "__main__":
    unittest.main()
