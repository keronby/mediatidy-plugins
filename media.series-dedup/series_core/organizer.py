"""Plan all files before submission; journal every side effect and await tasks."""
import hashlib
import posixpath
import re
from .storage import read_value, write_value
from .submitter import to_mt_path, match_root

VIDEO = ('.mkv', '.mp4', '.avi', '.ts', '.m2ts', '.wmv', '.flv', '.mov', '.webm', '.rmvb', '.mpg', '.mpeg', '.iso')


def collect_files(sdk, roots, path, cloud, budget):
    root = match_root(path, roots)
    if not root:
        raise ValueError('作品目录未绑定授权根，请配置目录后再操作')
    relative = path.rstrip('/')[len(root['path'].rstrip('/')):].strip('/') or '.'
    queue, seen, refs = [relative], set(), {}
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        if len(seen) > 60:
            raise ValueError('作品子目录超过60个，请缩小操作范围；未提交任何文件')
        page = 1
        while True:
            budget['list_calls'] += 1
            if budget['list_calls'] > 450:
                raise ValueError('本轮目录分页超过450次，请缩小操作范围')
            response = sdk.files.list(root_id=root['id'], path=cur, page=page)
            if not isinstance(response, dict) or not isinstance(response.get('items'), list) or type(response.get('hasMore')) is not bool:
                raise ValueError('files.list 返回格式错误，拒绝部分提交')
            for item in response['items']:
                name = str(item.get('name') or '')
                if item.get('isDir'):
                    child = str(item.get('path') or '')
                    normalized = posixpath.normpath(child)
                    if normalized.startswith('../') or normalized == '..' or normalized.startswith('/'):
                        raise ValueError('目录返回越界相对路径')
                    parent = '' if cur == '.' else cur.rstrip('/') + '/'
                    if not child or not normalized.startswith(parent) or normalized == cur:
                        raise ValueError('目录返回非子目录路径')
                    queue.append(normalized)
                elif name.lower().endswith(VIDEO if cloud else ('.strm',)):
                    if not item.get('ref'):
                        raise ValueError('文件缺少 fileRef')
                    refs[item['ref']] = item
                    if len(refs) > budget['remaining']:
                        raise ValueError('文件数超过本轮限制，请缩小选择；未提交任何文件')
            if not response['hasMore']:
                break
            if not response['items']:
                raise ValueError('空页却标记 hasMore，拒绝部分提交')
            page += 1
    if not refs:
        raise ValueError('作品目录内无可提交的' + ('视频文件' if cloud else 'STRM文件'))
    budget['remaining'] -= len(refs)
    return list(refs.values())


def plan(ctx, rows, cfg, roots):
    limit = min(150, max(1, int((ctx.mt.runtime.info().get('limits') or {}).get('maxWorkflowFiles', 100))))
    budget = {'remaining': limit, 'list_calls': 0}
    items, identities = [], set()
    paths = set()
    for row in rows:
        path = row.get('local_path') or row.get('dir_path')
        link = row.get('link_type')
        if not path or row.get('disabled') or link not in ('http', 'cloudnas'):
            raise ValueError('保护、混合或未知条目禁止整理')
        if path in paths:
            continue
        paths.add(path)
        media = row.get('media_type')
        pid = int(row.get('assigned_profile_id') or cfg.get('localProfileId' if link == 'http' else 'cloudProfileId') or 0)
        tid = int(row.get('tmdb_id') or 0)
        if pid <= 0 or tid <= 0 or media not in ('movie', 'tv'):
            raise ValueError('缺少有效方案、TMDB ID 或媒体类型')
        source = to_mt_path(path, link_type=link, profile_id=pid,
                            strm_local_prefix=cfg.get('strmLocalPrefix', ''),
                            strm_cloud_prefix=cfg.get('strmCloudPrefix', ''))
        if link == 'cloudnas' and (not cfg.get('strmLocalPrefix') or not cfg.get('strmCloudPrefix')):
            raise ValueError('云盘整理需要明确配置本地与云盘前缀')
        files = collect_files(ctx.mt, roots, source, link == 'cloudnas', budget)
        if 'allowed_winner_paths' in row:
            allowed = row['allowed_winner_paths']
            if not isinstance(allowed, list) or not allowed:
                raise ValueError('胜者文件清单为空或无效')
            root = match_root(source, roots)
            if not root:
                raise ValueError('胜者目录缺少授权根')
            base = source[len(root['path']):].strip('/')
            expected = set()
            for winner in allowed:
                relative = posixpath.relpath(winner, path)
                if relative == '..' or relative.startswith('../') or relative.startswith('/'):
                    raise ValueError('胜者清单越出作品目录')
                wanted = posixpath.join(base, relative)
                matches = [f for f in files if
                           (posixpath.splitext(f.get('path', ''))[0] == posixpath.splitext(wanted)[0]
                            if link == 'cloudnas' else f.get('path') == wanted)]
                if len(matches) != 1:
                    raise ValueError('胜者清单无法唯一对应宿主文件，停止整理')
                expected.add(matches[0]['ref'])
            files = [f for f in files if f['ref'] in expected]
        for file in files:
            ref = file['ref']
            if ref in identities:
                raise ValueError('选择的目录重叠，包含重复文件')
            identities.add(ref)
            payload = {'fileRef': ref, 'profileId': pid, 'tmdbId': tid, 'mediaType': media}
            digest = hashlib.sha256((str(pid) + ':' + media + ':' + str(tid) + ':' + ref).encode()).hexdigest()
            payload['idempotencyKey'] = 'organize:' + digest
            if media == 'tv':
                match = re.search(r'[Ss](\d{1,4})[Ee](\d{1,5})', file.get('name', ''))
                if match and (int(match[1]) > 9999 or not 1 <= int(match[2]) <= 9999):
                    raise ValueError('季集编号超出宿主契约范围，未提交任何文件')
                if match:
                    payload.update(season=int(match[1]), episode=int(match[2]))
            items.append({'path': path, 'link_type': link, 'payload': payload, 'status': 'pending',
                          'lock_key': 'organize-lock/' + hashlib.sha256(ref.encode()).hexdigest()})
    if not items:
        raise ValueError('无可整理文件')
    return items


