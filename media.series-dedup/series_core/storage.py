"""Strict KV primitives: no hidden truncation or swallowed host errors."""
import json
from typing import Any


def read_value(ctx: Any, key: str, default: Any = None) -> Any:
    result = ctx.mt.storage.get(key=key)
    if not isinstance(result, dict) or not isinstance(result.get('found'), bool):
        raise ValueError(f'storage.get 返回结构无效: {key}')
    return result.get('value') if result['found'] else default


def write_value(ctx: Any, key: str, value: Any, max_bytes: int = 256 * 1024) -> bool:
    # ensure_ascii=True conservatively includes escaping overhead in the budget.
    size = len(json.dumps(value, ensure_ascii=True).encode('utf-8'))
    limit = min(max_bytes or 256 * 1024, 256 * 1024)
    if size > limit:
        raise ValueError(f'storage.set {key}: {size} bytes 超过 {limit} bytes；请分片，禁止截断')
    ctx.mt.storage.set(key=key, value=value)
    return True


def write_parts(ctx: Any, prefix: str, values: list, budget: int = 120000) -> dict:
    """Write immutable byte-sized parts; caller publishes descriptor last."""
    parts, chunk, size = [], [], 2
    # Preflight every item before writing, including one oversized group.
    encoded = [len(json.dumps(v, ensure_ascii=True).encode('utf-8')) for v in values]
    if any(n + 2 > budget for n in encoded):
        raise ValueError('单条结果超过分片上限，请拆分该条目，禁止截断')
    for value, length in zip(values, encoded):
        if chunk and size + length + 2 > budget:
            key = f'{prefix}/{len(parts):06d}'
            write_value(ctx, key, chunk, budget)
            parts.append(key)
            chunk, size = [], 2
        chunk.append(value)
        size += length + 2
    if chunk:
        key = f'{prefix}/{len(parts):06d}'
        write_value(ctx, key, chunk, budget)
        parts.append(key)
    return {'parts': parts, 'count': len(values)}


def read_parts(ctx: Any, descriptor: dict) -> list:
    result = []
    for key in descriptor['parts']:
        part = read_value(ctx, key)
        if not isinstance(part, list):
            raise ValueError(f'结果分片缺失或损坏: {key}')
        result.extend(part)
    if len(result) != descriptor['count']:
        raise ValueError('结果分片计数不匹配，禁止作为完整结果使用')
    return result


def publish_snapshot(ctx: Any, pointer_key: str, meta: dict, datasets: dict) -> dict:
    """Copy-on-write: old descriptor remains readable until all parts are saved.

    Reuse unchanged datasets, allowing metadata/status updates without copying all
    rows. Retained generations need explicit retention/GC before production use.
    """
    from uuid import uuid4
    previous = read_value(ctx, pointer_key, {})
    descriptors = dict(previous.get('datasets', {})) if isinstance(previous, dict) else {}
    generation = uuid4().hex
    for name, values in datasets.items():
        descriptors[name] = write_parts(ctx, f'scans/{generation}/{name}', values)
    snapshot = {'format': 1, 'generation': generation, 'meta': meta, 'datasets': descriptors}
    write_value(ctx, pointer_key, snapshot)
    return snapshot


def read_page(ctx: Any, descriptor: dict, offset: int = 0,
              limit: int = 20, budget: int = 35000) -> dict:
    """Bound row count AND JSON bytes without loading the whole dataset."""
    offset = max(0, int(offset))
    rows, seen, used = [], 0, 2
    for key in descriptor['parts']:
        part = read_value(ctx, key)
        if not isinstance(part, list):
            raise ValueError(f'结果分片缺失或损坏: {key}')
        for row in part:
            seen += 1
            if seen <= offset:
                continue
            size = len(json.dumps(row, ensure_ascii=True).encode('utf-8')) + 2
            if size + 2 > budget:
                raise ValueError('单条结果超过页面预算，请拆分明细后再展示')
            if len(rows) >= limit or used + size > budget:
                return {'rows': rows, 'next': offset + len(rows), 'count': descriptor['count']}
            rows.append(row)
            used += size
    if seen != descriptor['count']:
        raise ValueError('结果分片计数不匹配')
    return {'rows': rows, 'next': None, 'count': descriptor['count']}


def collect_old_parts(ctx: Any, pointer_key: str, cursor: str = '') -> dict:
    """Bounded maintenance: delete only unreferenced parts older than 24 hours.

    One page per explicit invocation; cursor comes from the host, never an offset.
    Unknown timestamps are retained. No task journals/locks/protection data touched.
    """
    from datetime import datetime, timezone, timedelta
    snapshot = read_value(ctx, pointer_key, {})
    def referenced(value):
        if not isinstance(value, dict) or not isinstance(value.get('datasets', {}), dict):
            raise ValueError('快照指针格式错误，禁止回收')
        return {key for descriptor in value.get('datasets', {}).values()
                for key in descriptor['parts']}
    keep = referenced(snapshot)
    response = ctx.mt.storage.list(prefix='scans/', cursor=cursor, limit=100, values=False)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    deleted, retained = 0, 0
    for entry in response['items']:
        key = entry['key']
        if not key.startswith('scans/') or key in keep:
            retained += 1
            continue
        try:
            timestamp = datetime.fromisoformat(entry.get('updatedAt', '').replace('Z', '+00:00'))
            if timestamp.tzinfo is None or timestamp >= cutoff:
                retained += 1
                continue
        except (TypeError, ValueError):
            retained += 1
            continue
        if not entry.get('revision'):
            retained += 1
            continue
        # Re-read protects parts referenced by a newly published snapshot.
        if key in referenced(read_value(ctx, pointer_key, {})):
            retained += 1
            continue
        ctx.mt.storage.delete(key=key, expected_revision=entry['revision'])
        deleted += 1
    return {'deleted': deleted, 'retained': retained, 'nextCursor': response.get('nextCursor', '')}
