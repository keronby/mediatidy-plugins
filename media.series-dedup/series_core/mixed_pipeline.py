"""Durable full mixed pipeline: source -> index -> shells -> exact winners.

Native local cleanup uses lstat fingerprints and unlink/rmdir only. It never
recursively removes directories, follows symlinks, or derives paths from URLs.
"""
import hashlib
import os
import posixpath
import stat
from pathlib import Path
from .mixed import plan_group, collect_entries
from .storage import read_value, write_value
from .local_source import saved_path
from .submitter import match_root, to_mt_path
from . import organizer
from .mt_files import MTTasksPending

COMPANIONS = ('.nfo', '.jpg', '.png', '-mediainfo.json', '.mediainfo.json')
# Only known directory artwork, captured during preflight, never arbitrary files.
SHELL_ARTWORK = frozenset(stem + ext for stem in
    ('poster', 'fanart', 'folder', 'banner', 'landscape', 'thumb', 'clearlogo', 'logo', 'disc')
    for ext in ('.jpg', '.jpeg', '.png', '.webp'))


def fingerprint(path, boundary):
    path, boundary = Path(path), Path(boundary)
    if not boundary.is_absolute() or boundary == Path('/') or path == boundary or boundary not in path.parents:
        raise ValueError('本地文件越出扫描根目录')
    for parent in [path, *path.parents]:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('拒绝符号链接或路径替换')
        if parent == boundary:
            break
    if not stat.S_ISREG(info.st_mode):
        # info currently belongs to boundary, so inspect the file separately.
        info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('清理目标不是普通文件')
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def mapped_work(work, cfg):
    prefix, target = cfg.get('strmLocalPrefix','').rstrip('/'), cfg.get('strmCloudPrefix','').rstrip('/')
    from .mt_files import mapped_path
    mapped_path(target)
    if not prefix or not work.startswith(prefix + '/'):
        raise ValueError('作品未命中明确的本地→云盘虚拟路径映射')
    return mapped_path(target + work[len(prefix):])


def cloud_tree(client, work):
    queue, files, seen = [work], [], set()
    while queue:
        path = queue.pop(0)
        if path in seen or len(seen) >= 60:
            raise ValueError('云盘目录循环或超过60目录')
        seen.add(path)
        for item in client.list_files(path):
            if item.get('isDir'):
                queue.append(item['path'])
            else:
                files.append(item)
            if len(files) > 500:
                raise ValueError('单次云端预检超过500文件，请分区处理')
    return files, seen


def exact_cloud(client, work, entry, cache):
    if work not in cache:
        cache[work] = cloud_tree(client, work)
    relative = posixpath.relpath(entry['strm_path'], entry['dir_path'])
    stem = posixpath.splitext(posixpath.join(work, relative))[0]
    matches = [f['path'] for f in cache[work][0] if
               posixpath.splitext(f['path'])[0] == stem and
               f['path'].lower().endswith(organizer.VIDEO)]
    if len(matches) != 1:
        raise ValueError('云盘视频无法按作品相对路径唯一对应；不使用STRM目标地址')
    return matches[0]


