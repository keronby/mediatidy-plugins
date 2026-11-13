"""Explicit mount bindings; root names never confer paths or authority."""
import posixpath


def authorized_bindings(sdk, bindings):
    if not isinstance(bindings, list):
        raise ValueError('rootBindings 必须为数组，请显式配置 rootId 与本地挂载路径')
    available = sdk.files.roots()
    if not isinstance(available, list):
        raise ValueError('files.roots 返回格式错误')
    allowed = {root['id']: root for root in available if isinstance(root, dict) and root.get('read')}
    result, paths, ids = [], set(), set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError('rootBindings 条目必须为对象')
        rid, path = binding.get('rootId'), binding.get('mountPath')
        if rid not in allowed:
            raise ValueError('绑定的 rootId 不在当前可读授权根内')
        if not isinstance(path, str) or not path.startswith('/') or posixpath.normpath(path) != path or path == '/' or '\\' in path or '\x00' in path:
            raise ValueError('mountPath 必须为规范绝对挂载路径且不能为系统根')
        if path in paths or rid in ids:
            raise ValueError('重复的挂载路径或 rootId，禁止歧义绑定')
        paths.add(path); ids.add(rid)
        result.append(dict(allowed[rid], path=path))
    return sorted(result, key=lambda root: len(root['path']), reverse=True)


def normalize_selection(value):
    """A picker selection is always a root ID and canonical relative path."""
    if not isinstance(value, dict) or set(value) != {'rootId', 'path'}:
        raise ValueError('请重新选择授权目录；旧绝对路径不会自动迁移')
    rid, path = value['rootId'], value['path']
    if not isinstance(rid, str) or not rid.strip() or rid != rid.strip():
        raise ValueError('目录缺少有效 rootId')
    if (not isinstance(path, str) or not path or path.startswith('/') or '\\' in path
            or '\x00' in path or posixpath.normpath(path) != path
            or '..' in path.split('/')):
        raise ValueError('目录必须为规范相对路径，根目录使用 .')
    return {'rootId': rid, 'path': path}


def resolve_selection(sdk, bindings, value):
    selection = normalize_selection(value)
    roots = authorized_bindings(sdk, bindings)
    root = next((r for r in roots if r['id'] == selection['rootId']), None)
    if root is None:
        raise ValueError('所选目录缺少显式挂载绑定或授权已撤销')
    # Verify the selected root-relative directory through the host before local I/O.
    stat = sdk.files.stat(root_id=root['id'], path=selection['path'])
    if not isinstance(stat, dict) or stat.get('isDir') is not True:
        raise ValueError('所选授权路径不是目录')
    return root['path'] if selection['path'] == '.' else root['path'] + '/' + selection['path']