def start(ctx, items, dry_run=False, parent_key=None):
    # Each file needs one lock write and up to two journal writes. Stay below
    # storage.set's 500-call budget, including final/initial records.
    if len(items) > 150:
        raise ValueError('本轮最多150文件，请分批操作')
    if dry_run:
        return {'status': 'simulated', 'files': len(items), 'submitted': 0}
    key = 'organize-runs/' + ctx.flow_id
    if read_value(ctx, key) is not None:
        raise ValueError('已有本流程任务记录，禁止重放')
    for item in items:
        if read_value(ctx, item['lock_key']) is not None:
            raise ValueError('文件已有整理记录，请先核对原任务；禁止跨流程重放')
    record = {'status': 'submitting', 'items': items, 'parent_key': parent_key}
    write_value(ctx, key, record)
    for item in items:
        ctx.mt.storage.set(key=item['lock_key'], value={'recordKey': key}, expected_revision='')
    refs = []
    for item in items:
        item['status'] = 'submitting'
        write_value(ctx, key, record)
        try:
            task = ctx.mt.organize.manual.run(item['payload'])
            if not isinstance(task, dict) or not task.get('ref'):
                raise ValueError('整理结果缺少任务引用，请核对宿主，禁止重试')
            item.update(task_ref=task['ref'], status='waiting')
            refs.append(task['ref'])
        except Exception as exc:
            item.update(status='outcome_unknown', error=str(exc))
            break
        write_value(ctx, key, record)
    record['status'] = 'waiting' if refs else 'outcome_unknown'
    write_value(ctx, key, record)
    if parent_key:
        parent = read_value(ctx, parent_key)
        parent.update(phase='organizing', status=record['status'], organize_key=key)
        write_value(ctx, parent_key, parent)
    if refs:
        ctx.mt.tasks.wait(refs=list(dict.fromkeys(refs)), completion={'action': 'collect-organize', 'input': {'key': key}})
        return {'status': 'waiting', 'taskRefs': refs, 'recordKey': key}
    return {'status': 'outcome_unknown', 'recordKey': key}


def collect(ctx, key):
    record = read_value(ctx, key)
    if not isinstance(record, dict) or not isinstance(record.get('items'), list):
        raise ValueError('缺少整理任务记录')
    completed, tasks = 0, {}
    for item in record['items']:
        if not item.get('task_ref'):
            continue
        ref = item['task_ref']
        if ref not in tasks:
            tasks[ref] = ctx.mt.tasks.get(ref=ref)
        task = tasks[ref]
        # A missing failure count is not evidence of successful completion.
        if task.get('status') == 'done' and type(task.get('failed')) is int and task['failed'] == 0:
            item['status'] = 'done'
            completed += 1
        else:
            item['status'] = 'needs_review'
    record['status'] = 'done' if completed == len(record['items']) else 'needs_review'
    write_value(ctx, key, record)
    if record.get('parent_key'):
        parent = read_value(ctx, record['parent_key'])
        parent.update(phase='done' if record['status'] == 'done' else 'organizing',
                      status=record['status'])
        write_value(ctx, record['parent_key'], parent)
    # Keep scan rows: organizing may change paths and duplicate group composition.
    # Only a new scan can reassess the work. Locks remain until explicit recovery.
    return {'status': record['status'], 'completed': completed, 'total': len(record['items']),
            'recordKey': key, 'rescan_required': True}
