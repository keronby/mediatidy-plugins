"""mt-console mixed-group assessment and file-level loser cleanup.

STRM contents classify links only; mutation paths use explicit work mappings.
"""
import copy
import hashlib
import posixpath
from .mixed import plan_group
from .storage import read_value, write_value
from .submitter import to_mt_path, match_root


def assess(group, rule, cfg=None):
    cfg = cfg or {}
    result = plan_group(group, rule)
    group['mixed_scoring'] = {key: result[key] for key in
                              ('rule_id', 'rule_name', 'chaos', 'remaining_link_types', 'buckets')}
    group['is_chaos_group'] = result['chaos']
    group['mixed_scored'] = True
    for row in group['dirs']:
        entries = [e for e in result['entries'] if e['dir_path'] == row['dir_path']]
        winners = [e for e in entries if e.get('winner') and not e.get('skipped')]
        losers = [e for e in entries if e.get('winner') is False and not e.get('skipped')]
        row.update(winner_strms=winners, loser_strms=losers, delete_entries=losers,
                   winner_count=len(winners), loser_count=len(losers),
                   is_winner_dir=bool(winners), is_loser_dir=bool(losers) and not winners,
                   delete_candidate=bool(losers), chaos_protected=result['chaos'])
        remaining = {e['link_kind'] for e in entries if e not in losers}
        # Retain the naming verdict calculated before mixed-group handling.
        row['need_organize'] = bool(row.get('naming_need_organize')) and bool(winners) and not result['chaos']
        row['disabled'] = bool(result['chaos']) or not winners
        row['disabled_reason'] = '清理败者后仍有两种链接，混乱组保护' if result['chaos'] else ('仅含败者或未参与评分' if not winners else '')
        row['mixed_cleanup_required'] = bool(result['losers'])
        if len(remaining) == 1:
            row['link_type'] = next(iter(remaining))
        # Scoring never selects an organize profile. Reuse normal link routing.
        link = row.get('link_type')
        if winners and link in ('http', 'cloudnas'):
            local = link == 'http'
            row['assigned_profile_id'] = cfg.get('localProfileId' if local else 'cloudProfileId')
            row['assigned_dest_dir'] = cfg.get('localDestDir' if local else 'cloudDestDir', '')
            row['route_desc'] = ('分享链 → 本地整理方案 ' if local else '云盘挂载路径 → 云盘整理方案 ') + str(row['assigned_profile_id'] or '未配置')
        else:
            row.update(assigned_profile_id=None, assigned_dest_dir=None, route_desc='无可整理胜者')
        row['compare_verdict'] = f"胜者 {len(winners)} / 败者 {len(losers)}"
    group['need_organize_count'] = sum(bool(r['need_organize']) for r in group['dirs'])
    group['status'] = '混乱组保护（可按文件清理败者）' if result['chaos'] else '评分完成，先清理败者再复扫整理胜者'
    return result