def prepare(ctx, groups, rule, cfg, roots, source_client=None):
    """Validate the entire batch and persist all intent before any side effect."""
    key = 'mixed-pipelines/' + ctx.flow_id
    if read_value(ctx,key) is not None:
        raise ValueError('禁止重放已有混合流程')
    cfg = dict(cfg)
    cfg['sourceBackend'] = 'mt'
    boundary = saved_path(cfg)
    plans = [(g, plan_group(g,rule)) for g in groups]
    entries = [e for _,p in plans for e in p['entries']]
    if len(entries) > 40:
        raise ValueError('完整混合处理每轮最多40个索引文件（含跳过项），为清理和整理保留记录预算；本地扫描不限量')
    if any(e['link_kind']=='cloudnas' for e in entries) and source_client is None:
        from .mt_files import MTFiles
        source_client = MTFiles(ctx, roots, key + '/mt-deletes')
    cache, items, retained, locks, cloud_dirs, works = {}, [], [], [], set(), set()
    seen = set()
    for group, result in plans:
        for entry in result['entries']:
            path = entry['strm_path']; work = entry['dir_path']
            if path in seen:
                raise ValueError('所选作品文件重叠')
            seen.add(path); works.add(work)
            identity = fingerprint(path,boundary)
            lock = 'mixed-path-lock/' + hashlib.sha256(path.encode()).hexdigest()
            existing = read_value(ctx,lock)
            if existing is not None:
                owner = existing.get('recordKey') if isinstance(existing, dict) else None
                raise ValueError('该路径已有清理记录，禁止新建删除；请在核验续接入口使用原记录：' +
                                 str(owner or '记录损坏，需人工核对'))
            locks.append(lock)
            is_loser = entry.get('winner') is False and not entry.get('skipped')
            cloud = None
            if entry['link_kind']=='cloudnas':
                mapped = mapped_work(work,cfg)
                cloud = exact_cloud(source_client,mapped,entry,cache)
                cloud_dirs.update(cache[mapped][1])
            if not is_loser:
                retained.append({'path':path,'identity':identity,'cloud':cloud,
                                 'winner':bool(entry.get('winner')), 'work':work,
                                 'link':entry['link_kind'],'tmdb_id':group['tmdb_id'],
                                 'media_type':group['media_type'],'chaos':result['chaos']})
                continue
            local = [{'path':path,'identity':identity}]
            stem = os.path.splitext(path)[0]
            for suffix in COMPANIONS:
                companion = stem + suffix
                if os.path.lexists(companion):
                    local.append({'path':companion,'identity':fingerprint(companion,boundary)})
            if cloud:
                root = match_root(to_mt_path(work,link_type='cloudnas',
                    strm_local_prefix=cfg.get('strmLocalPrefix',''),
                    strm_cloud_prefix=cfg.get('strmCloudPrefix','')), roots)
                if not root:
                    raise ValueError('云盘胜者/败者缺少整理授权根绑定')
            cloud_sources = [cloud] if cloud else []
            if cloud:
                cloud_stem = posixpath.splitext(cloud)[0]
                companions = {cloud_stem + suffix for suffix in COMPANIONS}
                cloud_sources += [f['path'] for f in cache[mapped][0] if f['path'] in companions]
            items.append({'path':path,'work':work,'cloud':cloud,'cloud_sources':cloud_sources,'local':local,
                          'lock':lock,'status':'pending'})
    # A loser-only work may contain directory artwork in addition to per-file
    # companions. Capture exact identities/remote paths BEFORE any deletion.
    retained_works = {item['work'] for item in retained}
    for work in sorted({item['work'] for item in items} - retained_works):
        owner = next(item for item in items if item['work'] == work)
        known_local = {local['path'] for item in items for local in item['local']}
        for name in sorted(os.listdir(work)):
            path = os.path.join(work, name)
            if name.lower() in SHELL_ARTWORK and path not in known_local:
                owner['local'].append({'path': path, 'identity': fingerprint(path, boundary)})
        if owner['cloud']:
            mapped = mapped_work(work, cfg)
            for remote in cache[mapped][0]:
                if (posixpath.dirname(remote['path']) == mapped and
                        posixpath.basename(remote['path']).lower() in SHELL_ARTWORK and
                        remote['path'] not in owner['cloud_sources']):
                    owner['cloud_sources'].append(remote['path'])
    retained_paths = {item['path'] for item in retained}
    local_deletions = [local['path'] for item in items for local in item['local']]
    if retained_paths.intersection(local_deletions) or len(set(local_deletions)) != len(local_deletions):
        raise ValueError('伴随文件与保留文件重叠，禁止清理')
    retained_cloud = {item['cloud'] for item in retained if item['cloud']}
    cloud_deletions = [path for item in items for path in item['cloud_sources']]
    if retained_cloud.intersection(cloud_deletions) or len(set(cloud_deletions)) != len(cloud_deletions):
        raise ValueError('云端删除清单与保留文件重叠')
    if len(cloud_dirs) > 60:
        raise ValueError("单轮云端目录合计超过60，请缩小范围")
    # Validate organize routes and roots before deleting anything, even though
    # final fileRefs are resolved again after cleanup and synchronization.
    for item in retained:
        if item['winner'] and not item['chaos']:
            profile = cfg.get('localProfileId' if item['link']=='http' else 'cloudProfileId')
            source = to_mt_path(item['work'],link_type=item['link'],
                strm_local_prefix=cfg.get('strmLocalPrefix',''), strm_cloud_prefix=cfg.get('strmCloudPrefix',''))
            if not profile or not match_root(source,roots):
                raise ValueError('胜者缺少常规整理方案或显式授权根绑定')
    record = {'status':'locking','locks_ready':False,'locks':locks,'items':items,'retained':retained,'cfg':cfg,'roots':roots,
              'boundary':boundary,'works':sorted(works),'cloud_dirs':sorted(cloud_dirs),
              'phase':'sources','rule_id':rule.get('entry_id')}
    write_value(ctx,key,record)
    for lock in locks:
        ctx.mt.storage.set(key=lock,value={'recordKey':key},expected_revision='')
    record.update(status='prepared', locks_ready=True)
    write_value(ctx,key,record)
    return key, source_client


