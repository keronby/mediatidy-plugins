# -*- coding: utf-8 -*-
"""sha1 来源采集器（契约 §3.1）。

三个来源，定位不同（详见契约 §3.1.1）：

  1. MT FF 缓存   —— 【长期主力 / 每次增量】
                     直连 mediatidy.db 的 organize_file_probe_cache（只读）。
                     MT 全库只有这一列存 sha1，实测 40 万行。
                     MT 每整理完一个作品就把探测结果写进这张表，所以库会随 MT
                     干活自动长大；用 rowid 游标只取新增行即可，无需全量重扫。

  2. 历史导入     —— 【一次性】用户自己的 sha1 文件（txt / csv / 每行一条）。

  3. STRM 扫描    —— 【一次性】本地 .strm 文件，从分享 URL 的 sha1= 参数取。
                     实测两个目录共 127 万个 strm，其中 125.6 万个带 sha1，
                     1.0 万个 http 但无 sha1，1.3 万个是非 http 的 CD2 本地路径
                     （这类拿不到 sha1，安全跳过）。

为什么 ②③ 是一次性的：它们只是建库初期把存量补到 79.6 万条的水位，
之后 MT 自己产出的增量已经覆盖日常所有新整理文件，再全量重扫 127 万个
strm 只会白花 150 秒。因此 ②③ 默认跳过，由 includeStrm / includeHistory
或 mode="full" 显式触发。

所有采集器统一返回 (新增条数, 消息) 并直接写入 Sha1Library。
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from core.library import Sha1Library

RE_SHA1 = re.compile(r"^[0-9a-fA-F]{40}$")
# 从任意文本里捞 40 位十六进制
RE_FIND_SHA1 = re.compile(r"\b([0-9a-fA-F]{40})\b")


def normalize(s: str) -> str:
    s = (s or "").strip()
    return s.lower() if RE_SHA1.match(s) else ""


# --------------------------------------------------------------------------- #
# 1. MT FF 缓存
# --------------------------------------------------------------------------- #
DEFAULT_MT_DB = "/app/data/mediatidy.db"


def probe_mt_db(db_path: str) -> Dict[str, Any]:
    """检查 MT 数据库是否可读，返回表与行数信息。"""
    info: Dict[str, Any] = {"path": db_path, "ok": False, "error": "", "rows": 0}
    if not os.path.exists(db_path):
        info["error"] = "文件不存在：%s" % db_path
        return info
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=10.0)
        try:
            n = conn.execute("SELECT COUNT(*) FROM organize_file_probe_cache").fetchone()[0]
            info["rows"] = int(n)
            info["ok"] = True
        finally:
            conn.close()
    except Exception as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


META_MT_CURSOR = "mt_cache_created_at"   # 已同步到的最大 created_at（含亚秒精度字符串）
META_MT_ROWID = "mt_cache_rowid"          # 兼容旧库：上一次用过的 rowid 游标
META_STRM_DONE = "strm_scan_done"         # STRM 一次性扫描完成标记（"1" 表示已扫完）
META_CLOUD_DONE_PREFIX = "cloud_scan_done"  # 云盘一次性扩库完成标记（按根路径各记一条）
META_QUEUE_PREFIX = "queue"                 # 断点续跑的待展开目录队列，按根路径分开存

# 时间游标回退安全窗（秒）：MT 写入与读取之间可能有时钟/事务先后差，
# 每次都从「上次游标 - 30 秒」开始重新取，重复的行由 add_many 的
# ON CONFLICT 天然去重，不会重复计数，但能兜住边界漏行。
MT_OVERLAP_SEC = 30


def mt_total_rows(db_path: str = DEFAULT_MT_DB) -> int:
    """MT 缓存表总行数，用于报表与进度。"""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=10.0)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM organize_file_probe_cache").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def _shift_ts(ts: str, seconds: int) -> str:
    """把 'YYYY-MM-DD HH:MM:SS.ffff+08:00' 这类时间串回退若干秒。

    只做字符串层面的日期时间部分回退，时区后缀原样保留；
    解析失败时返回空串（调用方退回全量）。
    """
    if not ts:
        return ""
    # MT 的时间串形如 2026-10-29 21:04:38.51060223+08:00：
    #   ① 小数位可能是 8~9 位（纳秒），strptime 的 %f 只吃 1~6 位；
    #   ② 时区后缀要在切分前摘掉，否则 "2026-10-29" 里的 '-' 也会被当作时区分隔。
    text = ts.strip()
    tz = ""
    for mark in ("+", "Z", "z"):
        idx = text.find(mark)
        if idx > 10:                      # 位置在日期部分之后，才是时区
            tz = text[idx:]
            text = text[:idx]
            break
    try:
        from datetime import datetime, timedelta
        text = text.replace("T", " ")
        # 截断到微秒（6 位）后解析
        if "." in text:
            base, _, frac = text.partition(".")
            text = base + "." + frac[:6].ljust(6, "0")
            fmt = "%Y-%m-%d %H:%M:%S.%f"
        else:
            fmt = "%Y-%m-%d %H:%M:%S"
        dt = datetime.strptime(text, fmt) - timedelta(seconds=seconds)
        out = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        if tz in ("Z", "z"):
            tz = "+00:00"
        return out + tz
    except Exception:
        return ""


def collect_mt_cache(lib: Sha1Library, db_path: str = DEFAULT_MT_DB,
                     incremental: bool = True,
                     on_progress: Optional[Callable[[int, int], None]] = None) -> Tuple[int, str]:
    """从 MT 的 organize_file_probe_cache 只读拉取 sha1（双路增量）。

    **为什么不能只用 rowid 游标**：这张表被 MT 限死在 40 万行，存满后
    「新增数据替换最旧数据」。实测 rowid 跨度 1~452952 而表内只有 40 万行，
    空洞从 rowid 29 就开始并散布全表 —— 即淘汰是**按时间散布**的，
    不是只砍尾部。因此纯 rowid 游标会漏掉「后插入、先被淘汰」的行。

    双路策略（增量模式）：
      路 A 主路（索引 idx_..._created，0.002s 级）：
            created_at > (上次时间游标 - 30 秒安全窗) → 拿到所有新写入的行
      路 B 兜底（全表拉 sha1，约 2.5s，纯内存比对、不写库）：
            取表里当前全部 sha1，与本地库已有集合求差，
            差集即为「曾经漏同步且尚未被淘汰」的行，补进库

    full=True 时走全量重读（首次建库 / 修复），并重置时间游标。
    """
    if not os.path.exists(db_path):
        return 0, "MT 数据库不存在（%s），跳过" % db_path
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=30.0)
    except Exception as exc:
        return 0, "打不开 MT 数据库：%s" % exc

    added_a = 0
    added_b = 0
    rows_a = 0
    cursor = (lib.get_meta(META_MT_CURSOR, "") or "").strip()
    use_time = incremental and bool(cursor)
    table_rows = 0
    max_created = cursor

    try:
        table_rows = int(conn.execute(
            "SELECT COUNT(*) FROM organize_file_probe_cache").fetchone()[0])

        if not use_time:
            # ---------- 全量 ----------
            cur = conn.execute("SELECT sha1, created_at FROM organize_file_probe_cache")
            buf: List[str] = []
            total = 0
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                for h, ct in rows:
                    if ct and ct > max_created:
                        max_created = ct
                    if h:
                        buf.append(h)
                total += len(rows)
                if len(buf) >= 20000:
                    added_a += lib.add_many(buf, 1)
                    buf = []
                    if on_progress:
                        on_progress(total, table_rows)
            if buf:
                added_a += lib.add_many(buf, 1)
        else:
            # ---------- 路 A：时间游标 ----------
            since = _shift_ts(cursor, -MT_OVERLAP_SEC)
            if since:
                cur = conn.execute(
                    "SELECT sha1, created_at FROM organize_file_probe_cache "
                    "WHERE created_at > ? ORDER BY created_at", (since,))
                buf = []
                while True:
                    rows = cur.fetchmany(5000)
                    if not rows:
                        break
                    for h, ct in rows:
                        if ct and ct > max_created:
                            max_created = ct
                        if h:
                            buf.append(h)
                    rows_a += len(rows)
                    if len(buf) >= 20000:
                        added_a += lib.add_many(buf, 1)
                        buf = []
                if buf:
                    added_a += lib.add_many(buf, 1)
            else:
                # 时间串解析不了 → 退回全量，避免静默漏数据
                use_time = False
                cur = conn.execute("SELECT sha1, created_at FROM organize_file_probe_cache")
                buf = []
                while True:
                    rows = cur.fetchmany(5000)
                    if not rows:
                        break
                    for h, ct in rows:
                        if ct and ct > max_created:
                            max_created = ct
                        if h:
                            buf.append(h)
                    rows_a += len(rows)
                    if len(buf) >= 20000:
                        added_a += lib.add_many(buf, 1)
                        buf = []
                if buf:
                    added_a += lib.add_many(buf, 1)

            # ---------- 路 B：全表比对差集（兜住被提前淘汰的行） ----------
            all_rows = conn.execute("SELECT sha1 FROM organize_file_probe_cache").fetchall()
            known = lib.existing_lower()          # 只读，不写库
            missing = [r[0] for r in all_rows if r[0] and r[0].lower() not in known]
            if missing:
                for i in range(0, len(missing), 20000):
                    added_b += lib.add_many(missing[i:i + 20000], 1)
    except Exception as exc:
        return added_a + added_b, "读取 MT FF 缓存失败：%s: %s" % (type(exc).__name__, exc)
    finally:
        conn.close()

    # 全部成功后才推进游标
    if max_created:
        lib.set_meta(META_MT_CURSOR, max_created)
    lib.set_state("mt_cache", (max_created or "")[:19] or None, table_rows)

    if not use_time:
        return added_a + added_b, (
            "MT FF 缓存全量读取 %d 行，新增 %d 条（时间游标已置到 %s）"
            % (table_rows, added_a + added_b, (max_created or "")[:19]))

    return added_a + added_b, (
        "MT FF 缓存增量同步：时间游标 > %s 取到 %d 行（新增 %d）＋全表差集补齐 %d 条；"
        "表内共 %d 行，时间游标推进到 %s"
        % ((_shift_ts(cursor, -MT_OVERLAP_SEC) or cursor)[:19], rows_a, added_a,
           added_b, table_rows, (max_created or "")[:19]))


def collect_csv(lib: Sha1Library, csv_path: str) -> Tuple[int, str]:
    """B2 降级方案：从用户导出的 CSV / TXT 里提取 sha1。"""
    return collect_history(lib, csv_path)


# --------------------------------------------------------------------------- #
# 2. 历史导入
# --------------------------------------------------------------------------- #
def collect_history(lib: Sha1Library, path: str) -> Tuple[int, str]:
    """导入用户历史 sha1 库：每行一个 sha1，或 CSV（任意列里含 40 位十六进制即可）。"""
    if not path or not os.path.exists(path):
        return 0, "历史库文件不存在：%s" % (path or "(未填写)")
    if os.path.isdir(path):
        return 0, "历史库路径是目录，请指向具体文件"

    added = 0
    total = 0
    skipped = 0
    buf: List[str] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line:
                continue
            total += 1
            hit = RE_FIND_SHA1.search(line)
            if hit:
                buf.append(hit.group(1))
            else:
                skipped += 1
            if len(buf) >= 20000:
                added += lib.add_many(buf, 2)
                buf = []
    if buf:
        added += lib.add_many(buf, 2)
    lib.set_state("history", path, lib.get_state("history")["rows"] + added)
    msg = "历史库 %s：扫描 %d 行，提取 %d 条，新增 %d 条" % (
        os.path.basename(path), total, total - skipped, added)
    if skipped:
        msg += "（%d 行未找到 40 位 sha1）" % skipped
    return added, msg


# --------------------------------------------------------------------------- #
# 3. STRM 扫描
# --------------------------------------------------------------------------- #
def sha1_from_strm_content(content: str) -> str:
    """strm 首行可能是 http 播放链接（带 sha1= 参数），也可能是 CD2 本地路径。"""
    c = (content or "").strip()
    if not c:
        return ""
    if c[:4].lower() == "http":
        try:
            q = parse_qs(urlparse(c).query)
            for key in ("sha1", "SHA1", "Sha1"):
                if key in q:
                    v = normalize(q[key][0])
                    if v:
                        return v
        except Exception:
            pass
        # 兜底：链接里任意位置出现 40 位十六进制
        m = RE_FIND_SHA1.search(c)
        return normalize(m.group(1)) if m else ""
    # 非 http：CD2 本地路径，本身不含 sha1
    return ""


def collect_strm(lib: Sha1Library, roots: List[str],
                 deadline: Optional[float] = None,
                 on_progress: Optional[Callable[[int, int], None]] = None
                 ) -> Tuple[int, str]:
    """扫描本地 .strm：只处理 http 型（自带 sha1），统计非 http 型数量。"""
    added = 0
    scanned = 0
    http_with = 0
    http_without = 0
    non_http = 0
    buf: List[str] = []
    seen_at = time.time()

    for root in roots:
        root = (root or "").strip()
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if deadline is not None and time.time() > deadline:
                if buf:
                    added += lib.add_many(buf, 4)
                lib.set_state("strm", str(seen_at), http_with)
                return added, ("STRM 扫描时间用尽：已扫 %d 个（http 带 sha1 %d / 非 http %d），"
                               "本次新增 %d 条" % (scanned, http_with, non_http, added))
            for fn in filenames:
                if not fn.lower().endswith(".strm"):
                    continue
                scanned += 1
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        head = f.read(4096)
                except OSError:
                    continue
                h = sha1_from_strm_content(head)
                if h:
                    http_with += 1
                    buf.append(h)
                elif head.strip()[:4].lower() == "http":
                    http_without += 1
                else:
                    non_http += 1
                if len(buf) >= 20000:
                    added += lib.add_many(buf, 4)
                    buf = []
                    if on_progress:
                        on_progress(scanned, http_with)
    if buf:
        added += lib.add_many(buf, 4)
    lib.set_state("strm", str(seen_at), http_with)
    if deadline is None or time.time() <= deadline:
        # 完整扫完才置一次性标记；中途超时的不置，下次仍会继续
        lib.set_meta(META_STRM_DONE, "1")
    return added, ("STRM 扫描 %d 个：http 带 sha1 %d，http 无 sha1 %d，非 http（本地路径型）%d；新增 %d 条"
                   % (scanned, http_with, http_without, non_http, added))

def collect_cloud(lib: Sha1Library, ctx, roots: List[str],
                  max_depth: int = 12,
                  deadline: Optional[float] = None,
                  on_progress: Optional[Callable[[int, int], None]] = None
                  ) -> Tuple[int, str]:
    """通过 MT 原生 files.* 遍历云盘媒体库，把每个文件的 sha1 收进归约库。

    零依赖、不需要 CD2 gRPC。MT 的 files.list 在 cloud 类型根上会带回 sha1
    （与 walk/list_dir 同一语义），与 collect_strm/collect_mt_cache 用同一套
    Sha1Library 入库（SRC_CLOUD）。files.* 每运行 500 次配额靠 mtfs 的根缓存
    与 200 页自动翻页承压，大库分多次点击续跑（每次 deadline 到点即停，
    已入库的不丢，下次继续）。

    注意：MT 返回的 sha1 已是小写；统一 lower() 兜底。
    """
    from core import mtfs

    added = 0
    files = 0
    with_sha1 = 0
    dirs = 0
    errors: List[str] = []
    summary: List[str] = []
    buf: List[str] = []

    def flush() -> int:
        nonlocal buf
        if not buf:
            return 0
        n = lib.add_many(buf, 16)  # SRC_CLOUD
        buf = []
        return n

    for root in roots:
        root = (root or "").strip()
        if not root:
            continue
        if lib.get_meta("%s:%s" % (META_CLOUD_DONE_PREFIX, root), "") == "1":
            summary.append("%s 已扩库完成，跳过（重跑请先清标记）" % root)
            continue
        try:
            top = mtfs.list_dir(ctx, root)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s：%s" % (root, str(exc)[:80]))
            continue
        summary.append("%s 首层 %d 项（目录 %d / 文件 %d）" % (
            root, len(top),
            sum(1 for x in top if x.get("isDir")),
            sum(1 for x in top if not x.get("isDir"))))
        # BFS（队列式）+ deadline 停下：已入库不丢，下次从头再扫只是多扫一遍，
        # sha1 去重在 add_many 层，重复的不会多记。
        queue = [(root, 0)]
        while queue:
            if deadline is not None and time.time() > deadline:
                added += flush()
                return added, ("云盘媒体库扫描时间用尽：已展开目录 %d 个 / 文件 %d 个"
                               "（带 sha1 %d），新增 %d 条；再点一次继续"
                               % (dirs, files, with_sha1, added))
            cur, depth = queue.pop(0)
            try:
                entries = mtfs.list_dir(ctx, cur)
            except Exception as exc:  # noqa: BLE001
                errors.append("%s：%s" % (cur, str(exc)[:60]))
                continue
            dirs += 1
            if dirs % 50 == 0 and on_progress:
                on_progress(files, with_sha1)
            for e in entries:
                if e.get("isDir"):
                    if depth + 1 < max_depth:
                        queue.append((str(e.get("path") or ""), depth + 1))
                    continue
                files += 1
                sha1 = str(e.get("sha1") or "").strip().lower()
                if sha1:
                    with_sha1 += 1
                    buf.append(sha1)
            if len(buf) >= 20000:
                added += flush()
        lib.set_meta("%s:%s" % (META_CLOUD_DONE_PREFIX, root), "1")
    added += flush()
    lib.set_state("cloud", str(time.time()), with_sha1)
    msg = ("云盘媒体库扫描：%d 个目录 / %d 个文件，带 sha1 %d，新增 %d 条"
           % (dirs, files, with_sha1, added))
    if summary:
        msg += "；" + "；".join(summary)
    if errors:
        msg += "；失败 %d 个（%s）" % (len(errors), "；".join(errors[:3]))
    return added, msg