def cleanup(ctx, group, rule, cfg, roots):
    """Re-score locally, re-stat all sources before any side effect, delete files only."""
    groups = group if isinstance(group, list) else [group]
    # Score each identity separately: never compare unrelated works. Preflight
    # the combined file set before acquiring locks or submitting any delete.
    losers = [entry for current in groups
              for entry in plan_group(copy.deepcopy(current), rule)['losers']]
    limit = min(150, max(1, int((ctx.mt.runtime.info().get('limits') or {}).get('maxWorkflowFiles', 100))))
    if not losers:
        return {'status': 'no_losers', 'rescan_required': True}
    if len(losers) > limit:
        raise ValueError(f'本轮败者文件超过{limit}，请缩小作品范围；未提交')
    items, refs = [], set()
    budget = {'remaining':500, 'list_calls':0}
    cloud_files = {}
    for entry in losers:
        work, file_path = entry['dir_path'], entry['strm_path']
        relative = posixpath.relpath(file_path, work)
        if relative == '..' or relative.startswith('../') or relative.startswith('/'):
            raise ValueError('败者文件越出作品目录')
        source = to_mt_path(work, link_type=entry['link_kind'],
                            strm_local_prefix=cfg.get('strmLocalPrefix', ''),
                            strm_cloud_prefix=cfg.get('strmCloudPrefix', ''))
        # Cloud target filename cannot safely be inferred from a STRM URL. Only
        # accept a unique real video of the same stem via host listing.
        if entry['link_kind'] == 'cloudnas':
            from .organizer import collect_files
            if source not in cloud_files:
                cloud_files[source] = collect_files(ctx.mt, roots, source, True, budget)
            files = cloud_files[source]
            root = match_root(source, roots)
            base = source[len(root['path']):].strip('/')
            stem = posixpath.splitext(posixpath.join(base, relative))[0]
            matches = [f for f in files if posixpath.splitext(f.get('path',''))[0] == stem]
            if len(matches) != 1:
                raise ValueError('云盘败者无法按作品相对路径唯一对应视频，拒绝猜测')
            ref = matches[0]['ref']
        else:
            path = posixpath.join(source, relative)
            root = match_root(path, roots)
            if not root:
                raise ValueError('败者不在显式授权根内')
            stat = ctx.mt.files.stat(root_id=root['id'], path=path[len(root['path']):].lstrip('/'))
            if stat.get('isDir') is not False or not stat.get('ref'):
                raise ValueError('败者源已变化或不是文件，拒绝删除')
            ref = stat['ref']
        if ref in refs:
            raise ValueError('败者文件映射重复，拒绝提交')
        refs.add(ref)
        lock = 'mixed-file-lock/' + hashlib.sha256(ref.encode()).hexdigest()
        if read_value(ctx, lock) is not None:
            raise ValueError('该文件已有清理记录，请核对原任务')
        items.append({'file_ref':ref, 'path':file_path, 'lock':lock, 'status':'pending'})
    key = 'mixed-runs/' + ctx.flow_id
    if read_value(ctx, key) is not None:
        raise ValueError('禁止重复提交同一流程')
    record = {'status':'submitting','items':items,'rule_id':rule.get('entry_id')}
    write_value(ctx, key, record)
    for item in items:
        ctx.mt.storage.set(key=item['lock'],value={'recordKey':key},expected_revision='')
    tasks = []
    for item in items:
        item['status'] = 'submitting'
        write_value(ctx,key,record)
        try:
            task = ctx.mt.files.delete(file_ref=item['file_ref'], recursive=False,
                         idempotency_key='mixed-delete:'+hashlib.sha256(item['file_ref'].encode()).hexdigest())
            if not isinstance(task,dict) or not task.get('ref'):
                raise ValueError('删除结果未知，禁止自动重试')
            item.update(status='waiting',task_ref=task['ref'])
            tasks.append(task['ref'])
        except Exception as exc:
            item.update(status='outcome_unknown',error=str(exc))
            break
        write_value(ctx,key,record)
    record['status'] = 'waiting' if tasks else 'outcome_unknown'
    write_value(ctx,key,record)
    if tasks:
        ctx.mt.tasks.wait(refs=list(dict.fromkeys(tasks)),completion={'action':'collect-mixed','input':{'key':key}})
        return {'status':'waiting','recordKey':key,'files':len(tasks)}
    return {'status':'outcome_unknown','recordKey':key}


def collect(ctx, key):
    record = read_value(ctx,key)
    if not isinstance(record,dict) or not isinstance(record.get('items'),list):
        raise ValueError('缺少混合清理任务记录')
    done = 0
    for item in record['items']:
        if not item.get('task_ref'):
            continue
        task = ctx.mt.tasks.get(ref=item['task_ref'])
        ok = task.get('status') == 'done' and type(task.get('failed')) is int and task['failed'] == 0
        item['status'] = 'done' if ok else 'needs_review'
        done += int(ok)
    record['status'] = 'done' if done == len(record['items']) else 'needs_review'
    write_value(ctx,key,record)
    return {'status':record['status'],'deleted_files':done,'rescan_required':True,
            'message':'请重新扫描核对剩余胜者，再提交整理；原目录没有整目录删除'}
