# -*- coding: utf-8 -*-
"""MT TMDB 缓存直读与强制同步刷新模块。"""

import os
import json
import sqlite3
import logging
import urllib.request
import urllib.error
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("media-series-dedup")

# MT 默认数据库路径（只读模式）
MT_DB_PATH = os.environ.get("MT_DB_PATH", "/data/mediatidy.db")  # 部署时按实际 MT 数据库路径覆盖
MT_BASE_URL = "http://127.0.0.1:2019"
MT_DEFAULT_USER = "admin"
MT_DEFAULT_PASS = os.environ.get("MT_PASSWORD", "")


class MTClient:
    """轻量级 MT API 客户端（标准库实现，支持 401 自动登录重试）。"""

    def __init__(self, base_url: str = MT_BASE_URL, user: str = MT_DEFAULT_USER, password: str = MT_DEFAULT_PASS):
        self.base = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.token: Optional[str] = None

    def login(self) -> str:
        url = f"{self.base}/api/auth/login"
        payload = json.dumps({"username": self.user, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        tok = (data.get("data") or {}).get("token")
        if not tok:
            raise RuntimeError(f"MT 登录失败: {data}")
        self.token = tok
        return tok

    def _ensure_token(self):
        if not self.token:
            self.login()

    def request(self, path: str, payload: Any = None, method: str = "GET") -> Dict[str, Any]:
        self._ensure_token()
        url = f"{self.base}{path}" if path.startswith("/") else f"{self.base}/{path}"
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None

        for attempt in range(2):
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            }
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.token = None
                    self.login()
                    continue
                try:
                    body = e.read().decode("utf-8", "replace")
                    return json.loads(body)
                except Exception:
                    raise e
        return {}


def query_mt_tmdb_caches(tmdb_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """直连 MT SQLite 数据库，批量只读读取 TMDB 缓存信息。"""
    results = {}
    if not tmdb_ids or not os.path.exists(MT_DB_PATH):
        return results

    uri = f"file:{MT_DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        cursor = conn.cursor()
        batch_size = 500
        for i in range(0, len(tmdb_ids), batch_size):
            chunk = tmdb_ids[i:i + batch_size]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                SELECT tmdb_id, media_type, id, title, original_title, year, poster_path, updated_at
                FROM tmdb_cache
                WHERE tmdb_id IN ({placeholders})
            """
            cursor.execute(sql, chunk)
            for row in cursor.fetchall():
                tid, mtype, cid, title, orig_title, year, poster, updated = row
                results[int(tid)] = {
                    "tmdb_id": int(tid),
                    "cache_id": cid,
                    "media_type": mtype,
                    "title": title or "",
                    "original_title": orig_title or "",
                    "year": int(year) if year and str(year).isdigit() else None,
                    "poster_path": poster or "",
                    "updated_at": updated or "",
                }
        conn.close()
    except Exception as e:
        logger.warning("直读 MT SQLite 缓存库失败: %s", e)
    return results


def refresh_single_tmdb(client: MTClient, tmdb_id: int, media_type: str = "tv") -> Optional[Dict[str, Any]]:
    """强制调用 MT 接口刷新单个 TMDB 条目的最新数据。"""
    try:
        # 1. 查找 cache_id
        res = client.request(f"/api/tmdb-cache?search={tmdb_id}", method="GET")
        items = res.get("data", {}).get("list") or []
        cache_id = None
        for it in items:
            if str(it.get("tmdbId")) == str(tmdb_id):
                cache_id = it.get("id")
                break
        if not cache_id and items:
            cache_id = items[0].get("id")

        if not cache_id:
            # 如果缓存不存在，返回 None
            return None

        # 2. 调用 refresh 接口
        ref_res = client.request(f"/api/tmdb-cache/{cache_id}/refresh", payload={}, method="POST")
        data = ref_res.get("data") or {}
        if not data:
            return None

        year_val = data.get("year")
        try:
            year_int = int(year_val) if year_val else None
        except Exception:
            year_int = None

        return {
            "tmdb_id": tmdb_id,
            "cache_id": cache_id,
            "title": data.get("title") or "",
            "original_title": data.get("originalTitle") or "",
            "year": year_int,
            "poster_path": data.get("posterPath") or "",
        }
    except Exception as e:
        logger.warning("刷新 TMDB ID %s 失败: %s", tmdb_id, e)
        return None


def refresh_tmdb_batch(
    tmdb_ids: List[int],
    client: Optional[MTClient] = None,
    max_workers: int = 5,
    stop_check_fn=None,
    progress_callback=None
) -> Dict[int, Dict[str, Any]]:
    """并发批量强制刷新 TMDB 官方缓存。"""
    client = client or MTClient()
    results = {}
    total = len(tmdb_ids)

    def _worker(tid):
        if stop_check_fn and stop_check_fn():
            return None
        return refresh_single_tmdb(client, tid)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, tid): tid for tid in tmdb_ids}
        done_cnt = 0
        for f in futures:
            if stop_check_fn and stop_check_fn():
                break
            tid = futures[f]
            try:
                res = f.result()
                if res:
                    results[tid] = res
            except Exception as e:
                logger.warning("并发任务执行异常: %s", e)
            done_cnt += 1
            if progress_callback:
                progress_callback(done_cnt, total)

    return results
