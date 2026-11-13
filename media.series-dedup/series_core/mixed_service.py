"""Authorized mixed-link review; never executes the resulting deletion plan."""
import copy
import hashlib
from .roots import authorized_bindings
from .submitter import match_root
from .mixed import plan_group
from .storage import write_parts, write_value


def selected_rule(sdk, rule_id):
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise ValueError('必须显式选择 MT 质量规则 ID')
    record = sdk.rules.get(id=rule_id, family='quality')
    if not isinstance(record, dict) or record.get('id') != rule_id or record.get('family') != 'quality' or record.get('enabled') is not True:
        raise ValueError('评分规则不存在、类型不符或已停用')
    entry = record.get('definition')
    if not isinstance(entry, dict) or not isinstance(entry.get('scoring'), dict) or not entry['scoring']:
        raise ValueError('评分规则格式不受支持，禁止使用默认空评分')
    # Same conversion as MTC core.config._build_rules_from_entry.
    version = entry.get('version') or {}
    slots = []
    for slot in version.get('slots') or []:
        slots.append({'name': slot.get('name', 'normal'), 'match': slot.get('match', {}),
                      'scoring': slot.get('scoring') or entry['scoring'],
                      'exclude': slot.get('exclude') or entry.get('exclude', {}),
                      'min_score_diff': slot.get('min_score_diff', entry.get('min_score_diff', 8.0))})
    return {'min_score_diff': float(entry.get('min_score_diff', 8.0)),
            'scoring': copy.deepcopy(entry['scoring']), 'exclude': entry.get('exclude', {}),
            'slots': slots, 'unmatched': version.get('unmatched', 'skip'),
            'entry_id': record['id'], 'entry_name': record.get('name', ''),
            'revision': record.get('revision', '')}


def configured_rule(sdk, business_config):
    """Use the normal quality-rule selection, with an advanced-settings override."""
    explicit = business_config.get('qualityRuleId')
    if explicit:
        return selected_rule(sdk, str(explicit))
    ids = {int(business_config[key]) for key in ('localProfileId', 'cloudProfileId')
           if business_config.get(key)}
    if not ids:
        raise ValueError('请先配置常规本地/云盘整理方案')
    selected = set()
    for profile_id in sorted(ids):
        effective = sdk.rules.effective(profile_id=profile_id)
        identity = (effective.get('selection') or {}).get('qualityRuleId')
        if not identity:
            raise ValueError('未选择质量规则，请在高级设置中选择或在 MT 整理方案中配置')
        selected.add(identity)
    if len(selected) != 1:
        raise ValueError('常规整理方案使用不同质量规则，无法跨方案比较；请在 MT 中统一质量规则')
    return selected_rule(sdk, next(iter(selected)))


def preview(ctx, group, rule_id):
    if group.get('media_type') not in ('movie', 'tv') or not group.get('tmdb_id'):
        raise ValueError('缺少明确作品身份')
    dirs = group.get('dirs', [])
    if not dirs or len(dirs) > 20:
        raise ValueError('评分预览每次最多20个作品目录')
    config = ctx.mt.config.get()
    roots = authorized_bindings(ctx.mt, config.get('rootBindings', []))
    for directory in dirs:
        path = directory.get('dir_path', '')
        root = match_root(path, roots)
        if not root:
            raise ValueError('评分目录不属于显式授权绑定')
        relative = path[len(root['path']):].lstrip('/') or '.'
        stat = ctx.mt.files.stat(root_id=root['id'], path=relative)
        if not isinstance(stat, dict) or stat.get('isDir') is not True:
            raise ValueError('评分源目录已变化，请重新扫描')
    rule = selected_rule(ctx.mt, rule_id)
    result = plan_group(copy.deepcopy(group), rule)
    identity = str(group['media_type']) + ':' + str(group['tmdb_id'])
    key = 'mixed-plans/' + hashlib.sha256(identity.encode()).hexdigest()
    meta = {'group_key': identity, 'rule_id': rule_id, 'rule_revision': rule['revision'],
            'chaos': result['chaos'], 'planning_only': True,
            'winners': len(result['winners']), 'losers': len(result['losers']),
            'skipped': len(result['skipped'])}
    from uuid import uuid4
    prefix = 'mixed-data/' + uuid4().hex
    datasets = {name: write_parts(ctx, prefix + '/' + name, result[name]) for name in ('entries', 'buckets')}
    write_value(ctx, key, {'meta': meta, 'datasets': datasets})
    return dict(meta, recordKey=key)
