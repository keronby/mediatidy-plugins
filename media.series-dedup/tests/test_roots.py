import unittest
from types import SimpleNamespace as NS
from series_core.roots import authorized_bindings
from series_core.submitter import get_roots


class RootTests(unittest.TestCase):
    def sdk(self):
        return NS(files=NS(roots=lambda: [{'id':'real','name':'Misleading (/fake)','read':True}]))

    def test_names_never_supply_paths(self):
        self.assertEqual(authorized_bindings(self.sdk(), []), [])
        self.assertEqual(get_roots(self.sdk(), {}, {'rootPath':'/fake'}), [])

    def test_explicit_binding(self):
        roots = authorized_bindings(self.sdk(), [{'rootId':'real','mountPath':'/my-mount'}])
        self.assertEqual(roots[0]['path'], '/my-mount')

    def test_fabricated_or_revoked_id_rejected(self):
        with self.assertRaises(ValueError):
            authorized_bindings(self.sdk(), [{'rootId':'learned-root','mountPath':'/fake'}])

    def test_invalid_and_ambiguous_mounts(self):
        for path in ['/', '/a/../b', '/a/', 'relative']:
            with self.assertRaises(ValueError):
                authorized_bindings(self.sdk(), [{'rootId':'real','mountPath':path}])
        with self.assertRaises(ValueError):
            authorized_bindings(self.sdk(), [{'rootId':'real','mountPath':'/a'}, {'rootId':'real','mountPath':'/b'}])

    def test_selection_rejects_legacy_and_traversal(self):
        from series_core.roots import normalize_selection
        for value in ['/old/path', None, {'rootId':'real','path':'../x'},
                      {'rootId':'real','path':'/x'}, {'rootId':'real','path':'a/./b'},
                      {'rootId':'real','path':'a\\b'}, {'rootId':'real','path':'.','rootPath':'/fake'}]:
            with self.assertRaises(ValueError):
                normalize_selection(value)
        self.assertEqual(normalize_selection({'rootId':'real','path':'.'})['path'], '.')

    def test_resolve_checks_host_before_local_path(self):
        from series_core.roots import resolve_selection
        sdk = self.sdk()
        calls = []
        def stat(**kw):
            calls.append(kw)
            return {'isDir': True}
        sdk.files.stat = stat
        self.assertEqual(resolve_selection(sdk, [{'rootId':'real','mountPath':'/mount'}],
                                           {'rootId':'real','path':'tv/work'}), '/mount/tv/work')
        self.assertEqual(calls, [{'root_id':'real','path':'tv/work'}])
        def denied(**kw):
            raise PermissionError('PERMISSION_DENIED')
        sdk.files.stat = denied
        with self.assertRaises(PermissionError):
            resolve_selection(sdk, [{'rootId':'real','mountPath':'/mount'}], {'rootId':'real','path':'.'})

    def test_missing_mount_never_guesses_nas_prefix(self):
        from series_core.scanner import resolve_local_path
        self.assertEqual(resolve_local_path('/vol1/missing/work'), '/vol1/missing/work')
