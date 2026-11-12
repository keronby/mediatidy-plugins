"""Verified local deletion. CD2 exclusion never follows STRM targets."""
import os

COMPANIONS = ('.nfo', '.jpg', '.png', '-mediainfo.json', '.mediainfo.json', '.xml', '.srt', '.ass')


def delete_local(path, *, follow_target=False, dry_run=False, previous=None, checkpoint=None):
    result = {'ok': False, 'path': path, 'deleted': 0, 'errors': [], 'targets': []}
    if not os.path.isabs(path) or not path.lower().endswith('.strm') or os.path.islink(path):
        result['errors'].append('拒绝非绝对 STRM 路径或符号链接')
        return result
    previous = previous or {}
    completed = list(previous.get('completed', [])) if previous.get('path') == path else []
    result['completed'] = completed
    try:
        if follow_target:
            with open(path, encoding='utf-8', errors='strict') as stream:
                target = stream.readline().strip()
            if not target.lower().startswith(('http://', 'https://')):
                if (not os.path.isabs(target) or '..' in target.split('/')
                        or not os.path.splitext(target)[1].lower() in
                        {'.mkv', '.mp4', '.iso', '.ts', '.m2ts', '.avi', '.mov', '.wmv', '.flv', '.bdmv'}):
                    raise ValueError('STRM 指向不是明确媒体绝对路径，拒绝删除索引')
                if not os.path.isfile(target) and target not in completed:
                    raise ValueError('无法确认源媒体存在，保留 STRM 供核对')
                result['targets'].append(target)
        stem = path[:-5]
        result['targets'].extend(p for p in [stem + ext for ext in COMPANIONS] if os.path.isfile(p))
        result['targets'].append(path)  # index last: partial failure remains retryable
        if dry_run:
            result['ok'] = True
            return result
        for target in result['targets']:
            if target in completed:
                continue
            if os.path.lexists(target):
                os.remove(target)
                if os.path.lexists(target):
                    raise OSError('删除后文件仍存在')
                result['deleted'] += 1
            completed.append(target)
            if checkpoint:
                checkpoint(result)
        result['ok'] = not os.path.lexists(path)
        if checkpoint:
            checkpoint(result)
    except (OSError, ValueError, UnicodeError) as exc:
        result['errors'].append(str(exc))
    return result
