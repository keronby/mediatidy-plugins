# -*- coding: utf-8 -*-
"""影视查重（影视目录多版本重整与分流）插件。

功能与业务规则（遵从用户标准）：
1. 正常合规目录标准：【片名 (年份) {tmdb-数字}】且片名年份与 TMDB 官方一致。
2. 待重整/待处理对象（统一纳入处理大表）：
   - 缺少年份（如：天下第一纨绔 {tmdb-307304} ➔ 补齐官方年份）；
   - TMDB 风格非标准（如：[tmdb=123] ➔ 重整为 {tmdb-123}）；
   - 未标注 TMDB 目录（完全未识别出 TMDB ID，需手动刮削补齐）；
   - 片名或年份与 TMDB 官方不一致；
   - 同一 TMDB 存在重复多目录（洗版合并）。
3. 核心保护铁律（唯一不予处理）：
   - 混合链按 mt-console 逐文件评分、清理败者、复扫胜者；混乱结果保护。
4. 双链路智能分流：
   - HTTP 分享链 ➔ 指派用户选择的本地整理方案与本地目标目录；
   - CloudNAS 挂载链 ➔ 指派用户选择的云盘整理方案与云盘目标目录。
5. 操作体验与 STRM 重复扫描完全对齐：
   - 工作台提供原生 FilePicker 树形目录选择器，支持直接勾选单目录或多目录；
   - 所有整理方案均支持 ProfilePicker 直接下拉选取，配置收归工作台。
"""

import os
import re
import sys
import json
import time
import logging
from typing import Dict, List, Any, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from mt_plugin import Context, Plugin as PluginBase, action, query, continuation, ui
from series_core.mixed import slot_label, distinct_work_dirs
from series_core.storage import read_value, write_value, publish_snapshot, read_parts, read_page
from series_core.scanner import scan_media_library, resolve_local_path, parse_media_dir
from series_core.tmdb import query_mt_tmdb_caches, refresh_tmdb_batch
from series_core.matcher import match_duplicate_groups

logger = logging.getLogger("media.series-dedup")

PLUGIN_ID = "media.series-dedup"

# 配置结构版本：结构或默认值语义变化时递增，用于丢弃旧版遗留配置
CONFIG_SCHEMA_VERSION = "2"

# 授权根表（rootId -> 绝对路径），用于 Picker 相对路径拼接，零远程依赖
STORAGE_KEY_ROOTS = "series_dedup_root_map"

STORAGE_KEY_CONFIG = "series_dedup_config"
STORAGE_KEY_STATE = "series_dedup_state"
STORAGE_KEY_SHARE = "series_dedup_share"
STORAGE_KEY_LOCAL = "series_dedup_local"
STORAGE_KEY_MIXED = "series_dedup_mixed"
STORAGE_KEY_FULL_GROUPS = "series_dedup_full_groups"
STORAGE_KEY_SNAPSHOT = "series_dedup_current_snapshot"
STORAGE_KEY_PROTECTED = "series_dedup_protected_no_year"


def _storage_set_safe(ctx: Context, key: str, value: Any, max_bytes: int = 180000) -> bool:
    return write_value(ctx, key, value, max_bytes)


def _storage_get_safe(ctx: Context, key: str, default: Any = None) -> Any:
    return read_value(ctx, key, default)


