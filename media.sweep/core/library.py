# -*- coding: utf-8 -*-
"""sha1 归约库（SQLite）。

设计要点（契约 §3）：
  - 库文件放 ctx.data_dir/sha1lib.sqlite3，跨 worker 重启保留。
  - 只存 sha1 + 极轻量来源标记，不存任何媒体元数据。
  - 4 个来源各自记水位（watermark），支持增量刷新。
  - 表：
      sha1(sha1 PK, src, first_seen)   —— src 是位掩码，1=MT缓存 2=历史导入 4=strm 8=CD2
      src_state(name PK, last_value, rows, updated_at)
      job(id PK, kind, source, target, status, created_at, updated_at, stats)
      job_item(job_id, key, sha1, verdict, src_path, dest_path, note)
      meta(k, v)
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

SRC_MT_CACHE = 1
SRC_HISTORY = 2
SRC_STRM = 4
SRC_CD2 = 8
# 云盘媒体库提取：直接遍历云盘上「已整理好的媒体库」，取每个文件的 sha1。
# 与 MT FF 缓存的区别是它不受 40 万行滚动淘汰影响 —— 库里有什么就能收什么。
SRC_CLOUD = 16

SRC_NAMES = {
    SRC_MT_CACHE: "MT FF 缓存",
    SRC_HISTORY: "历史导入",
    SRC_STRM: "STRM 扫描",
    SRC_CD2: "CD2 扫描",
    SRC_CLOUD: "云盘媒体库",
}

# 单批插入上限，避免 SQLite 变量数超限（默认 999）
BATCH = 400


SCHEMA = """
CREATE TABLE IF NOT EXISTS sha1 (
    sha1       TEXT PRIMARY KEY,
    src        INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS src_state (
    name       TEXT PRIMARY KEY,
    last_value TEXT,
    rows       INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'running',
    created_at INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0,
    stats      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS job_item (
    job_id    INTEGER NOT NULL,
    key       TEXT NOT NULL,
    sha1      TEXT NOT NULL DEFAULT '',
    verdict   TEXT NOT NULL,
    hit       INTEGER NOT NULL DEFAULT 0,
    src_path  TEXT NOT NULL DEFAULT '',
    dest_path TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, key)
);
CREATE INDEX IF NOT EXISTS idx_job_item_verdict ON job_item(job_id, verdict);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL DEFAULT ''
);
"""


class Sha1Library:
    """sha1 归约库。线程内使用，不要跨线程共享连接。"""

    def __init__(self, path: str) -> None:
        self.path = path
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-20000")  # ~20 MB
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "Sha1Library":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def add_many(self, sha1_list: Iterable[str], src: int) -> int:
        """批量插入。已存在的只做位或（不覆盖 first_seen）。返回本次"新插入或新增来源"的条数。"""
        now = int(time.time())
        rows: List[Tuple[str, int, int]] = []
        seen_local = set()
        added = 0
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            for s in sha1_list:
                s = (s or "").strip().lower()
                if len(s) != 40 or s in seen_local:
                    continue
                seen_local.add(s)
                rows.append((s, src, now))
                if len(rows) >= BATCH:
                    added += self._flush_insert(cur, rows)
                    rows = []
            if rows:
                added += self._flush_insert(cur, rows)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        return added

    def existing_lower(self) -> set:
        """只读返回库里全部 sha1 的小写集合（约 80 万条约 60~80 MB 内存）。

        用于「MT 缓存全表差集补齐」：MT 表只有 40 万行且会被滚动淘汰，
        拉全表 sha1 与本地集合求差，即可找回曾经漏同步但尚未被淘汰的行。
        """
        out = set()
        for (h,) in self.conn.execute("SELECT sha1 FROM sha1"):
            if h:
                out.add(h.lower())
        return out

    @staticmethod
    def _flush_insert(cur: sqlite3.Cursor, rows: List[Tuple[str, int, int]]) -> int:
        # 先查已存在的，统计真实新增
        ph = ",".join("?" * len(rows))
        existing = {r[0] for r in cur.execute(
            "SELECT sha1 FROM sha1 WHERE sha1 IN (%s)" % ph, [r[0] for r in rows]
        )}
        cur.executemany(
            "INSERT INTO sha1(sha1, src, first_seen) VALUES(?,?,?) "
            "ON CONFLICT(sha1) DO UPDATE SET src = sha1.src | excluded.src",
            rows,
        )
        return len(rows) - len(existing)

    def contains(self, sha1_list: Iterable[str]) -> Dict[str, bool]:
        """批量存在性查询 → {sha1: bool}"""
        wanted = [s.strip().lower() for s in sha1_list if s and len(s.strip()) == 40]
        out = {s: False for s in wanted}
        for i in range(0, len(wanted), BATCH):
            chunk = wanted[i:i + BATCH]
            ph = ",".join("?" * len(chunk))
            for (s,) in self.conn.execute("SELECT sha1 FROM sha1 WHERE sha1 IN (%s)" % ph, chunk):
                out[s] = True
        return out

    def has(self, sha1: str) -> bool:
        sha1 = (sha1 or "").strip().lower()
        if len(sha1) != 40:
            return False
        row = self.conn.execute("SELECT 1 FROM sha1 WHERE sha1=?", (sha1,)).fetchone()
        return row is not None

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM sha1").fetchone()[0]

    def count_by_src(self) -> Dict[str, int]:
        """按来源统计条数（一条 sha1 可能同时来自多个来源，所以分别累加位标记）。

        实现上一次扫描算出全部来源：库里 79 万行时，按位逐个 COUNT 要扫 4 遍，
        query 通道只有 10 秒预算，几遍全表扫描会直接 deadline exceeded（真机踩过）。
        """
        out: Dict[str, int] = {name: 0 for name in SRC_NAMES.values()}
        names = list(SRC_NAMES.items())
        for (src,) in self.conn.execute("SELECT src FROM sha1"):
            src = src or 0
            for bit, name in names:
                if src & bit:
                    out[name] += 1
        return out

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    # ------------------------------------------------------------------ #
    # 来源水位
    # ------------------------------------------------------------------ #
    def get_state(self, name: str) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT last_value, rows, updated_at FROM src_state WHERE name=?", (name,)
        ).fetchone()
        if not row:
            return {"last_value": None, "rows": 0, "updated_at": 0}
        return {"last_value": row[0], "rows": row[1], "updated_at": row[2]}

    def set_state(self, name: str, last_value: Optional[str], rows: int) -> None:
        self.conn.execute(
            "INSERT INTO src_state(name, last_value, rows, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET last_value=excluded.last_value, "
            "rows=excluded.rows, updated_at=excluded.updated_at",
            (name, last_value, rows, int(time.time())),
        )

    def all_states(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for name, lv, rows, up in self.conn.execute(
            "SELECT name, last_value, rows, updated_at FROM src_state"
        ):
            out[name] = {"last_value": lv, "rows": rows, "updated_at": up}
        return out

    # ------------------------------------------------------------------ #
    # 任务记录
    # ------------------------------------------------------------------ #
    def new_job(self, kind: str, source: str, target: str) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            "INSERT INTO job(kind, source, target, status, created_at, updated_at) "
            "VALUES(?,?,?,'running',?,?)", (kind, source, target, now, now),
        )
        return int(cur.lastrowid)

    def update_job(self, job_id: int, status: Optional[str] = None,
                   stats: Optional[Dict[str, Any]] = None) -> None:
        now = int(time.time())
        if status and stats is not None:
            import json
            self.conn.execute(
                "UPDATE job SET status=?, stats=?, updated_at=? WHERE id=?",
                (status, json.dumps(stats, ensure_ascii=False), now, job_id),
            )
        elif status:
            self.conn.execute("UPDATE job SET status=?, updated_at=? WHERE id=?",
                              (status, now, job_id))

    def add_items(self, job_id: int, items: List[Tuple[str, str, str, int, str, str]]) -> None:
        """items: (key, sha1, verdict, hit, dest_path, note)"""
        if not items:
            return
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(
                "INSERT INTO job_item(job_id, key, sha1, verdict, hit, dest_path, note) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id, key) DO UPDATE SET "
                "verdict=excluded.verdict, hit=excluded.hit, dest_path=excluded.dest_path, "
                "note=excluded.note",
                [(job_id,) + tuple(i) for i in items],
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def latest_job(self, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if kind:
            row = self.conn.execute(
                "SELECT id, kind, source, target, status, created_at, updated_at, stats "
                "FROM job WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT id, kind, source, target, status, created_at, updated_at, stats "
                "FROM job ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        import json
        try:
            stats = json.loads(row[7] or "{}")
        except Exception:
            stats = {}
        return {
            "id": row[0], "kind": row[1], "source": row[2], "target": row[3],
            "status": row[4], "created_at": row[5], "updated_at": row[6], "stats": stats,
        }

    def job_items(self, job_id: int, verdict: Optional[str] = None,
                  limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        if verdict:
            rows = self.conn.execute(
                "SELECT key, sha1, verdict, hit, src_path, dest_path, note FROM job_item "
                "WHERE job_id=? AND verdict=? ORDER BY key LIMIT ? OFFSET ?",
                (job_id, verdict, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT key, sha1, verdict, hit, src_path, dest_path, note FROM job_item "
                "WHERE job_id=? ORDER BY key LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
        return [
            {"key": r[0], "sha1": r[1], "verdict": r[2], "hit": r[3],
             "src_path": r[4], "dest_path": r[5], "note": r[6]}
            for r in rows
        ]

    def job_counts(self, job_id: int) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for verdict, n in self.conn.execute(
            "SELECT verdict, COUNT(*) FROM job_item WHERE job_id=? GROUP BY verdict", (job_id,)
        ):
            out[verdict] = n
        return out

    # ------------------------------------------------------------------ #
    # meta
    # ------------------------------------------------------------------ #
    def get_meta(self, k: str, default: str = "") -> str:
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def set_meta(self, k: str, v: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