def absent(client, path):
    parent, name = posixpath.split(path)
    return not any(item['name']==name for item in client.list_files(parent))


def advance(ctx, key, source_client=None):
    """Resume verification, never replay an uncertain DeleteFiles request."""
    record = read_value(ctx,key)
    if not isinstance(record,dict):
        raise ValueError('混合流程记录不存在')
    if record.get('locks_ready') is not True:
        raise ValueError('混合流程未完成全部加锁，禁止续接删除')
    for lock in record['locks']:
        if read_value(ctx,lock) != {'recordKey':key}:
            raise ValueError('混合流程锁归属已变化，禁止继续')
    if record['phase'] == 'organizing':
        raise ValueError('胜者整理已提交，请核对整理任务，禁止重放')
    if record['phase'] == 'done':
        return {'status':'done','recordKey':key,'rescan_required':True}
    if record['phase'] == 'organize_unknown':
        raise ValueError('整理结果未知，禁止重放')
    if record['cloud_dirs'] and source_client is None:
        if record['cfg'].get('sourceBackend') == 'mt':
            from .mt_files import MTFiles
            source_client = MTFiles(ctx, record['roots'], key + '/mt-deletes')
        else:
            raise ValueError('旧云盘流程禁止自动切换路径，请人工核对原任务后重新扫描')
    def save(): write_value(ctx,key,record)
    try:
        # Validate all retained identities before the first destructive call.
        for item in record['retained']:
            if fingerprint(item['path'],record['boundary']) != item['identity']:
                raise ValueError('胜者或跳过项已变化，停止清理')
        for item in record['items']:
            if item['status']=='pending':
                # Check every local identity again before source deletion.
                for local in item['local']:
                    if fingerprint(local['path'],record['boundary']) != local['identity']:
                        raise ValueError('败者或伴随文件已变化，停止处理')
                if item['cloud']:
                    item['status']='source_unknown'; save()
                    source_client.delete_files(item['cloud_sources'])
                    item['status']='source_sent'; save()
                else:
                    item['status']='source_gone'; save()
            if item['cloud'] and item['status'] in ('source_unknown','source_sent'):
                if any(not absent(source_client,path) for path in item['cloud_sources']):
                    record['status']='waiting_sync'; save()
                    return {'status':'waiting_sync','recordKey':key,'message':'云端源尚未确认消失；可核验续接，不重发删除'}
                item['status']='source_gone'; save()
            if item['status']=='source_gone':
                for local in item['local']:
                    if os.path.lexists(local['path']):
                        if fingerprint(local['path'],record['boundary']) != local['identity']:
                            raise ValueError('本地索引已变化，禁止清理')
                        os.unlink(local['path'])
                item['status']='local_done'; save()
        record['phase']='shells'; save()
        # Preserve every winner and skipped file. No cleanup may consume them.
        for item in record['retained']:
            if fingerprint(item['path'],record['boundary']) != item['identity']:
                raise ValueError('胜者或跳过项已变化，停止整理')
            if item['cloud'] and absent(source_client,item['cloud']):
                raise ValueError('云端胜者消失，停止整理')
        # Loser-only trees may contain arbitrary sidecars. A complete recursive
        # read must prove no STRM/video remains before deleting those sidecars.
        from .empty_shell import cloud_inventory, clean_local
        retained_cloud_works = {mapped_work(x['work'],record['cfg'])
                                for x in record['retained'] if x['cloud']}
        loser_cloud_works = {mapped_work(x['work'],record['cfg'])
                             for x in record['items'] if x['cloud']}
        for work in sorted(loser_cloud_works - retained_cloud_works):
            if absent(source_client,work):
                continue
            files, directories = cloud_inventory(source_client,work)
            record['cloud_dirs'] = sorted(set(record['cloud_dirs']) | set(directories))
            intents = record.setdefault('shell_file_intents', [])
            # Already submitted paths are verified, never reissued.
            if any(not absent(source_client,p) for p in intents):
                record['status']='waiting_sync'; save()
                return {'status':'waiting_sync','recordKey':key}
            if files:
                if cloud_inventory(source_client,work) != (files,directories):
                    raise ValueError('云端败者空壳已变化')
                intents.extend(files); save()
                source_client.delete_files(files)
                if any(not absent(source_client,p) for p in files):
                    record['status']='waiting_sync'; save()
                    return {'status':'waiting_sync','recordKey':key}
        # Remove truly empty mapped directories bottom up; never recursive delete.
        removed = record.setdefault('removed_cloud_dirs',[])
        for path in sorted(record['cloud_dirs'],key=lambda p:p.count('/'),reverse=True):
            if any(item['cloud'] and item['cloud'].startswith(path.rstrip('/') + '/')
                   for item in record['retained']):
                continue
            if path in removed:
                continue
            if absent(source_client,path):
                removed.append(path)
                if record.get('empty_delete_unknown') == path:
                    record.pop('empty_delete_unknown', None)
                save(); continue
            if not source_client.list_files(path):
                pending = record.get('empty_delete_unknown')
                if pending == path:
                    record['status']='waiting_sync'; save()
                    return {'status':'waiting_sync','recordKey':key}
                record['empty_delete_unknown']=path; save()
                # Repeat emptiness check immediately before the RPC.
                if source_client.list_files(path):
                    raise ValueError('云盘空目录已变化')
                source_client.delete_files([path])
                if not absent(source_client,path):
                    record['status']='waiting_sync'; save()
                    return {'status':'waiting_sync','recordKey':key}
                removed.append(path); record.pop('empty_delete_unknown',None); save()
        retained_cloud_works = {mapped_work(item['work'],record['cfg']) for item in record['retained'] if item['cloud']}
        loser_cloud_works = {mapped_work(item['work'],record['cfg']) for item in record['items'] if item['cloud']}
        for work in loser_cloud_works - retained_cloud_works:
            if not absent(source_client,work):
                raise ValueError('云端败者目录仍有文件或同步未确认，停止胜者整理')
        retained_works = {item['work'] for item in record['retained']}
        for work in record['works']:
            if work not in retained_works and os.path.lexists(work):
                clean_local(work, record['boundary'])
            if not os.path.exists(work):
                continue
            fingerprint_dir(work,record['boundary'])
            for root, dirs, files in os.walk(work,topdown=False,followlinks=False):
                for name in dirs:
                    child = os.path.join(root,name)
                    fingerprint_dir(child,record['boundary'])
                    if not os.listdir(child): os.rmdir(child)
                if not os.listdir(root): os.rmdir(root)
        retained_works = {item['work'] for item in record['retained']}
        for work in set(record['works']) - retained_works:
            if os.path.lexists(work):
                raise ValueError('败者目录仍有未知文件，停止胜者整理')
        actual = {entry['strm_path'] for work in record['works'] if os.path.isdir(work)
                  for entry in collect_entries(work)}
        expected = {item['path'] for item in record['retained']}
        if actual != expected:
            raise ValueError('清理后STRM集合已变化，停止整理并重新扫描')
        record['phase']='winners'; save()
        rows = []
        for item in record['retained']:
            if not item['winner'] or item['chaos']:
                continue
            row = {'dir_path':item['work'],'link_type':item['link'],'media_type':item['media_type'],
                   'tmdb_id':item['tmdb_id'],'allowed_winner_paths':[item['path']]}
            rows.append(row)
        # Collapse same-work winners into one exact allowlist, never skipped refs.
        merged = {}
        for row in rows:
            identity=(row['dir_path'],row['link_type'])
            if identity in merged:
                merged[identity]['allowed_winner_paths'] += row['allowed_winner_paths']
            else: merged[identity]=row
        if not merged:
            record.update(phase='done',status='protected' if record['retained'] else 'done'); save()
            return {'status':record['status'],'recordKey':key,'rescan_required':True}
        items = organizer.plan(ctx,list(merged.values()),record['cfg'],record['roots'])
        record.update(phase='organize_unknown',status='submitting_winners'); save()
        # Persist parent linkage before tasks.wait; no RPC may follow registration.
        return organizer.start(ctx,items,parent_key=key)
    except MTTasksPending as pending:
        record['status'] = 'waiting_tasks'
        record.pop('error', None)
        save()
        return wait_pending(ctx, key, record, pending.refs)
    except Exception as exc:
        record['status']='needs_review'
        record['error']=str(exc)
        save()
        raise


