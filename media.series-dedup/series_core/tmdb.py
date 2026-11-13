# -*- coding: utf-8 -*-
"""通过 SDK 查询宿主 TMDB 元数据；不提供强制刷新。"""

import logging
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("media-series-dedup")

# 官方契约：TMDB 元数据唯一通过 ctx.mt.tmdb.detail（action ctx）获取。
# 不再猜密码登录 MT HTTP API，也不再直连宿主 SQLite 数据库。


def query_mt_tmdb_caches(targets: Any, ctx: Optional[Any] = None) -> Dict[Any, Dict[str, Any]]:
    """批量读取 TMDB 元数据：唯一通过官方 SDK ctx.mt.tmdb.detail 获取。
    支持传入 [(media_type, tmdb_id)] 元组列表、tmdb_id 整数列表或字典结构。
    返回同时支持 (media_type, tmdb_id)、f"{media_type}:{tmdb_id}" 及 tmdb_id 索引。
    """
    results: Dict[Any, Dict[str, Any]] = {}
    if not targets:
        return results

    # 规范化目标列表 [(media_type, tmdb_id)]
    target_pairs = []
    raw_ids = set()
    for item in targets:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                mtype = str(item[0] or "tv").lower()
                tid = int(item[1])
                target_pairs.append((mtype, tid))
                raw_ids.add(tid)
            except Exception:
                pass
        elif isinstance(item, str) and ":" in item:
            try:
                parts = item.split(":")
                mtype = str(parts[0] or "tv").lower()
                tid = int(parts[-1])
                target_pairs.append((mtype, tid))
                raw_ids.add(tid)
            except Exception:
                pass
        else:
            try:
                tid = int(item)
                target_pairs.append(("tv", tid))
                raw_ids.add(tid)
            except Exception:
                pass

    if not raw_ids:
        return results

    # 1. 若宿主上下文提供了 tmdb 模块支持，优先尝试
    if ctx and hasattr(ctx, "mt") and hasattr(ctx.mt, "tmdb") and hasattr(ctx.mt.tmdb, "detail"):
        for mtype, tid in target_pairs:
            try:
                dt = ctx.mt.tmdb.detail(id=tid, mediaType=mtype)
                if isinstance(dt, dict) and dt.get("id"):
                    y_str = dt.get("year")
                    y_int = int(y_str) if y_str and str(y_str).isdigit() else None
                    info = {
                        "tmdb_id": int(dt["id"]),
                        "cache_id": str(dt.get("id")),
                        "media_type": str(dt.get("mediaType") or mtype).lower(),
                        "title": dt.get("title") or "",
                        "original_title": dt.get("originalTitle") or "",
                        "year": y_int,
                        "poster_path": dt.get("posterPath") or "",
                        "updated_at": "",
                    }
                    results[(mtype, tid)] = info
                    results[f"{mtype}:{tid}"] = info
                    if tid not in results:
                        results[tid] = info
            except Exception:
                pass

    # 不再直连 SQLite：官方契约以 ctx.mt.tmdb.detail 为唯一数据源
    return results


def refresh_single_tmdb(
    client: Optional[Any] = None,
    tmdb_id: int = 0,
    media_type: str = "tv",
    ctx: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """通过官方 SDK ctx.mt.tmdb.detail 读取最新元数据。

    官方契约 tmdb.detail 只在 action ctx 可用，不保证宿主绕过缓存；不再使用
    HTTP 猜密码客户端或直连 SQLite。
    """
    if not tmdb_id:
        return None
    mtype_req = "movie" if str(media_type or "tv").lower() == "movie" else "tv"
    if ctx is None or not hasattr(ctx, "mt") or not hasattr(ctx.mt, "tmdb"):
        logger.warning("refresh_single_tmdb 需要 ctx.mt.tmdb.detail（action ctx），当前无 ctx，跳过刷新")
        return None
    try:
        dt = ctx.mt.tmdb.detail(id=tmdb_id, mediaType=mtype_req)
    except Exception as e:
        logger.warning("tmdb.detail 调用失败 %s #%s: %s", mtype_req, tmdb_id, e)
        return None
    if not isinstance(dt, dict) or not dt.get("id"):
        return None
    y_val = dt.get("year")
    y_int = int(y_val) if y_val and str(y_val).isdigit() else None
    logger.info("已通过 SDK 查询 TMDB 元数据: %s #%s -> 《%s》 (%s)", mtype_req, tmdb_id, dt.get("title"), y_int)
    return {
        "tmdb_id": tmdb_id,
        "media_type": mtype_req,
        "title": dt.get("title") or "",
        "original_title": dt.get("originalTitle") or "",
        "year": y_int,
        "poster_path": dt.get("posterPath") or "",
    }


def refresh_tmdb_batch(
    targets: Any,
    client: Optional[Any] = None,
    max_workers: int = 5,
    stop_check_fn=None,
    progress_callback=None,
    ctx: Optional[Any] = None
) -> Dict[Any, Dict[str, Any]]:
    """并发批量重新查询宿主 TMDB 元数据（经 ctx.mt.tmdb.detail）。支持传入 [(media_type, tmdb_id)] 或 tmdb_id 列表。"""
    results = {}
    target_list = []

    for it in (targets or []):
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            target_list.append((str(it[0]).lower(), int(it[1])))
        elif isinstance(it, str) and ":" in it:
            parts = it.split(":")
            target_list.append((parts[0].lower(), int(parts[-1])))
        else:
            try:
                target_list.append(("tv", int(it)))
            except Exception:
                pass

    total = len(target_list)

    def _worker(item):
        if stop_check_fn and stop_check_fn():
            return None
        mtype, tid = item
        return refresh_single_tmdb(client=client, tmdb_id=tid, media_type=mtype, ctx=ctx)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, item): item for item in target_list}
        done_cnt = 0
        for f in futures:
            if stop_check_fn and stop_check_fn():
                break
            mtype, tid = futures[f]
            try:
                res = f.result()
                if res:
                    results[(mtype, tid)] = res
                    results[f"{mtype}:{tid}"] = res
                    if tid not in results:
                        results[tid] = res
            except Exception as e:
                logger.warning("并发任务执行异常: %s", e)
            done_cnt += 1
            if progress_callback:
                progress_callback(done_cnt, total)

    return results