def _save_state_split(
    ctx: Context,
    state_meta: Dict[str, Any],
    share_links: Optional[List[Dict[str, Any]]] = None,
    local_links: Optional[List[Dict[str, Any]]] = None,
    mixed_links: Optional[List[Dict[str, Any]]] = None,
    full_groups: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """完整分片写入成功后发布指针，不截断列表。"""
    meta = {k: v for k, v in state_meta.items() if k not in
            ("share_links", "local_links", "mixed_links", "protected_links")}
    changes = {name: values for name, values in (
        ("share_links", share_links), ("local_links", local_links),
        ("mixed_links", mixed_links), ("full_groups", full_groups)) if values is not None}
    # First write migrates legacy arrays before publishing a new-format pointer.
    if read_value(ctx, STORAGE_KEY_SNAPSHOT) is None:
        for name, key in (("share_links", STORAGE_KEY_SHARE), ("local_links", STORAGE_KEY_LOCAL),
                          ("mixed_links", STORAGE_KEY_MIXED), ("full_groups", STORAGE_KEY_FULL_GROUPS)):
            changes.setdefault(name, read_value(ctx, key, []))
    publish_snapshot(ctx, STORAGE_KEY_SNAPSHOT, meta, changes)


def _get_full_groups(ctx: Context) -> List[Dict[str, Any]]:
    snapshot = read_value(ctx, STORAGE_KEY_SNAPSHOT)
    if snapshot is not None:
        return [g for g in read_parts(ctx, snapshot["datasets"]["full_groups"]) if len(distinct_work_dirs(g)) >= 2]
    return [g for g in read_value(ctx, STORAGE_KEY_FULL_GROUPS, []) if len(distinct_work_dirs(g)) >= 2]


def _get_protected_no_year(ctx: Context) -> Dict[str, Dict[str, Any]]:
    """读取无年份保护组字典，以 f"{media_type}:{tmdb_id}" 或 dir_path 为 key。严格过滤非 dict 异常值。

    仅使用 MT 官方 storage 单一数据源，不读任何外部文件，
    实例数据可能在卸载后保留，关联与清理由宿主管理。
    """
    stored = read_value(ctx, STORAGE_KEY_PROTECTED, {})
    if not isinstance(stored, dict):
        raise ValueError("保护组存储格式错误，禁止自动覆盖")
    if not all(isinstance(v, dict) for v in stored.values()):
        raise ValueError("保护组包含无效条目，禁止自动丢弃")
    return stored


def _assert_unprotected_rows(ctx: Context, rows: List[Dict[str, Any]]) -> None:
    """Recheck current protection data, rather than trusting a scan snapshot."""
    protected = _get_protected_no_year(ctx)
    for row in rows:
        path = row.get("local_path") or row.get("dir_path")
        key = str(row.get("media_type")) + ":" + str(row.get("tmdb_id"))
        if key in protected or path in protected or any(
            (path and item.get("local_path") == path) or
            (row.get("tmdb_id") and str(item.get("tmdb_id")) == str(row["tmdb_id"])
             and item.get("media_type") == row.get("media_type"))
            for item in protected.values()
        ):
            raise ValueError("作品已加入保护组，请解除保护并重新扫描后操作")


def _save_protected_no_year(ctx: Context, prot_map: Dict[str, Dict[str, Any]]) -> None:
    """持久化保存无年份保护组（仅写入 MT 官方 storage）。"""
    _storage_set_safe(ctx, STORAGE_KEY_PROTECTED, prot_map, max_bytes=180000)



def _normalize_slashes(p: str) -> str:
    """压缩重复斜杠，去掉末尾斜杠（根 "/" 除外）。"""
    if not p:
        return ""
    out = re.sub(r"/{2,}", "/", p.strip())
    if len(out) > 1 and out.endswith("/"):
        out = out.rstrip("/")
    return out


def _known_roots_from_config(ctx: Context, cfg: Any = None) -> Dict[str, str]:
    """Explicit user bindings (KV first, legacy host config fallback), never guesses."""
    from series_core.roots import authorized_bindings
    host_config = ctx.mt.config.get()
    cfg = _get_config(ctx) if cfg is None else cfg
    bindings = cfg["rootBindings"] if "rootBindings" in cfg else host_config.get("rootBindings", [])
    roots = authorized_bindings(ctx.mt, bindings)
    return {root["id"]: root["path"] for root in roots}


def _sync_authorized_roots(ctx: Context, payload: Any = None) -> Dict[str, str]:
    return _known_roots_from_config(ctx)


def _norm_profile_id(v: Any) -> int:
    """ProfilePicker 在宿主侧是 number 类型；未选时为 0。统一归一为 int，非法/空→0。"""
    try:
        if v is None or v == "":
            return 0
        return int(v)
    except Exception:
        return 0

DEFAULT_CONFIG = {
    "localScanPath": "",
    "localProfileId": 0,
    "localDestDir": "",
    "cloudProfileId": 0,
    "cloudDestDir": "",
    "forceRefreshTmdb": True,
    "debugMode": True,
    "strm_local_prefix": "",
    "strm_cloud_prefix": "",
    "tmdb_style": "style_curly",
}


def _get_profile_map(ctx: Context) -> Dict[str, str]:
    """获取所有整理方案的 ID -> 名称 映射字典。优先从官方 SDK 动态读取。"""
    res_map = {}
    try:
        if hasattr(ctx.mt, "organize") and hasattr(ctx.mt.organize, "profiles"):
            p_res = ctx.mt.organize.profiles()
            if callable(p_res):
                p_res = p_res()
            items = p_res if isinstance(p_res, list) else (p_res.get("items") or p_res.get("data") or [])
            for it in items:
                if isinstance(it, dict) and "id" in it and "name" in it:
                    res_map[str(it["id"])] = str(it["name"])
    except Exception as e:
        logger.debug("动态获取方案列表失败: %s", e)
    return res_map


def _get_config(ctx: Context) -> Dict[str, Any]:
    """读取用户在工作台持久化保存的业务配置，并附带方案中文名称。

    唯一数据源为 MT 官方 storage（按安装实例隔离，卸载可能保留），
    不做任何外部文件持久化，避免重装后把旧用户路径重新带回界面。
    """
    cfg = dict(DEFAULT_CONFIG)

    stored = read_value(ctx, STORAGE_KEY_CONFIG, {})
    if not isinstance(stored, dict):
        raise ValueError("配置存储格式错误，禁止用默认值覆盖")
    if stored:
        if str(stored.get("cfgVersion") or "") != CONFIG_SCHEMA_VERSION:
            raise ValueError("配置版本不兼容，请先备份并迁移；不会静默丢弃旧配置")
        cfg.update({k: v for k, v in stored.items() if k not in ("cd2Url", "cd2VirtualPrefix", "cd2Token")})

    from series_core.local_source import saved_path
    cfg["localScanPath"] = saved_path(cfg)

    # 动态匹配方案名称与可读描述
    pmap = _get_profile_map(ctx)
    local_id = str(cfg.get("localProfileId") or "").strip()
    cloud_id = str(cfg.get("cloudProfileId") or "").strip()

    local_name = pmap.get(local_id, f"方案 {local_id}") if local_id else "未设置"
    cloud_name = pmap.get(cloud_id, f"方案 {cloud_id}") if cloud_id else "未设置"

    cfg["localProfileName"] = local_name
    cfg["cloudProfileName"] = cloud_name
    cfg["localProfileDesc"] = f"【{local_name}】(ID: {local_id})" if local_id else "【未选择方案】"
    cfg["cloudProfileDesc"] = f"【{cloud_name}】(ID: {cloud_id})" if cloud_id else "【未选择方案】"
    if "last_saved_at" not in cfg:
        cfg["last_saved_at"] = "默认初始配置"
    return cfg


def _get_state(ctx: Context, pages: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读取上一次扫描的状态数据（分键组装，防止单键超标）。"""
    default_state: Dict[str, Any] = {
        "status": "idle",
        "is_scanning": False,
        "total_dirs_scanned": 0,
        "share_links_count": 0,
        "local_links_count": 0,
        "mixed_links_count": 0,
        "protected_links_count": 0,
        "active_tab": "tab-share",
        "share_links": [],
        "local_links": [],
        "mixed_links": [],
        "protected_links": [],
        "last_scan_time": "尚未扫描",
    }
    snapshot = read_value(ctx, STORAGE_KEY_SNAPSHOT)
    if snapshot is not None:
        default_state.update(snapshot["meta"])
        for name in ("share_links", "local_links", "mixed_links"):
            if pages is not None:
                page = read_page(ctx, snapshot["datasets"][name], pages.get(name, 0))
                default_state[name] = page["rows"]
                default_state[name + "_count"] = page["count"]
                default_state[name + "_next"] = page["next"]
            else:
                default_state[name] = read_parts(ctx, snapshot["datasets"][name])
                default_state[name + "_count"] = len(default_state[name])
    else:
        default_state.update(read_value(ctx, STORAGE_KEY_STATE, {}))
        for name, key in (("share_links", STORAGE_KEY_SHARE), ("local_links", STORAGE_KEY_LOCAL),
                          ("mixed_links", STORAGE_KEY_MIXED)):
            values = read_value(ctx, key, [])
            default_state[name] = values
            default_state[name + "_count"] = len(values)

    # Invalidate out-of-scope legacy rows without mutating their immutable snapshot.
    eligible_paths = {d["dir_path"] for g in _get_full_groups(ctx) for d in g.get("dirs", [])}
    for name in ("share_links", "local_links"):
        old_rows = default_state[name]
        default_state[name] = [r for r in old_rows if r.get("local_path") in eligible_paths]
        default_state[name + "_count"] = max(0, default_state[name + "_count"] - len(old_rows) + len(default_state[name]))
    prot_map = _get_protected_no_year(ctx)
    default_state["protected_links"] = list(prot_map.values())
    default_state["protected_links_count"] = len(default_state["protected_links"])
    if pages is not None:
        # Legacy arrays and protection maps are already loaded, but still bound the response.
        for name in ("share_links", "local_links", "mixed_links", "protected_links"):
            if name + "_next" in default_state:
                continue
            values = default_state[name]
            offset = max(0, int(pages.get(name, 0)))
            rows: List[Dict[str, Any]] = []
            used = 2
            for row in values[offset:]:
                size = len(json.dumps(row, ensure_ascii=True).encode()) + 2
                if size + 2 > 35000:
                    raise ValueError("单条结果超过页面预算，请拆分明细")
                if len(rows) >= 20 or used + size > 35000:
                    break
                rows.append(row)
                used += size
            default_state[name] = rows
            end = offset + len(rows)
            default_state[name + "_next"] = end if end < len(values) else None
    # Older persisted snapshots predate table selection. Derive stable typed
    # identities on read as well as new scans; never require a rescan to select.
    previous_mixed_count = len(default_state["mixed_links"])
    default_state["mixed_links"] = [row for row in default_state["mixed_links"]
        if len(set(p for p in row.get("all_paths_text", "").splitlines() if p)) >= 2]
    default_state["mixed_links_count"] = max(0, default_state["mixed_links_count"] - previous_mixed_count + len(default_state["mixed_links"]))
    for row in default_state["mixed_links"]:
        row["group_key"] = str(row["media_type"]) + ":" + str(row["tmdb_id"])
        if isinstance(row.get("score_details"), str):
            row["score_details"] = row["score_details"].replace(" | unmatched | ", " | 公共槽 | ")
    return default_state


def _compact_for_storage(
    evaluated_groups: List[Dict[str, Any]],
    unidentified_dirs: List[Dict[str, Any]],
    protected_map: Optional[Dict[str, Dict[str, Any]]] = None,
):
    """精简并分类数据存储，严格划分为：分享链接、本地/云盘链接、混合链接评分组、无年份保护组。"""
    if protected_map is None:
        protected_map = {}

    share_links = []
    local_links = []
    mixed_links = []

    for g in evaluated_groups:
        if len(distinct_work_dirs(g)) < 2:
            continue
        tid = str(g.get("tmdb_id") or "")
        m_type = g.get("media_type", "tv")
        media_type_desc = g.get("media_type_desc") or ("🎬 电影" if m_type == "movie" else "📺 剧集")
        title = g.get("title") or ""
        year = g.get("year")
        official_title = f"{title} ({year})" if year else title
        is_mixed = g.get("is_mixed", False)
        group_key = f"{m_type}:{tid}"

        # 🛡️ 核心：检查是否无年份或已在保护组
        # 如果 TMDB 有 ID，但 TMDB 官方年份为空、None 或 0 / "0"，或者已被保护
        has_no_year = bool(tid and tid.isdigit() and (year is None or year == 0 or str(year) == "0"))
        is_already_protected = (group_key in protected_map)

        if has_no_year or is_already_protected:
            sample_dir = g.get("dirs", [{}])[0]
            dir_p = sample_dir.get("dir_path") or sample_dir.get("dir_name", "")
            d_name = sample_dir.get("dir_name", "")
            l_type = sample_dir.get("link_type", "other")
            reason_text = "TMDB 官方暂无上映年份 (年份为0/空)" if has_no_year else protected_map.get(group_key, {}).get("reason", "手动收入保护组")

            protected_map[group_key] = {
                "key": group_key,
                "tmdb_id": tid,
                "media_type": m_type,
                "media_type_desc": media_type_desc,
                "title": title or d_name,
                "dir_name": d_name,
                "local_path": dir_p,
                "link_type": l_type,
                "reason": reason_text,
                "added_at": protected_map.get(group_key, {}).get("added_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                "auto_added": has_no_year,
            }
            # 彻底跳过！绝不进入待重整列表，避免批量处理时白白重复提交
            continue

        if len(distinct_work_dirs(g)) < 2:
            continue

        if is_mixed:
            # 混合组按逐文件评分结论展示，不整目录删除。
            paths_list = list(dict.fromkeys(d.get("dir_path") or d.get("dir_name", "") for d in g.get("dirs", [])))
            mixed_links.append({
                "tmdb_id": tid,
                "group_key": f"{m_type}:{tid}",
                "media_type": m_type,
                "media_type_desc": media_type_desc,
                "title": title,
                "official_title": official_title,
                "dirs_count": len(g.get("dirs", [])),
                "format_label": " / ".join(sorted({f for d in g.get("dirs", []) for f in d.get("formats", [])})) or "未识别（地址无扩展名或旧扫描）",
                "lock_reason": g.get("status", "待评分"),
                "score_details": "\n".join(
                    f"{d.get('dir_name', '')} | {'胜者' if e.get('winner') else '败者'} | "
                    f"{e.get('name', '')} | {e.get('episode') or '电影'} | "
                    f"{slot_label(e.get('slot', ''))} | {e.get('link_kind', '')} | 分数 {e.get('score')} | {d.get('route_desc', '')}"
                    for d in g.get("dirs", [])
                    for e in d.get("winner_strms", []) + d.get("loser_strms", [])),
                "score_summary": " / ".join(
                    f"{d.get('dir_name', '')}: 胜{d.get('winner_count', 0)} 败{d.get('loser_count', 0)}"
                    for d in g.get("dirs", [])),
                "sample_dirs": " / ".join(d.get("dir_name", "") for d in g.get("dirs", [])[:2]),
                "local_path": paths_list[0] if paths_list else "",
                "all_paths_text": "\n".join(paths_list),
            })
            continue

        # 待重整处理的目录（与最新TMDB官方标题不符 / 缺年份 / 风格非标 / 重复）
        for d in g.get("dirs", []):
            if d.get("need_organize") and not d.get("disabled"):
                if d.get("dir_path") in protected_map:
                    continue

                link_type = d.get("link_type", "other")
                strm_source = d.get("strm_sample", "")
                cloudnas_path = d.get("cloudnas_path", "")
                compare_verdict = d.get("compare_verdict") or "❌ 比对失败（需重新提交整理）"
                item = {
                    "tmdb_id": tid,
                    "media_type": m_type,
                    "media_type_desc": media_type_desc,
                    "title": title,
                    "official_title": official_title,
                    "category": d.get("category", "未分类"),
                    "dir_name": d.get("dir_name"),
                    "local_path": d.get("dir_path", ""),
                    "link_type": link_type,
                    "format_label": d.get("format_label") or "未识别（地址无扩展名或旧扫描）",
                    "strm_source": strm_source,
                    "cloudnas_path": cloudnas_path,
                    "compare_verdict": compare_verdict,
                    "route_desc": d.get("route_desc"),
                    "diff_reason": d.get("diff_reason"),
                    "standard_target_name": d.get("standard_target_name"),
                    "assigned_profile_id": _norm_profile_id(d.get("assigned_profile_id")),
                    "assigned_dest_dir": d.get("assigned_dest_dir"),
                    "submit_status": "",
                }
                # 分流至对应分类表：除 http 分享直链外，全部归入本地/云盘链接表
                if link_type == "http":
                    share_links.append(item)
                else:
                    local_links.append(item)

    return share_links, local_links, mixed_links


def _set_rows_status(ctx: Context, local_paths, status_text: str, *, submitted: bool = True) -> None:
    """批量/单项提交前先把行状态置为"提交中"并落盘（v1.1.10）。

    submit_single/batch-submit 是同步长动作（收集 fileRef + 逐文件 manual.run），
    期间页面不会刷新；先落盘"⏳ 提交中"后，任何中途的 status 查询都能看到，
    配合页面 run.* 表达式禁用按钮，防止用户重复点击。
    """
    try:
        paths = set(local_paths or [])
        if not paths:
            return
        st = _get_state(ctx)
        share_links = st.get("share_links", [])
        local_links = st.get("local_links", [])
        for item in share_links + local_links:
            if item.get("local_path") in paths:
                item["submit_status"] = status_text
                item["is_submitted"] = submitted
        _save_state_split(ctx, st, share_links=share_links, local_links=local_links)
    except Exception as e:
        logger.warning("预置提交中状态失败: %s", e)


def _remove_entry_from_state(ctx: Context, path_to_remove: str, link_type: str) -> None:
    """从 state 中移除被删除或处理掉的条目，并更新计数（分键保存）。"""
    st = _get_state(ctx)
    share_links = [
        it for it in st.get("share_links", [])
        if it.get("local_path") != path_to_remove
    ]
    local_links = [
        it for it in st.get("local_links", [])
        if it.get("local_path") != path_to_remove
    ]
    st["share_links_count"] = len(share_links)
    st["local_links_count"] = len(local_links)
    remaining = []
    for group in _get_full_groups(ctx):
        group = dict(group)
        group["dirs"] = [row for row in group.get("dirs", [])
                         if (row.get("local_path") or row.get("dir_path")) != path_to_remove]
        if group["dirs"]:
            remaining.append(group)
    _save_state_split(ctx, st, share_links=share_links, local_links=local_links, full_groups=remaining)


def _execute_delete_entry(ctx: Context, local_path: str, link_type: str = "http") -> Dict[str, Any]:
    """按分类删除（m00681）：http 删本地 /strm；cloudnas 映射删网盘（MT任务确认后核验）。

    经官方 SDK files.stat 取得 fileRef 后 files.delete，不走本地 shutil。
    cloudnas 映射取全局配置 strm_local_prefix → strm_cloud_prefix。
    """
    cfg = _get_config(ctx)
    strm_local_prefix = cfg.get("strmLocalPrefix") or cfg.get("strm_local_prefix") or ""
    strm_cloud_prefix = cfg.get("strmCloudPrefix") or cfg.get("strm_cloud_prefix") or ""
    from series_core.deleter import sdk_delete_dir
    task = sdk_delete_dir(
        ctx.mt, local_path, link_type,
        strm_local_prefix=strm_local_prefix, strm_cloud_prefix=strm_cloud_prefix,
        known_roots=_known_roots_from_config(ctx, cfg),
        config=cfg,
    )
    return task


class Plugin(PluginBase):
    """影视查重（影视目录重整分流）插件。"""

    def validate_config(self, config: dict, ctx: Context) -> Optional[Dict[str, Any]]:
        """纯配置校验，符合 MediaTidy 宿主配置校验契约。"""
        return None

    def on_init(self, ctx: Context) -> None:
        cfg = _get_config(ctx)
        # 注意：启动时不得把内存配置无条件回写 storage——
        # 若升级后 storage 为空，回写会把空配置固化并覆盖掉本该保留的数据。
        # 仅在已有有效配置（含版本标记）时原样保留，无需任何回写动作。
        saved = ctx.mt.storage.get(key=STORAGE_KEY_PROTECTED)
        if not saved["found"]:
            ctx.mt.storage.set(key=STORAGE_KEY_PROTECTED, value={})
        logger.info("media.series-dedup 影视查重插件已初始化，已恢复跨版本持久化配置与保护组 (v1.2.2)")

    def on_stop(self, ctx: Context) -> None:
        """插件停止或更新卸载时的生命周期回调。"""
        logger.info("media.series-dedup 影视查重插件已平稳停止 (v1.2.2)")

    def get_page(self, ctx: Context):
        """返回纯静态 AST 声明页面，注入持久化配置初值至 state。"""

        # 读取最新持久化配置，作为页面表单控件的初始回显值（解决每次刷新/升级后表单变空的问题）
        cfg = _get_config(ctx)
        raw_local_p = str(cfg.get("strmLocalPrefix") or cfg.get("strm_local_prefix") or "").strip()
        raw_cloud_p = str(cfg.get("strmCloudPrefix") or cfg.get("strm_cloud_prefix") or "").strip()
        norm_local_p = _normalize_slashes(raw_local_p)
        norm_cloud_p = _normalize_slashes(raw_cloud_p)

        from series_core.local_source import saved_path
        cur_saved_root = saved_path(cfg)

        # 智能判定默认选中的 Tab：本地路径多就显示本地项，分享链接多就显示分享项，绝不留空
        last_state = _get_state(ctx, pages={})
        local_cnt = int(last_state.get("local_links_count", 0) or 0)
        share_cnt = int(last_state.get("share_links_count", 0) or 0)
        mixed_cnt = int(last_state.get("mixed_links_count", 0) or 0)
        prot_cnt = int(last_state.get("protected_links_count", 0) or 0)

        if local_cnt > share_cnt:
            default_active_tab = "tab-local"
        elif share_cnt > local_cnt:
            default_active_tab = "tab-share"
        elif share_cnt > 0:
            default_active_tab = "tab-share"
        elif local_cnt > 0:
            default_active_tab = "tab-local"
        elif mixed_cnt > 0:
            default_active_tab = "tab-mixed"
        elif prot_cnt > 0:
            default_active_tab = "tab-protected"
        else:
            default_active_tab = "tab-share"

        init_state = {
            "share_links": 0, "local_links": 0, "mixed_links": 0, "protected_links": 0,
            "scanPath": cur_saved_root,
            "localProfileId": _norm_profile_id(cfg.get("localProfileId")),
            "cloudProfileId": _norm_profile_id(cfg.get("cloudProfileId")),
            "localDestDir": str(cfg.get("localDestDir") or ""),
            "cloudDestDir": str(cfg.get("cloudDestDir") or ""),
            "strmLocalPrefix": norm_local_p,
            "strmCloudPrefix": norm_cloud_p,
            "strm_local_prefix": norm_local_p,
            "strm_cloud_prefix": norm_cloud_p,
            "tmdbStyle": str(cfg.get("tmdb_style") or "style_curly"),
            "tmdb_style": str(cfg.get("tmdb_style") or "style_curly"),
            "qualityRuleId": str(cfg.get("qualityRuleId") or ""),
            "rootBindingsJson": json.dumps(cfg.get("rootBindings", []), ensure_ascii=False, indent=2),
            "active_tab": default_active_tab,
        }

        # 官方原生扫描运行状态表达式（严格对齐官方指南第 325 行规范，挂载于扫描按钮 loading 属性）
        is_scan_running = ui.expr(
            "and",
            ui.expr("eq", ui.ref("run.action"), "scan"),
            ui.expr("or", ui.expr("eq", ui.ref("run.status"), "queued"), ui.expr("eq", ui.ref("run.status"), "running"))
        )

        # 提交运行状态（v1.1.10）：submit-single/batch-submit 同步长动作期间，
        # run.action/run.status 由宿主实时下发，按钮据此禁用+改文案，防重复点击
        is_submit_running = ui.expr(
            "and",
            ui.expr("or", ui.expr("eq", ui.ref("run.action"), "submit-single"),
                    ui.expr("eq", ui.ref("run.action"), "batch-submit"),
                    ui.expr("eq", ui.ref("run.action"), "submit-organize")),
            ui.expr("or", ui.expr("eq", ui.ref("run.status"), "queued"), ui.expr("eq", ui.ref("run.status"), "running"))
        )
        # 行级"提交中"判定：submit_status 含"提交中"时禁用该行按钮
        def _row_submitting():
            return ui.expr("includes", ui.ref("row.submit_status"), "提交中")

        top_banner = ui.Alert(
            text="【影视查重】目录规范化与多版本智能重整：以「片名 (年份) {tmdb-ID}」为标准，自动识别缺少年份、非标TMDB风格、未标注TMDB与多版本重复并分流重整；混合组按选定质量规则逐文件评分，清理败者后复扫；剩余双链混乱时保护。",
            tone="primary"
        )

        # 一、查重扫描与控制（扫描目录直接置顶方便选取，去除繁琐长文）
        scan_control_card = ui.Card(
            ui.Text(text=ui.expr(
                "concat",
                "📍 当前保存生效的扫描目录: 【",
                ui.expr(
                    "if",
                    ui.ref("query.status.data.config.localScanPath"),
                    ui.ref("query.status.data.config.localScanPath"),
                    "未设置（请填写本地目录）"
                ),
                "】",
                ui.expr(
                    "if",
                    ui.expr("and", ui.ref("state.scanPath"), ui.expr("ne", ui.ref("state.scanPath"), ui.ref("query.status.data.config.localScanPath"))),
                    "  ⚠️ 输入框已修改，尚未保存",
                    ""
                )
            )),
            ui.Input(label="本地 STRM 扫描目录", bind="state.scanPath",
                     placeholder="填写插件运行环境内的绝对路径"),
            ui.Alert(text="扫描直接读取本地目录，不受 MT 文件接口的分页或工作流文件数限制。整理、删除另按各自接口授权执行。", tone="muted"),
            ui.Stack(
                ui.Button(
                    label="💾 保存选定目录",
                    variant="outline",
                    onClick=[
                        {"type": "invoke", "actionId": "save-config", "input": {
                            "scanPath": ui.ref("state.scanPath"),
                        }},
                        {"type": "query", "queryId": "status"},
                    ]
                ),
                ui.Button(
                    label=ui.expr("if", is_scan_running, "⏳ 正在查重扫描中...", "🔍 立即启动查重扫描"),
                    variant="default",
                    disabled=is_scan_running,
                    loading=is_scan_running,
                    onClick=[
                        {"type": "invoke", "actionId": "scan", "input": {
                            "scanPath": ui.ref("state.scanPath"),
                            "tmdb_style": ui.ref("state.tmdbStyle"),
                        }},
                        {"type": "query", "queryId": "status"},
                        {
                            "type": "set",
                            "path": "state.active_tab",
                            "value": ui.expr(
                                "if",
                                ui.expr("gt", ui.ref("query.status.data.state.local_links_count"), ui.ref("query.status.data.state.share_links_count")),
                                "tab-local",
                                ui.expr(
                                    "if",
                                    ui.expr("gt", ui.ref("query.status.data.state.share_links_count"), 0),
                                    "tab-share",
                                    ui.expr(
                                        "if",
                                        ui.expr("gt", ui.ref("query.status.data.state.local_links_count"), 0),
                                        "tab-local",
                                        "tab-share"
                                    )
                                )
                            )
                        }
                    ]
                ),
                ui.Button(
                    label="🔄 刷新状态与数据",
                    variant="outline",
                    onClick=[
                        {"type": "query", "queryId": "status"},
                        {
                            "type": "set",
                            "path": "state.active_tab",
                            "value": ui.expr(
                                "if",
                                ui.expr("gt", ui.ref("query.status.data.state.local_links_count"), ui.ref("query.status.data.state.share_links_count")),
                                "tab-local",
                                ui.expr(
                                    "if",
                                    ui.expr("gt", ui.ref("query.status.data.state.share_links_count"), 0),
                                    "tab-share",
                                    ui.expr(
                                        "if",
                                        ui.expr("gt", ui.ref("query.status.data.state.local_links_count"), 0),
                                        "tab-local",
                                        "tab-share"
                                    )
                                )
                            )
                        }
                    ]
                ),
                ui.Button(
                    label="✨ 提交常规整理（不含混合组）",
                    variant="outline",
                    onClick=[
                        {"type": "invoke", "actionId": "submit-organize", "input": {}},
                        {"type": "query", "queryId": "status"},
                    ]
                ),
                ui.Button(
                    label="🧹 清空扫描缓存及自动无年份保护",
                    variant="ghost",
                    onClick=[
                        {"type": "invoke", "actionId": "clear", "input": {}},
                        {"type": "query", "queryId": "status"},
                    ]
                ),
                direction="row",
                gap="md"
            ),
            title="一、查重扫描与控制"
        )

        # 二、整理方案与路径配置（界面干净精简，去繁琐说明）
        config_card = ui.Card(
            ui.Text(text=ui.expr("concat", "⚙️ 当前生效方案: 本地【", ui.ref("query.status.data.config.localProfileName"), "】 ➔ ", ui.ref("query.status.data.config.localDestDir"), " | 云盘【", ui.ref("query.status.data.config.cloudProfileName"), "】 ➔ ", ui.ref("query.status.data.config.cloudDestDir"))),
            # 方案与目标设置
            ui.Grid(
                ui.ProfilePicker(label="本地整理方案 (针对 HTTP 分享直链)", bind="state.localProfileId"),
                ui.Input(label="分享链接转移目标目录", bind="state.localDestDir", placeholder="选填，如：/strm/影视库"),
                columns=2,
                gap="md"
            ),
            ui.Grid(
                ui.ProfilePicker(label="云盘整理方案 (针对 CloudNAS 本地挂载链)", bind="state.cloudProfileId"),
                ui.Input(label="本地链接转移目标目录", bind="state.cloudDestDir", placeholder="选填，如：/网盘挂载点/影视库"),
                columns=2,
                gap="md"
            ),
            ui.Stack(
                ui.Button(
                    label="💾 保存方案配置",
                    variant="default",
                    onClick=[
                        {"type": "invoke", "actionId": "save-config", "input": {
                            "localProfileId": ui.expr("if", ui.ref("state.localProfileId"), ui.ref("state.localProfileId"), 0),
                            "cloudProfileId": ui.expr("if", ui.ref("state.cloudProfileId"), ui.ref("state.cloudProfileId"), 0),
                            "localDestDir": ui.ref("state.localDestDir"),
                            "cloudDestDir": ui.ref("state.cloudDestDir"),
                        }},
                        {"type": "query", "queryId": "status"},
                    ]
                ),
                ui.Button(
                    label="🔄 刷新配置显示",
                    variant="outline",
                    onClick=[
                        {"type": "query", "queryId": "status"}
                    ]
                ),
                direction="row",
                gap="md"
            ),
            # 高级设置折叠收纳：日常默认收起，包含常用 TMDB 目录风格对照与 MT 云盘路径映射
            ui.Details(
                ui.Card(
                    ui.Select(
                        label="🎯 目标 TMDB 目录命名风格 (重整与比对规范目标)",
                        bind="state.tmdbStyle",
                        options=[
                            {"label": "【花括号标准】片名 (年份) {tmdb-ID}  (推荐)", "value": "style_curly"},
                            {"label": "【方括号 tmdbid】片名 (年份) [tmdbid=ID]", "value": "style_bracket_tmdbid"},
                            {"label": "【方括号 tmdb】片名 (年份) [tmdb-ID]", "value": "style_bracket_dash"},
                            {"label": "【点号后缀】片名 (年份).tmdb-ID", "value": "style_dot"},
                            {"label": "【无年份花括号】片名 {tmdb-ID}", "value": "style_no_year"},
                        ]
                    ),
                    ui.Text(
                        text="📌 常用风格对照示例：\n"
                             "• 花括号标准: 茶之味 (2004) {tmdb-2002} (推荐默认)\n"
                             "• 方括号tmdbid: 茶之味 (2004) [tmdbid=2002]\n"
                             "• 方括号tmdb: 茶之味 (2004) [tmdb-2002]\n"
                             "• 点号后缀: 茶之味 (2004).tmdb-2002\n"
                             "• 无年份花括号: 茶之味 {tmdb-2002}\n"
                             "※ 选定后，比对时将以所选风格为准；重整时也会按此风格预测目标目录名。"
                    ),
                    ui.Text(text="评分与 MT 使用相同的 10 倍刻度：权重 × 位置分（最高 10）；总分与分差均按此口径，多槽位分别比较，忽略最小分差阈值。"),
                    ui.Grid(
                        ui.Select(label="质量评分规则（多槽位查重，忽略最小分差）", bind="state.qualityRuleId",
                                  options=ui.ref("query.qualityrules.data.options")),
                        ui.Input(label="本地 STRM 目录前缀", bind="state.strmLocalPrefix", placeholder="选填映射，如：/strm/电影"),
                        ui.Input(label="MT 云盘挂载目录前缀（整理用）", bind="state.strmCloudPrefix", placeholder="选填映射，如：/网盘挂载点/电影"),
                        columns=2,
                        gap="md"
                    ),
                    ui.Button(label="测试MT云盘路径映射（只读）", onClick=[{"type":"invoke", "actionId":"test-mt-mapping", "input":{}}]),
                    ui.Text(text=ui.ref("query.status.data.mt_mapping.message")),


                    ui.Text(text="授权根绑定：将下方可读根ID与该根对应的完整挂载目录显式配对（不是媒体子目录）。保存不会授予权限，所有文件操作仍由MT校验。"),
                    ui.Text(text=ui.ref("query.authorizedroots.data.description")),
                    ui.Input(label="授权根绑定JSON（rootId / mountPath）", bind="state.rootBindingsJson", placeholder='[{"rootId":"从上方复制真实ID","mountPath":"/对应完整挂载根"}]'),
                    ui.Button(label="保存授权根绑定（不执行媒体操作）", onClick=[{"type":"invoke", "actionId":"save-root-bindings", "input":{"bindingsJson":ui.ref("state.rootBindingsJson")}}, {"type":"query", "queryId":"status"}]),
                    ui.Button(
                        label="💾 保存高级设置（评分规则与三方路径映射）",
                        variant="outline",
                        onClick=[
                            {"type": "invoke", "actionId": "save-config", "input": {
                                "tmdb_style": ui.ref("state.tmdbStyle"),
                                "tmdbStyle": ui.ref("state.tmdbStyle"),
                                "strm_local_prefix": ui.ref("state.strmLocalPrefix"),
                                "strm_cloud_prefix": ui.ref("state.strmCloudPrefix"),
                                "strmLocalPrefix": ui.ref("state.strmLocalPrefix"),
                                "strmCloudPrefix": ui.ref("state.strmCloudPrefix"),
                                "qualityRuleId": ui.ref("state.qualityRuleId"),
                            }},
                            {"type": "query", "queryId": "status"},
                        ]
                    )
                ),
                label="⚙️ 高级设置：TMDB 目录命名风格 & MT 云盘路径映射 (默认收起，点击展开)"
            ),
            title="二、整理方案与路径配置"
        )

        # 三、核心指标网格（Grid 4 列）
        metrics_grid = ui.Grid(
            ui.Metric(label="全库扫描目录总数", value=ui.ref("query.status.data.state.total_dirs_scanned"), tone="primary"),
            ui.Metric(label="🌐 分享链接待重整数", value=ui.ref("query.status.data.state.share_links_count"), tone="warning"),
            ui.Metric(label="☁️ 本地链接待重整数", value=ui.ref("query.status.data.state.local_links_count"), tone="default"),
            ui.Metric(label="🛡️ 无年份保护组", value=ui.ref("query.status.data.state.protected_links_count"), tone="primary"),
            columns=4
        )

        # 四、辅助构建纯静态表头
        def _col(label: str, field: str) -> Dict[str, Any]:
            return {
                "label": label,
                "cells": [ui.Text(text=ui.ref("row." + field))]
            }

        # 1. 🌐 分享链接分类表格（HTTP 直链，适配本地方案）
        share_toolbar = ui.Stack(
            ui.Button(
                label=ui.expr("if", is_submit_running, "⏳ 提交中…", "🚀 批量提交勾选项"),
                variant="default",
                disabled=is_submit_running,
                onClick=[
                    {"type": "invoke", "actionId": "batch-submit", "input": {
                        "selectedPaths": ui.ref("input.selectedSharePaths"),
                        "linkType": "http",
                    }},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            ui.Button(
                label="🗑️ 批量彻底删除所选",
                variant="destructive",
                onClick=[
                    {"type": "invoke", "actionId": "batch-delete", "input": {
                        "selectedPaths": ui.ref("input.selectedSharePaths"),
                        "linkType": "http",
                    }},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            direction="row",
            gap="md"
        )

        share_table = ui.Table(
            source=ui.ref("query.status.data.state.share_links"),
            selectionBind="input.selectedSharePaths",
            rowKey="local_path",
            columns=[
                _col("TMDB ID", "tmdb_id"),
                _col("类型", "media_type_desc"),
                _col("视频格式", "format_label"),
                _col("TMDB 官方标题", "official_title"),
                _col("比对结论", "compare_verdict"),
                _col("当前目录名", "dir_name"),
                _col("分类", "category"),
                _col("需重整原因", "diff_reason"),
                {
                    "label": "本地完整路径",
                    "cells": [
                        ui.Details(
                            ui.Text(text=ui.ref("row.local_path")),
                            label="📂 展开查看绝对路径"
                        )
                    ]
                },
                _col("分流链路与方案", "route_desc"),
                _col("预测规范名称", "standard_target_name"),
                {
                    "label": "操作 / 整理状态",
                    "cells": [
                        ui.Text(text=ui.ref("row.submit_status")),
                        ui.Button(
                            label=ui.expr("if", _row_submitting(), "⏳ 提交中…", "🚀 重新提交整理"),
                            variant="default",
                            disabled=_row_submitting(),
                            onClick=[
                                {"type": "invoke", "actionId": "submit-single", "input": {
                                    "localPath": ui.ref("row.local_path"),
                                    "profileId": ui.expr("if", ui.ref("row.assigned_profile_id"), ui.ref("row.assigned_profile_id"), 0),
                                    "destDir": ui.ref("row.assigned_dest_dir"),
                                    "tmdbId": ui.ref("row.tmdb_id"),
                                    "mediaType": ui.ref("row.media_type"),
                                    "linkType": ui.ref("row.link_type"),
                                    "cloudnasPath": ui.ref("row.cloudnas_path"),
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        ),
                        ui.Button(
                            label="🛡️ 收入保护组",
                            variant="outline",
                            onClick=[
                                {"type": "invoke", "actionId": "protect-item", "input": {
                                    "localPath": ui.ref("row.local_path"),
                                    "tmdbId": ui.ref("row.tmdb_id"),
                                    "mediaType": ui.ref("row.media_type"),
                                    "title": ui.ref("row.title"),
                                    "dirName": ui.ref("row.dir_name"),
                                    "linkType": ui.ref("row.link_type"),
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        ),
                        ui.Button(
                            label="🗑️ 彻底删除",
                            variant="destructive",
                            onClick=[
                                {"type": "invoke", "actionId": "delete-single", "input": {
                                    "localPath": ui.ref("row.local_path"),
                                    "linkType": "http",
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        )
                    ]
                }
            ]
        )

        # 2. ☁️ 本地/云盘链接分类表格（云盘挂载链，适配云盘方案）
        local_toolbar = ui.Stack(
            ui.Button(
                label=ui.expr("if", is_submit_running, "⏳ 提交中…", "🚀 批量提交勾选项"),
                variant="default",
                disabled=is_submit_running,
                onClick=[
                    {"type": "invoke", "actionId": "batch-submit", "input": {
                        "selectedPaths": ui.ref("input.selectedLocalPaths"),
                        "linkType": "cloudnas",
                    }},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            ui.Button(
                label="🗑️ 批量彻底删除所选",
                variant="destructive",
                onClick=[
                    {"type": "invoke", "actionId": "batch-delete", "input": {
                        "selectedPaths": ui.ref("input.selectedLocalPaths"),
                        "linkType": "cloudnas",
                    }},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            direction="row",
            gap="md"
        )

        local_table = ui.Table(
            source=ui.ref("query.status.data.state.local_links"),
            selectionBind="input.selectedLocalPaths",
            rowKey="local_path",
            columns=[
                _col("TMDB ID", "tmdb_id"),
                _col("类型", "media_type_desc"),
                _col("视频格式", "format_label"),
                _col("TMDB 官方标题", "official_title"),
                _col("比对结论", "compare_verdict"),
                _col("当前目录名", "dir_name"),
                _col("分类", "category"),
                _col("需重整原因", "diff_reason"),
                {
                    "label": "本地完整路径",
                    "cells": [
                        ui.Details(
                            ui.Text(text=ui.ref("row.local_path")),
                            label="📂 展开查看绝对路径"
                        )
                    ]
                },
                _col("分流链路与方案", "route_desc"),
                _col("预测规范名称", "standard_target_name"),
                {
                    "label": "操作 / 整理状态",
                    "cells": [
                        ui.Text(text=ui.ref("row.submit_status")),
                        ui.Button(
                            label=ui.expr("if", _row_submitting(), "⏳ 提交中…", "🚀 重新提交整理"),
                            variant="default",
                            disabled=_row_submitting(),
                            onClick=[
                                {"type": "invoke", "actionId": "submit-single", "input": {
                                    "localPath": ui.ref("row.local_path"),
                                    "profileId": ui.expr("if", ui.ref("row.assigned_profile_id"), ui.ref("row.assigned_profile_id"), 0),
                                    "destDir": ui.ref("row.assigned_dest_dir"),
                                    "tmdbId": ui.ref("row.tmdb_id"),
                                    "mediaType": ui.ref("row.media_type"),
                                    "linkType": ui.ref("row.link_type"),
                                    "cloudnasPath": ui.ref("row.cloudnas_path"),
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        ),
                        ui.Button(
                            label="🛡️ 收入保护组",
                            variant="outline",
                            onClick=[
                                {"type": "invoke", "actionId": "protect-item", "input": {
                                    "localPath": ui.ref("row.local_path"),
                                    "tmdbId": ui.ref("row.tmdb_id"),
                                    "mediaType": ui.ref("row.media_type"),
                                    "title": ui.ref("row.title"),
                                    "dirName": ui.ref("row.dir_name"),
                                    "linkType": ui.ref("row.link_type"),
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        ),
                        ui.Button(
                            label="🗑️ 彻底删除",
                            variant="destructive",
                            onClick=[
                                {"type": "invoke", "actionId": "delete-single", "input": {
                                    "localPath": ui.ref("row.local_path"),
                                    "linkType": "cloudnas",
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        )
                    ]
                }
            ]
        )

        # 3. 混合组评分与败者清理
        mixed_table = ui.Table(
            source=ui.ref("query.status.data.state.mixed_links"),
            rowKey="group_key",
            selectionBind="input.selectedMixedGroups",
            columns=[
                _col("TMDB ID", "tmdb_id"),
                _col("类型", "media_type_desc"),
                _col("视频格式", "format_label"),
                _col("TMDB 官方标题", "official_title"),
                _col("包含版本数", "dirs_count"),
                _col("评分状态", "lock_reason"),
                {
                    "label": "各版本本地路径",
                    "cells": [
                        ui.Details(
                            ui.Text(text=ui.ref("row.all_paths_text")),
                            label="📂 展开查看各版本绝对路径"
                        )
                    ]
                },
                {
                    "label": "保护策略",
                    "cells": [
                        ui.Text(text=ui.ref("row.score_summary")),
                        ui.Details(ui.Text(text=ui.ref("row.score_details")), label="查看逐文件胜败与分数"),
                        ui.Button(label="处理本组：清理败者并整理胜者", variant="destructive", onClick=[
                            {"type": "invoke", "actionId": "cleanup-mixed", "input": {
                                "tmdbId": ui.ref("row.tmdb_id"), "mediaType": ui.ref("row.media_type")}}
                        ])
                    ]
                }
            ]
        )

        mixed_toolbar = ui.Stack(
            ui.Text(text="混合组在此处理，不用顶部常规整理按钮。评分规则使用前面高级设置中已保存的配置。① 扫描 ② 勾选作品并处理 ③ 确认败者源消失后清理索引、伴随文件及空目录 ④ 按本地/云盘方案整理明确胜者。失败或同步待确认时停止；跳过项和 chaos 保护项不会提交。"),
            ui.Stack(
                ui.Button(label="处理勾选混合组：清理败者并整理胜者", variant="destructive", onClick=[
                    {"type": "invoke", "actionId": "batch-cleanup-mixed", "input": {
                        "selectedGroups": ui.ref("input.selectedMixedGroups")}}]),
                ui.Button(label="刷新处理状态", variant="outline", onClick=[
                    {"type": "query", "queryId": "status"}]),
                direction="row", gap="sm"),
            ui.Input(label="待核验的混合流程记录键（失败提示中的 mixed-pipelines/...；续接会清理及整理，不会重发已提交删除）", bind="state.mixedResumeKey"),
            ui.Button(label="核验并续接混合流程", variant="outline", onClick=[
                {"type":"invoke", "actionId":"resume-mixed", "input":{"key":ui.ref("state.mixedResumeKey")}}]),
            gap="md")

        # 4. 🛡️ 无年份/免处理保护组表格
        protected_toolbar = ui.Stack(
            ui.Button(
                label="🔄 批量向 TMDB 查验年份并自动释放",
                variant="default",
                onClick=[
                    {"type": "invoke", "actionId": "check-protected-tmdb", "input": {}},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            direction="row",
            gap="md"
        )

        protected_table = ui.Table(
            source=ui.ref("query.status.data.state.protected_links"),
            rowKey="key",
            columns=[
                _col("TMDB ID", "tmdb_id"),
                _col("类型", "media_type_desc"),
                _col("视频格式", "format_label"),
                _col("作品名称", "title"),
                _col("保护原因", "reason"),
                _col("收入时间", "added_at"),
                {
                    "label": "本地完整路径",
                    "cells": [
                        ui.Details(
                            ui.Text(text=ui.ref("row.local_path")),
                            label="📂 展开查看绝对路径"
                        )
                    ]
                },
                {
                    "label": "操作",
                    "cells": [
                        ui.Button(
                            label="🔄 查验年份",
                            variant="outline",
                            onClick=[
                                {"type": "invoke", "actionId": "check-protected-tmdb", "input": {
                                    "key": ui.ref("row.key"),
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        ),
                        ui.Button(
                            label="🔓 移出保护组",
                            variant="destructive",
                            onClick=[
                                {"type": "invoke", "actionId": "unprotect-item", "input": {
                                    "key": ui.ref("row.key"),
                                    "localPath": ui.ref("row.local_path"),
                                }},
                                {"type": "query", "queryId": "status"}
                            ]
                        )
                    ]
                }
            ]
        )

        def pager(name):
            return ui.Stack(
                ui.Text(text=ui.expr("concat", "总条目：", ui.ref(f"query.status.data.state.{name}_count"),
                                    "；每页最多 20 项（同时限制字节大小）")),
                ui.Button(label="首页 / 刷新", variant="outline", onClick=[
                    {"type": "set", "path": f"state.{name}", "value": 0},
                    {"type": "query", "queryId": "status"},
                ]),
                ui.Button(label="下一页", variant="outline",
                    disabled=ui.expr("eq", ui.ref(f"query.status.data.state.{name}_next"), None),
                    onClick=[{"type": "set", "path": f"state.{name}",
                              "value": ui.ref(f"query.status.data.state.{name}_next")}]),
                direction="row", gap="sm",
            )

        # 五、分类选项卡（Tabs，精简标题，保证一屏完整显示）
        tabs_section = ui.Tabs(
            ui.Tab(
                share_toolbar,
                pager("share_links"),
                share_table,
                id="tab-share",
                label="🌐 分享链接"
            ),
            ui.Tab(
                local_toolbar,
                pager("local_links"),
                local_table,
                id="tab-local",
                label="☁️ 本地/云盘"
            ),
            ui.Tab(
                mixed_toolbar,
                pager("mixed_links"),
                mixed_table,
                id="tab-mixed",
                label="⚖️ 混合组评分"
            ),
            ui.Tab(
                protected_toolbar,
                pager("protected_links"),
                protected_table,
                id="tab-protected",
                label="🛡️ 无年份保护组"
            ),
            bind="state.active_tab"
        )

        submit_banner = ui.Alert(
            text="⏳ 正在提交整理任务（收集文件 → 逐文件入队），请勿重复点击；完成后本行会显示 ✅/❌ 结果。",
            tone="warning",
            visible=is_submit_running,
        )

        return ui.page(
            top_banner,
            submit_banner,
            scan_control_card,
            config_card,
            metrics_grid,
            ui.Card(tabs_section, title="📋 扫描结果分类明细"),
            state=init_state,
            queries=[
                {"id": "status", "name": "status", "input": {
                    name: ui.ref(f"state.{name}") for name in
                    ("share_links", "local_links", "mixed_links", "protected_links")}},
                {"id": "profiles", "name": "profiles"},
                {"id": "qualityrules", "name": "qualityrules"},
                {"id": "authorizedroots", "name": "authorizedroots"},
            ]
        )

    # ── 查询 Queries ─────────────────────────────────────────────

    @query("status")
    def status(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        state = _get_state(ctx, pages=input)
        cfg = _get_config(ctx)
        return {
            "config": cfg,
            "state": state,
            "mt_mapping": read_value(ctx, "mt/mapping", {"message": "MT云盘映射尚未测试"}),
        }

    @query("authorizedroots")
    def authorized_roots(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        roots = ctx.mt.files.roots()
        if not isinstance(roots, list):
            raise ValueError("files.roots 返回格式错误")
        return {"description": "当前可读授权根（名称不用于推导路径）：\n" + "\n".join(
            str(r["id"]) + " — " + str(r.get("name") or "")
            for r in roots if isinstance(r, dict) and r.get("read") and r.get("id"))}

    @action("save-root-bindings")
    def save_root_bindings(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.roots import authorized_bindings
        bindings = json.loads(input["bindingsJson"])
        if not isinstance(bindings, list) or len(bindings) > 30:
            raise ValueError("授权根绑定必须为数组且最多30项")
        for entry in bindings:
            if not isinstance(entry, dict) or set(entry) != {"rootId", "mountPath"}:
                raise ValueError("每项只能包含 rootId 与 mountPath")
        roots = authorized_bindings(ctx.mt, bindings)
        for root in roots:
            stat = ctx.mt.files.stat(root_id=root["id"], path=".")
            if not isinstance(stat, dict) or stat.get("isDir") is not True:
                raise ValueError("绑定授权根不可读取或不是目录")
        cfg = _get_config(ctx)
        cfg["rootBindings"] = bindings
        write_value(ctx, STORAGE_KEY_CONFIG, cfg)
        write_value(ctx, "mt/mapping", {"ok": False, "message": "授权根绑定已保存，请再运行只读映射测试；未执行删除"})
        return {"ok": True, "message": "授权根绑定已保存；未执行媒体操作"}

    @query("qualityrules")
    def quality_rules(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        options = [{"label": "跟随常规整理方案", "value": ""}]
        try:
            offset = 0
            for _ in range(20):
                result = ctx.mt.rules.list(family="quality", offset=offset, limit=100)
                for item in result.get("items", []):
                    if item.get("enabled") is True:
                        options.append({"label": str(item.get("name") or item.get("id")), "value": item.get("id")})
                next_offset = result.get("nextOffset")
                if next_offset is None or (isinstance(next_offset, (int, float)) and next_offset <= 0):
                    return {"options": options, "error": ""}
                if not isinstance(next_offset, (int, float)) or next_offset <= offset:
                    raise ValueError("质量规则分页未前进")
                offset = int(next_offset)
            raise ValueError("质量规则超过显示上限")
        except Exception as exc:
            return {"options": options, "error": "读取质量规则失败：" + str(exc)}

    @query("profiles")
    def profiles(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """通过官方 SDK 读取当前系统所有已授权的整理方案。"""
        try:
            if hasattr(ctx.mt, "organize") and hasattr(ctx.mt.organize, "profiles"):
                res = ctx.mt.organize.profiles()
                if callable(res):
                    res = res()
                items = res if isinstance(res, list) else (res.get("items") or res.get("data") or [])
                if items:
                    return {"profiles": [{"id": _norm_profile_id(p.get("id")), "name": p.get("name")} for p in items if isinstance(p, dict)]}
        except Exception as e:
            logger.warning("通过官方 SDK 读取方案失败: %s", e)

        return {"profiles": []}

    # ── 动作 Actions ─────────────────────────────────────────────

    @action("save-config")
    def save_config(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _get_config(ctx)

        from series_core.local_source import scan_path, saved_path
        selection = scan_path(input.get("scanPath", saved_path(cfg)))
        cfg["localScanPath"] = selection
        cfg.pop("rootPath", None)
        cfg.pop("scanDirectory", None)

        # 分享链接转移目录（仅非空时覆盖，避免某次保存未填而清空历史值）
        local_dest = (str(input.get("localDestDir") or input.get("localDestDir_sel") or "")).strip()
        if local_dest:
            cfg["localDestDir"] = local_dest

        # 本地链接转移目录（同上，仅非空时覆盖）
        cloud_dest = (str(input.get("cloudDestDir") or input.get("cloudDestDir_sel") or "")).strip()
        if cloud_dest:
            cfg["cloudDestDir"] = cloud_dest

        if "qualityRuleId" in input:
            cfg["qualityRuleId"] = str(input["qualityRuleId"] or "").strip()

        # 方案、风格与映射配置
        for k in ["localProfileId", "cloudProfileId"]:
            if k in input and input[k] is not None and str(input[k]).strip() not in ("", "0"):
                cfg[k] = _norm_profile_id(input[k])
        for k in ["strmLocalPrefix", "strmCloudPrefix", "strm_local_prefix", "strm_cloud_prefix", "tmdbStyle", "tmdb_style", "qualityRuleId"]:
            if k in input and input[k] is not None and str(input[k]).strip() != "":
                val = str(input[k]).strip()
                cfg[k] = val
                if k.startswith("strm") or k.startswith("strm_"):
                    val = _normalize_slashes(val)
                    cfg[k] = val
                if k == "tmdbStyle":
                    cfg["tmdb_style"] = val
                elif k == "tmdb_style":
                    cfg["tmdbStyle"] = val
                elif k == "strmLocalPrefix":
                    cfg["strm_local_prefix"] = val
                elif k == "strm_local_prefix":
                    cfg["strmLocalPrefix"] = val
                elif k == "strmCloudPrefix":
                    cfg["strm_cloud_prefix"] = val
                elif k == "strm_cloud_prefix":
                    cfg["strmCloudPrefix"] = val

        from datetime import datetime
        cfg["last_saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 同步更新方案名称描述
        pmap = _get_profile_map(ctx)
        local_id = str(cfg.get("localProfileId") or "").strip()
        cloud_id = str(cfg.get("cloudProfileId") or "").strip()
        local_name = pmap.get(local_id, f"方案 {local_id}") if local_id else "未设置"
        cloud_name = pmap.get(cloud_id, f"方案 {cloud_id}") if cloud_id else "未设置"
        cfg["localProfileName"] = local_name
        cfg["cloudProfileName"] = cloud_name
        cfg["localProfileDesc"] = f"【{local_name}】(ID: {local_id})" if local_id else "未设置（请在下方选择）"
        cfg["cloudProfileDesc"] = f"【{cloud_name}】(ID: {cloud_id})" if cloud_id else "未设置（请在下方选择）"

        cfg["cfgVersion"] = CONFIG_SCHEMA_VERSION
        if not _storage_set_safe(ctx, STORAGE_KEY_CONFIG, cfg, max_bytes=20000):
            return {"saved": False, "ok": False, "error": "storage.set 失败：插件没有 storage 授权或写入超限，请在插件中心重新授权后再保存；旧配置未被修改。"}
        success_msg = f"✅ 配置已成功保存！当前生效扫描目录：【{cfg.get('localScanPath')}】"
        ctx.mt.log(level="info", message=f"{success_msg}，本地方案 {cfg.get('localProfileDesc')}，云盘方案 {cfg.get('cloudProfileDesc')}")
        return {
            "saved": True,
            "message": success_msg,
            "config": cfg
        }

    @action("scan")
    def scan(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _get_config(ctx)

        from series_core.local_source import scan_path, saved_path
        target_dir = scan_path(input.get("scanPath", saved_path(cfg)))

        def scan_failed(message):
            state = _get_state(ctx)
            state.update(status="error", is_scanning=False, last_error=message)
            _save_state_split(ctx, state)
            ctx.mt.log(level="error", message=message)
            raise ValueError(message)

        ctx.mt.log(level="info", message=f"扫描请求：输入路径={input.get('scanPath')!r}，已保存路径={cfg.get('localScanPath')!r}，实际目标={target_dir!r}")
        if not target_dir:
            scan_failed("未设置扫描目录，请填写并保存容器内的实际目录。")

        _resolved = target_dir
        try:
            if not os.path.isdir(_resolved):
                scan_failed(f"扫描目录不存在或不是目录：{target_dir}（解析为 {_resolved}）")
            with os.scandir(_resolved) as entries:
                next(entries, None)
        except OSError as exc:
            scan_failed(f"无法读取扫描目录：{_resolved}: {type(exc).__name__}: {exc}")

        selected_dirs = [target_dir]

        # ⚠️ 扫描动作只读取目标目录，绝不回写 config。
        # 原因：扫描按钮的 scanPath 来自前端 state，若浏览器里残留旧值，
        # 回写会把用户已正确保存的目录覆盖成旧值（历史上正是这个 bug 造成"保存后又变回去"）。

        local_pid = input.get("localProfileId") or cfg.get("localProfileId") or ""
        local_dest = input.get("localDestDir") or cfg.get("localDestDir") or ""
        cloud_pid = input.get("cloudProfileId") or cfg.get("cloudProfileId") or ""
        cloud_dest = input.get("cloudDestDir") or cfg.get("cloudDestDir") or ""
        force_tmdb = True  # 重新查询宿主元数据，不代表强制刷新官网缓存
        target_style = input.get("tmdb_style") or input.get("tmdbStyle") or cfg.get("tmdb_style") or "style_curly"
        if cfg.get("tmdb_style") != target_style:
            cfg["tmdb_style"] = target_style
            _storage_set_safe(ctx, STORAGE_KEY_CONFIG, cfg, max_bytes=20000)

        # 标记为正在扫描，避免重复点击
        current_state = _get_state(ctx)
        current_state["is_scanning"] = True
        current_state["status"] = "scanning"
        _save_state_split(ctx, current_state)

        try:
            # 🛡️ 智能探活：对当前已在保护组的条目，向 TMDB 查验最新年份；如果官方已补全年份，自动释放！
            prot_map = _get_protected_no_year(ctx)
            if prot_map:
                probe_targets = []
                for k, it in prot_map.items():
                    if not isinstance(it, dict):
                        continue
                    tid = it.get("tmdb_id")
                    mtype = it.get("media_type") or "tv"
                    if tid and str(tid).isdigit():
                        probe_targets.append((mtype, int(tid)))
                if probe_targets:
                    try:
                        probe_caches = refresh_tmdb_batch(probe_targets, ctx=ctx)
                        released_keys = []
                        released_names = []
                        for k, it in prot_map.items():
                            if not isinstance(it, dict):
                                continue
                            tid = it.get("tmdb_id")
                            mtype = it.get("media_type") or "tv"
                            if tid and str(tid).isdigit():
                                tinfo = probe_caches.get((mtype, int(tid))) or probe_caches.get(int(tid))
                                if tinfo:
                                    y = tinfo.get("year")
                                    if y and isinstance(y, int) and y > 1900:
                                        released_keys.append(k)
                                        released_names.append(f"《{it.get('title')}》({y})")
                        if released_keys:
                            for rk in released_keys:
                                del prot_map[rk]
                            _save_protected_no_year(ctx, prot_map)
                            ctx.mt.log(level="info", message=f"🎉 TMDB 探活复查: 官方已补全年份，已自动释放 {len(released_keys)} 部作品放回重整列表: {', '.join(released_names)}")
                    except Exception as pe:
                        logger.warning("探活复查保护组异常: %s", pe)

            ctx.mt.log(level="info", message=f"开始极速本地查重，目标目录数: {len(selected_dirs)} (样项目录: {selected_dirs[0]})")
            scan_res = scan_media_library(selected_dirs,
                progress_callback=lambda event: ctx.mt.log(level="info", message=f"扫描进度：{event}"))
            groups = scan_res.get("groups", [])
            unidentified = scan_res.get("failed_dirs", [])

            _scan_errs = scan_res.get("scan_errors") or []
            if _scan_errs:
                for _e in _scan_errs:
                    ctx.mt.log(level="error", message=_e)
            tmdb_targets = [(g.get("media_type", "tv"), g["tmdb_id"]) for g in groups]
            caches = query_mt_tmdb_caches(tmdb_targets, ctx=ctx)

            # 针对差异/重复作品组重新查询宿主元数据；不声称强制刷新缓存。
            # 解决本地 TMDB 缓存残缺、年份为空或旧标题导致比对和整理出错的根因！
            need_sync_targets = []
            for g in groups:
                mtype = g.get("media_type", "tv")
                tid = g.get("tmdb_id")
                cached_info = caches.get((mtype, tid)) or caches.get(tid) or {}
                # 触发宿主元数据重新查询的条件：
                # 1. 本地无缓存或缓存残缺（缺少年份/标题）；
                # 2. 属于多版本重复作品（需依据官网最新标题裁定胜负）；
                # 3. 属于非标/异常目录（需依据官网最新元数据规范重整）。
                is_incomplete = not cached_info or not cached_info.get("year") or not cached_info.get("title")
                if is_incomplete or g.get("is_duplicate") or g.get("has_non_standard"):
                    need_sync_targets.append((mtype, tid))

            if need_sync_targets:
                ctx.mt.log(level="info", message=f"🔥 已聚类出 {len(need_sync_targets)} 组差异/重复作品，正在通过宿主 SDK 查询 TMDB 标题与年份（不保证强制刷新缓存）...")
                try:
                    refreshed = refresh_tmdb_batch(need_sync_targets, ctx=ctx)
                    caches.update(refreshed)
                    ctx.mt.log(level="info", message=f"✅ TMDB SDK 查询完成，已获取 {len(refreshed)} 部作品最新标准元数据")
                except Exception as te:
                    logger.debug("批量同步 TMDB 异常: %s", te)

            # Optional host category verification is a separate operation.
            # A large local library must never fail because HTTP calls are capped.
            evaluated = match_duplicate_groups(
                groups,
                caches,
                local_profile_id=int(local_pid) if str(local_pid).isdigit() else 0,
                local_dest_dir=local_dest,
                cloud_profile_id=int(cloud_pid) if str(cloud_pid).isdigit() else 0,
                cloud_dest_dir=cloud_dest,
                target_style=target_style,
            )

            # Mixed groups follow mt-console file-level scoring, not blanket locks.
            from series_core.mixed_service import configured_rule
            from series_core.mixed_flow import assess
            for group in evaluated:
                if not group.get("is_mixed"):
                    continue
                try:
                    assess(group, configured_rule(ctx.mt, cfg), cfg)
                except Exception as exc:
                    group["mixed_scoring"] = {"error": str(exc)}
                    group["status"] = "评分未完成：" + str(exc)
                    for row in group.get("dirs", []):
                        row.update(disabled=True, need_organize=False, delete_candidate=False,
                                   delete_entries=[], disabled_reason="评分未完成：" + str(exc))
            share_links, local_links, mixed_links = _compact_for_storage(evaluated, unidentified, prot_map)
            _save_protected_no_year(ctx, prot_map)

            # 智能判定胜出 Tab：扫描本地路径多久显示本地项，分享链接多就显示分享项，绝不留空白
            local_cnt = len(local_links)
            share_cnt = len(share_links)
            mixed_cnt = len(mixed_links)
            prot_cnt = len(prot_map)

            if local_cnt > share_cnt:
                determined_tab = "tab-local"
            elif share_cnt > local_cnt:
                determined_tab = "tab-share"
            elif share_cnt > 0:
                determined_tab = "tab-share"
            elif local_cnt > 0:
                determined_tab = "tab-local"
            elif mixed_cnt > 0:
                determined_tab = "tab-mixed"
            elif prot_cnt > 0:
                determined_tab = "tab-protected"
            else:
                determined_tab = "tab-share"

            new_state = {
                "status": "ready",
                "is_scanning": False,
                "total_dirs_scanned": scan_res.get("total_dirs_scanned", len(groups)),
                "share_links_count": share_cnt,
                "local_links_count": local_cnt,
                "mixed_links_count": mixed_cnt,
                "protected_links_count": prot_cnt,
                "active_tab": determined_tab,
                "last_scan_time": "刚刚",
                "visited_dirs": scan_res.get("visited_dirs", 0),
                "recognized_works": scan_res.get("recognized_works", 0),
                "unidentified_count": scan_res.get("unidentified_count", 0),
                "scan_errors": _scan_errs[:20],
                "last_error": "；".join(_scan_errs[:3]),
            }

            # 🛡️ 分键保存，彻底杜绝单值超过 256 KiB 限制
            _save_state_split(ctx, new_state, share_links=share_links, local_links=local_links, mixed_links=mixed_links, full_groups=evaluated)

            ctx.mt.log(level="info", message=f"扫描完成: 总目录 {new_state['total_dirs_scanned']}，分享链接 {len(share_links)} 项，本地链接 {len(local_links)} 项，待评估/处理 {len(mixed_links)} 组混合链")
            return new_state
        except Exception as e:
            logger.error("扫描过程发生异常: %s", e)
            current_state["is_scanning"] = False
            current_state["status"] = "error"
            current_state["last_error"] = str(e)
            ctx.mt.log(level="error", message=f"扫描失败：{e}")
            _save_state_split(ctx, current_state)
            raise

    @action("preview-mixed")
    def preview_mixed(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_service import preview
        groups = [g for g in _get_full_groups(ctx)
                  if str(g.get("tmdb_id")) == str(input["tmdbId"])
                  and g.get("media_type") == input["mediaType"]]
        if len(groups) != 1:
            raise ValueError("扫描快照中没有唯一匹配作品，请重新扫描")
        return preview(ctx, groups[0], input["ruleId"])

    @action("cleanup-mixed")
    def cleanup_mixed(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        key = str(input["mediaType"]) + ":" + str(input["tmdbId"])
        return self._cleanup_mixed_groups([key], ctx)

    @action("batch-cleanup-mixed")
    def batch_cleanup_mixed(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        return self._cleanup_mixed_groups(input.get("selectedGroups"), ctx)

    def _cleanup_mixed_groups(self, selected: Any, ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_service import configured_rule
        from series_core.mixed_pipeline import start as cleanup
        if not isinstance(selected, list) or not selected or len(selected) > 50:
            raise ValueError("请勾选1至50个混合组；文件总数还须满足本轮任务预算")
        if any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected):
            raise ValueError("混合组选择无效或重复")
        groups = []
        snapshot = _get_full_groups(ctx)
        protected = _get_protected_no_year(ctx)
        for key in selected:
            matches = [g for g in snapshot if g.get("is_mixed") and
                       str(g.get("media_type")) + ":" + str(g.get("tmdb_id")) == key]
            if len(matches) != 1:
                raise ValueError("所选混合组已变化，请重新扫描并勾选")
            if key in protected:
                raise ValueError("作品处于保护组，不能清理")
            groups.append(matches[0])
        cfg = _get_config(ctx)
        rule = configured_rule(ctx.mt, cfg)
        bindings = _known_roots_from_config(ctx)
        return cleanup(ctx, groups, rule, cfg,
                       [{"id": rid, "path": path} for rid, path in bindings.items()])

    @continuation("collect-mixed")
    def collect_mixed(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        # Preserve continuations already queued by pre-pipeline releases.
        if input["key"].startswith("mixed-runs/"):
            from series_core.mixed_flow import collect as legacy_collect
            return legacy_collect(ctx, input["key"])
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-1")
    def collect_mixed_1(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-2")
    def collect_mixed_2(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-3")
    def collect_mixed_3(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-4")
    def collect_mixed_4(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-5")
    def collect_mixed_5(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-6")
    def collect_mixed_6(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-7")
    def collect_mixed_7(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-8")
    def collect_mixed_8(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-9")
    def collect_mixed_9(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-10")
    def collect_mixed_10(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-11")
    def collect_mixed_11(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-12")
    def collect_mixed_12(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-13")
    def collect_mixed_13(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-14")
    def collect_mixed_14(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-15")
    def collect_mixed_15(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-16")
    def collect_mixed_16(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-17")
    def collect_mixed_17(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-18")
    def collect_mixed_18(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @continuation("collect-mixed-19")
    def collect_mixed_19(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        return collect(ctx, input["key"])

    @action("test-mt-mapping")
    def test_mt_mapping(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mt_files import MTFiles
        cfg = _get_config(ctx)
        from mt_plugin.core import SDKError
        try:
            bindings = _known_roots_from_config(ctx, cfg)
            if not bindings:
                raise ValueError("映射目录缺少显式授权根绑定")
            client = MTFiles(ctx, [{"id": rid, "path": path} for rid, path in bindings.items()],
                             "mt-mapping-probe/" + ctx.flow_id)
            client.list_files(cfg.get("strmCloudPrefix", ""))
            result = {"ok": True, "message": "MT云盘映射可读取（未执行删除；删除授权仍由宿主执行时检查）"}
        except SDKError as exc:
            # Keep the original typed error for the host run/diagnostic response.
            result = {"ok": False, "message": "MT读取接口失败，详情见宿主运行错误；未执行删除",
                      "code": exc.code, "path": exc.path, "retryable": exc.retryable}
            write_value(ctx, "mt/mapping", result)
            raise
        except ValueError as exc:
            missing_binding = str(exc) == "映射目录缺少显式授权根绑定"
            result = {"ok": False, "message": ("未配置授权根绑定，请先保存绑定；未执行删除" if missing_binding
                      else "MT映射配置或读取响应无效，请核对配置；未执行删除"),
                      "code": "MAPPING_BINDING_REQUIRED" if missing_binding else "MAPPING_VALIDATION_FAILED"}
        write_value(ctx, "mt/mapping", result)
        return result

    @action("resume-mixed")
    def resume_mixed(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.mixed_pipeline import collect
        key = input["key"]
        if not isinstance(key, str) or not key.startswith("mixed-pipelines/"):
            raise ValueError("请指定混合流程记录键")
        return collect(ctx, key)

    def _start_organize(self, rows: List[Dict[str, Any]], ctx: Context, dry_run: bool = False) -> Dict[str, Any]:
        from series_core.organizer import plan, start
        _assert_unprotected_rows(ctx, rows)
        cfg = _get_config(ctx)
        bindings = _known_roots_from_config(ctx, cfg)
        roots = [{"id": rid, "path": path} for rid, path in bindings.items()]
        items = plan(ctx, rows, cfg, roots)
        return start(ctx, items, dry_run=dry_run)

    @action("submit-organize")
    def submit_organize(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        rows = []
        for group in _get_full_groups(ctx):
            if group.get("is_mixed") or group.get("group_link_type") == "mixed":
                continue
            for row in group.get("dirs", []):
                if row.get("need_organize") and not row.get("disabled"):
                    rows.append(dict(row, tmdb_id=group["tmdb_id"], media_type=group.get("media_type")))
        return self._start_organize(rows, ctx, dry_run=bool(_get_config(ctx).get("debugMode", True)))

    @action("submit-single")
    def submit_single(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        # Resolve identity and protection from the persisted scan, never trust UI
        # supplied tmdb/profile/link fields to turn a protected row into a job.
        path = input.get("localPath")
        rows = _get_state(ctx)
        matches = [row for name in ("share_links", "local_links")
                   for row in rows.get(name, []) if row.get("local_path") == path]
        if len(matches) != 1:
            raise ValueError("未找到唯一扫描条目，请重新扫描后选择")
        return self._start_organize(matches, ctx)

    @continuation("collect-organize")
    def collect_organize(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.organizer import collect
        return collect(ctx, input["key"])

    @action("delete-single")
    def delete_single(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        local_path = input.get("localPath")
        link_type = input.get("linkType", "http")
        if not local_path:
            return {"error": "缺少目标目录路径"}

        return self._start_delete([local_path], link_type, ctx)

    def _start_delete(self, paths: List[str], link_type: str, ctx: Context) -> Dict[str, Any]:
        if link_type not in ("http", "cloudnas"):
            raise ValueError("混合或未知链接类型禁止整目录删除")
        paths = list(dict.fromkeys(paths))
        if not paths:
            raise ValueError("请先选择待删除条目")
        state = _get_state(ctx)
        candidates = state.get("share_links" if link_type == "http" else "local_links", [])
        _assert_unprotected_rows(ctx, [row for row in candidates if row.get("local_path") in paths])
        for path in paths:
            matches = [row for row in candidates if row.get("local_path") == path]
            if (len(matches) != 1 or matches[0].get("disabled")
                    or matches[0].get("link_type") != link_type):
                raise ValueError("删除条目已失效、受保护或链接类型不符，请重新扫描")
        info = ctx.mt.runtime.info()
        limit = min(150, max(1, int((info.get("limits") or {}).get("maxWorkflowFiles", 100))))
        if len(paths) > limit:
            raise ValueError(f"本轮最多删除 {limit} 个目录，请分批选择")
        key = f"deletions/{ctx.flow_id}"
        existing = ctx.mt.storage.get(key=key)
        if existing.get("found"):
            raise ValueError("该流程已有删除记录，请先核对原任务，禁止重复提交")
        # 跨流程路径锁：未知结果不能通过重新点击启动新流程来重放。
        import hashlib
        lock_keys = ["deletion-lock/" + hashlib.sha256(
            (link_type + ":" + p.rstrip("/")).encode()).hexdigest() for p in paths]
        for lock_key in lock_keys:
            lock = ctx.mt.storage.get(key=lock_key)
            if lock.get("found"):
                raise ValueError("该目录已有删除记录，请先核对原任务，禁止跨流程重复删除")
        # 在所有 SDK 写入前建立持久记录；异常时保留 submitting，禁止盲目重试。
        record: Dict[str, Any] = {"status": "submitting", "link_type": link_type,
                                  "items": [{"path": p, "status": "pending"} for p in paths]}
        ctx.mt.storage.set(key=key, value=record)
        # CAS create prevents overwriting an existing lock; locks intentionally
        # survive completion until an explicit, reviewed recovery mechanism exists.
        for lock_key in lock_keys:
            ctx.mt.storage.set(key=lock_key, value={"recordKey": key}, expected_revision="")
        refs = []
        for item in record["items"]:
            item["status"] = "submitting"
            ctx.mt.storage.set(key=key, value=record)
            try:
                task = _execute_delete_entry(ctx, item["path"], link_type)
                item.update(status=task["status"], task_ref=task["ref"])
                refs.append(task["ref"])
            except Exception as exc:
                item.update(status="outcome_unknown", error=str(exc))
                # 不能确定是否发生了副作用，不继续提交后续条目。
                break
            ctx.mt.storage.set(key=key, value=record)
        record["status"] = "waiting" if refs else "outcome_unknown"
        ctx.mt.storage.set(key=key, value=record)
        if refs:
            ctx.mt.tasks.wait(refs=refs, completion={"action": "collect-delete", "input": {"key": key}})
            return {"status": "waiting", "taskRefs": refs}
        return {"status": "outcome_unknown", "recordKey": key}

    @continuation("collect-delete")
    def collect_delete(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        key = input["key"]
        record = _storage_get_safe(ctx, key)
        if not isinstance(record, dict):
            raise ValueError("缺少删除任务记录，不能确认成功")
        completed = 0
        for item in record["items"]:
            if not item.get("task_ref"):
                continue
            task = ctx.mt.tasks.get(ref=item["task_ref"])
            item["status"] = task["status"]
            if task["status"] == "done" and type(task.get("failed")) is int and task["failed"] == 0:
                _remove_entry_from_state(ctx, item["path"], record["link_type"])
                completed += 1
            elif task.get("error"):
                item["error"] = task["error"]
        record["status"] = "done" if completed == len(record["items"]) else "needs_review"
        ctx.mt.storage.set(key=key, value=record)
        return {"status": record["status"], "deleted": completed, "total": len(record["items"]), "recordKey": key}

    @action("batch-submit")
    def batch_submit(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        paths = input.get("selectedPaths") or []
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise ValueError("请先勾选作品")
        state = _get_state(ctx)
        rows = [row for name in ("share_links", "local_links") for row in state.get(name, [])
                if row.get("local_path") in paths]
        if len(rows) != len(set(paths)):
            raise ValueError("选择包含失效或重复条目，请重新扫描")
        return self._start_organize(rows, ctx)

    @action("batch-delete")
    def batch_delete(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        selected_paths = input.get("selectedPaths") or []
        if isinstance(selected_paths, str):
            selected_paths = [selected_paths]
        link_type = input.get("linkType", "http")

        if not selected_paths:
            return {"error": "未勾选任何条目，请先勾选需要删除的作品"}

        return self._start_delete(selected_paths, link_type, ctx)

    @action("refresh-tmdb")
    def refresh_tmdb(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        groups = _get_full_groups(ctx)
        if not groups:
            return {"count": 0}
        targets = list(dict.fromkeys((g.get("media_type", "tv"), int(g["tmdb_id"])) for g in groups))
        caches = query_mt_tmdb_caches(targets, ctx=ctx)
        resolved = sum((media_type, tid) in caches for media_type, tid in targets)
        return {"queried": len(targets), "resolved": resolved,
                "force_refresh": False, "rescan_required": True,
                "message": "已重新查询宿主 TMDB 元数据；SDK 不提供强制刷新，不代表刷新官网缓存"}

    @action("cleanup-snapshots")
    def cleanup_snapshots(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        from series_core.storage import collect_old_parts
        return collect_old_parts(ctx, STORAGE_KEY_SNAPSHOT, str(input.get("cursor") or ""))

    @action("clear")
    def clear(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        # Clear scan-derived no-year entries, never silently remove manual exclusions.
        prot_map = {k: v for k, v in _get_protected_no_year(ctx).items()
                    if v.get("auto_added") is not True}
        _save_protected_no_year(ctx, prot_map)
        empty_state = {
            "status": "idle",
            "is_scanning": False,
            "total_dirs_scanned": 0,
            "share_links_count": 0,
            "local_links_count": 0,
            "mixed_links_count": 0,
            "protected_links_count": len(prot_map),
            "active_tab": "tab-share",
            "share_links": [],
            "local_links": [],
            "mixed_links": [],
            "protected_links": list(prot_map.values()),
            "last_scan_time": "尚未扫描",
        }
        _save_state_split(ctx, empty_state, share_links=[], local_links=[], mixed_links=[], full_groups=[])
        return empty_state

    @action("protect-item")
    def protect_item(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """将选定作品手动收入无年份/免处理保护组。"""
        tid = str(input.get("tmdbId") or input.get("tmdb_id") or "")
        mtype = str(input.get("mediaType") or input.get("media_type") or "tv").lower()
        title = input.get("title") or input.get("dirName") or input.get("dir_name") or "未命名作品"
        dir_p = input.get("localPath") or input.get("local_path") or input.get("dir_path") or ""
        dir_n = input.get("dirName") or input.get("dir_name") or os.path.basename(dir_p.rstrip("/"))
        ltype = input.get("linkType") or input.get("link_type") or "other"

        gkey = f"{mtype}:{tid}" if tid and tid != "未标注" else dir_p
        prot_map = _get_protected_no_year(ctx)

        prot_map[gkey] = {
            "key": gkey,
            "tmdb_id": tid,
            "media_type": mtype,
            "media_type_desc": "🎬 电影" if mtype == "movie" else "📺 剧集",
            "title": title,
            "dir_name": dir_n,
            "local_path": dir_p,
            "link_type": ltype,
            "reason": "手动收入保护组 (免重复处理)",
            "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "auto_added": False,
        }
        _save_protected_no_year(ctx, prot_map)

        # 同步从当前 state 的 share_links / local_links 中剔除
        st = _get_state(ctx)
        st["share_links"] = [it for it in st.get("share_links", []) if it.get("local_path") != dir_p and (not tid or it.get("tmdb_id") != tid)]
        st["local_links"] = [it for it in st.get("local_links", []) if it.get("local_path") != dir_p and (not tid or it.get("tmdb_id") != tid)]
        st["share_links_count"] = len(st["share_links"])
        st["local_links_count"] = len(st["local_links"])
        st["protected_links"] = list(prot_map.values())
        st["protected_links_count"] = len(st["protected_links"])
        _save_state_split(ctx, st, share_links=st.get("share_links"), local_links=st.get("local_links"))

        ctx.mt.log(level="info", message=f"🛡️ 已将《{title}》收入保护组，后续批量处理将自动跳过")
        return {"waiting": False, "ok": True, "message": f"已将《{title}》收入保护组"}

    @action("unprotect-item")
    def unprotect_item(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """将选定作品从保护组移出。"""
        gkey = input.get("key") or input.get("localPath") or input.get("local_path") or ""
        prot_map = _get_protected_no_year(ctx)
        removed_title = "该条目"
        if gkey in prot_map:
            removed_title = prot_map[gkey].get("title") or removed_title
            del prot_map[gkey]
            _save_protected_no_year(ctx, prot_map)
        else:
            for k, it in list(prot_map.items()):
                if it.get("local_path") == gkey or it.get("key") == gkey:
                    removed_title = it.get("title") or removed_title
                    del prot_map[k]
                    _save_protected_no_year(ctx, prot_map)
                    break

        st = _get_state(ctx)
        st["protected_links"] = list(prot_map.values())
        st["protected_links_count"] = len(st["protected_links"])
        _save_state_split(ctx, st, share_links=st.get("share_links"), local_links=st.get("local_links"))

        ctx.mt.log(level="info", message=f"🔓 已将《{removed_title}》移出保护组，下次扫描时将重新参与重整")
        return {"waiting": False, "ok": True, "message": f"已移出保护组"}

    @action("check-protected-tmdb")
    def check_protected_tmdb(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """批量向 TMDB 查验保护组条目的年份信息，有年份的自动释放。"""
        prot_map = _get_protected_no_year(ctx)
        if not prot_map:
            return {"waiting": False, "ok": True, "message": "保护组目前为空，无需查验"}

        targets = []
        single_key = input.get("key")
        for k, it in prot_map.items():
            if single_key and k != single_key:
                continue
            tid = it.get("tmdb_id")
            mtype = it.get("media_type") or "tv"
            if tid and str(tid).isdigit():
                targets.append((mtype, int(tid)))

        if not targets:
            return {"waiting": False, "ok": True, "message": "所选条目暂无有效 TMDB ID，无法在线查验"}

        caches = refresh_tmdb_batch(targets, ctx=ctx)
        released = []
        for k, it in list(prot_map.items()):
            if single_key and k != single_key:
                continue
            tid = it.get("tmdb_id")
            mtype = it.get("media_type") or "tv"
            if tid and str(tid).isdigit():
                tinfo = caches.get((mtype, int(tid))) or caches.get(int(tid))
                if tinfo:
                    y = tinfo.get("year")
                    if y and isinstance(y, int) and y > 1900:
                        released.append(f"《{it.get('title')}》({y})")
                        del prot_map[k]

        _save_protected_no_year(ctx, prot_map)
        st = _get_state(ctx)
        st["protected_links"] = list(prot_map.values())
        st["protected_links_count"] = len(st["protected_links"])
        _save_state_split(ctx, st, share_links=st.get("share_links"), local_links=st.get("local_links"))

        if released:
            msg = f"🎉 查验成功！TMDB 已补全年份，成功释放 {len(released)} 部作品: {', '.join(released)}。重新点击【开始查重】即可在待重整列表中看到它们！"
            ctx.mt.log(level="info", message=msg)
            return {"waiting": False, "ok": True, "message": msg}
        else:
            msg = "查验完成: TMDB 官方目前暂未补全这些作品的上映年份，继续留在保护组锁定。"
            return {"waiting": False, "ok": True, "message": msg}