def fingerprint_dir(path,boundary):
    path, boundary = Path(path), Path(boundary)
    if path == boundary or boundary not in path.parents:
        raise ValueError('禁止清理扫描根外目录')
    for parent in [path,*path.parents]:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError('拒绝符号链接或非目录空壳')
        if parent == boundary: break


def start(ctx, groups, rule, cfg, roots):
    key, client = prepare(ctx,groups,rule,cfg,roots)
    return advance(ctx,key,client)


def collect(ctx, key):
    """Continuation dispatcher for source cleanup and winner organization."""
    record = read_value(ctx, key)
    if not isinstance(record, dict):
        raise ValueError('混合流程记录不存在')
    # Pre-1.4.11 records have already entered collect-mixed. Never select it again.
    if 'wait_step' not in record:
        record['wait_step'] = 1
        write_value(ctx, key, record)
    phase = record.get('phase')
    if phase == 'organizing':
        child = record.get('organize_key')
        if not isinstance(child, str):
            raise ValueError('混合流程缺少整理任务记录')
        result = organizer.collect(ctx, child)
        record = read_value(ctx, key)
        if result.get('status') == 'done':
            record['phase'] = 'done'
            record['status'] = 'done'
        else:
            record['status'] = 'needs_review'
        write_value(ctx, key, record)
        return dict(result, parentKey=key, rescan_required=True)
    if phase in ('sources', 'shells', 'winners', 'waiting_tasks'):
        return advance(ctx, key)
    if phase == 'waiting_sync':
        return advance(ctx, key)
    if phase in ('organize_unknown', 'done'):
        return {'status': record.get('status', phase), 'recordKey': key,
                'rescan_required': True}
    raise ValueError('混合流程处于未知阶段，禁止重放')


def wait_pending(ctx, key, record, refs):
    """Each wait uses a distinct declared action; never form an event cycle."""
    step = record.get('wait_step', 0)
    if type(step) is not int or step < 0 or step >= 20:
        record.update(status='waiting_manual', error='本轮续接预算已用尽；保留任务引用，禁止重发删除')
        write_value(ctx, key, record)
        return {'status':'waiting_manual','recordKey':key,'taskRefs':refs}
    action = 'collect-mixed' if step == 0 else f'collect-mixed-{step}'
    record['wait_step'] = step + 1
    write_value(ctx, key, record)
    ctx.mt.tasks.wait(refs=refs, completion={'action':action,'input':{'key':key}})
    # Absolutely no SDK call after wait registration.
    return {'status':'waiting_tasks','recordKey':key,'taskRefs':refs}
