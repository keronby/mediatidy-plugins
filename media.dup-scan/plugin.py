# -*- coding: utf-8 -*-
"""media.dup-scan — STRM 重复查重（MT Python SDK v2，1.3.0）

重构要点（对齐官方 Wiki SDK v2）：
- 扫描默认走**本地快路径**（学 media.series-dedup）：宿主授权根为
  local 且根名为 strm 时，直接在容器内 /strm（或宿主 /vol1/1000/strm）
  用 os.walk 遍历，绕过宿主 files.scan/tasks.items 的逐文件审计/RPC，
  50w+ 库只需秒级；云盘根（storageType=cloud）自动回退到
  ctx.mt.files.scan（异步任务）+ tasks.wait + @continuation 分页
  tasks.items。读 STRM 首行本地直接 open、云端走 files.readText；
  删除仍走宿主 files.batch/files.delete（本地条目无 ref，按
  destination{rootId,path} 定位，附带文件先 files.stat 定位）。
  依据：官方 Wiki permissions.html/capabilities.html 明确受信任插件
  的 Python 原生行为不受 SDK 白名单/进程隔离限制。
- 目录用页面 DirectoryPicker 选择 {rootId, path}（path 相对 root，
  root 为 "."），不再硬编码 NAS 根路径。
- 结果按官方 storage-table 模式存储：scans/<runId>/summary +
  scans/<runId>/groups/<seq:%04d>，currentScan 指针；查询用
  storage.list(prefix,cursor,limit) 分页（limit 20），不依赖 offset。
- 配置只用宿主 config（configSchema）+ storage 镜像（白名单校验，
  found=false 时视为缺失，绝不把 envelope 当值）；删除插件目录下的
  本地 JSON 文件 文件持久化。
- 页面 get_page 只做静态组装：不调用 rules.list、不扫盘；绑定一律
  state.*；Table 用 field 列定义。
- validate_config 为纯函数（不调 SDK），仅校验字段类型/范围。
"""
import os
import uuid
from typing import Any

from mt_plugin import Plugin as PluginBase, action, continuation, query, ui

from core.nameinfo import extract_real_ext, parse as parse_strm_name
from core.rules import DEFAULT_FALLBACK_RULES, get_rule_by_id, load_all_rules, to_scorer_rules
from core.workscan import parse_work_dir, scan_work, summarize

MANIFEST_ID = "media.dup-scan"
PLUGIN_VERSION = "1.3.0"

KV_CONFIG = "config"
KV_CURRENT_SCAN = "currentScan"
KV_TOOL_SCAN = "toolScan"

SCAN_LIMIT = 10000          # runtime.maxScanFiles 上限
PAGE_SIZE = 100             # tasks.items 每页
LIST_LIMIT = 20             # storage.list 单页（官方示例）
MAX_GROUPS_STORED = 2000
MAX_TOOL_ROWS = 2000
DELETE_BATCH_LIMIT = 500    # files.batch 上限
COMPANION_EXTS = (".nfo", "-mediainfo.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "ruleId": "auto",
    "readStrmContent": True,
    "scanSource": None,   # {rootId, path}，上次选择的查重扫描目录
    "toolSource": None,   # {rootId, path}，上次选择的工具扫描目录
    # 本地/网盘地址映射（series-dedup core/matcher.py:53-55 同款语义，无预设值，用户自填）
    # 例：localDestDir=/strm/云盘影视  cloudDestDir=/小小玻珠/云盘影视
    "localDestDir": "",
    "cloudDestDir": "",
}
CONFIG_KEYS = tuple(DEFAULT_CONFIG.keys())


# ---------------------------------------------------------------------------
# 工具：storage 信封、安全读/写、idempotency
# ---------------------------------------------------------------------------
def _new_idempotency_key(ctx: Any, purpose: str) -> str:
    try:
        return f"{ctx.run_id}:{purpose}"
    except Exception:
        return f"{purpose}:{uuid.uuid4().hex[:16]}"


def _storage_get(ctx: Any, key: str, default: Any = None) -> Any:
    """读 storage：忽略 found=false 的 envelope；found 区分“缺失”与 null。"""
    try:
        data = ctx.mt.storage.get(key=key)
    except Exception:
        return default
    if not isinstance(data, dict):
        return data if data is not None else default
    if "found" in data:
        if not data.get("found"):
            return default
        value = data.get("value")
        return default if value is None else value
    value = data.get("value", data)
    return default if value is None else value


def _storage_set(ctx: Any, key: str, value: Any) -> bool:
    try:
        ctx.mt.storage.set(key=key, value=value)
        return True
    except Exception:
        return False


def _storage_list(ctx: Any, prefix: str, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
    """按 prefix,cursor,limit 分页拉全（官方 storage.list 不支持 offset）。"""
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(200):  # 最多 200 页 * 20 = 4000 条，防失控
        try:
            data = ctx.mt.storage.list(prefix=prefix, cursor=cursor, limit=limit, values=True)
        except Exception:
            break
        items = data.get("items", []) if isinstance(data, dict) else []
        out.extend(items)
        cursor = data.get("nextCursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return out


def _cfg(ctx: Any) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    saved = _storage_get(ctx, KV_CONFIG, default=None)
    if isinstance(saved, dict):
        for key in CONFIG_KEYS:
            if key in saved:
                cfg[key] = saved[key]
    try:
        host = ctx.config
    except Exception:
        host = None
    if host is not None:
        for key in CONFIG_KEYS:
            try:
                value = getattr(host, key, None)
            except Exception:
                value = None
            if value is not None:
                cfg[key] = value
    cfg["ruleId"] = str(cfg.get("ruleId") or "auto")
    cfg["readStrmContent"] = bool(cfg.get("readStrmContent", True))
    # scanSource/toolSource 归一化（非法则丢弃，回落 None）
    cfg["scanSource"] = _clean_source(cfg.get("scanSource")) if cfg.get("scanSource") else None
    cfg["toolSource"] = _clean_source(cfg.get("toolSource")) if cfg.get("toolSource") else None
    return cfg

def _persist_sources(ctx: Any, cfg: dict[str, Any], source: Any = None, tool_source: Any = None) -> None:
    """扫描/工具扫描时把本次选择的目录+规则回写 KV_CONFIG，下次打开页面自动回填。"""
    changed = False
    clean = _clean_source(source) if source else None
    if clean is not None and clean != cfg.get("scanSource"):
        cfg["scanSource"] = clean; changed = True
    tclean = _clean_source(tool_source) if tool_source else None
    if tclean is not None and tclean != cfg.get("toolSource"):
        cfg["toolSource"] = tclean; changed = True
    if changed:
        _storage_set(ctx, KV_CONFIG, {k: cfg.get(k) for k in CONFIG_KEYS})


def _cfg_view(ctx: Any) -> dict[str, Any]:
    return {key: _cfg(ctx).get(key, DEFAULT_CONFIG[key]) for key in CONFIG_KEYS}


def _clean_source(value: Any) -> dict[str, Any] | None:
    """归一化 DirectoryPicker 结果 {rootId, path}；非法返回 None。"""
    if not isinstance(value, dict):
        return None
    root_id = str(value.get("rootId") or value.get("root_id") or "").strip()
    path = str(value.get("path") or ".").strip() or "."
    if path.startswith("/") or ".." in path.split("/"):
        return None
    if not root_id:
        return None
    return {"rootId": root_id, "path": path}


def _work_dir_of(file_path: str) -> str:
    """向上找含 [tmdbid-…] 的作品目录；file_path 为 root 相对路径。"""
    parts = [p for p in str(file_path).split("/") if p not in ("", ".")]
    for i in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:i])
        if parse_work_dir(candidate.rsplit("/", 1)[-1]):
            return candidate
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


def _compact_file(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "rel": item.get("rel", ""),
        "name": item.get("name", ""),
        "score": item.get("score", 0),
        "why": item.get("why", ""),
        "slot": item.get("slot", ""),
        "ref": item.get("ref", ""),
        "version": item.get("version", ""),
        "rootId": item.get("rootId", ""),
    }


def _keep_drop_names(value: Any, files: list[dict[str, Any]], which: str) -> list[str]:
    """keep/drop 在 scan_work 输出里是单个 file dict（或 None），不是列表。
    统一归一为名字段列表，供 summary 展示 / batch_wash 匹配 / storage 存取。"""
    if isinstance(value, list) and value and isinstance(value[0], str):
        return list(value)
    if isinstance(value, dict) and value:
        name = value.get("name") or value.get("rel") or ""
        return [str(name)] if name else []
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [str(f.get("name") or f.get("rel") or "") for f in value if f.get("name") or f.get("rel")]
    # dict/None 兜底：从 files 首个推断（极少数旧存量）
    if isinstance(value, list):
        return list(value)
    return []


def _compact_group(group: dict[str, Any]) -> dict[str, Any]:
    files = [_compact_file(f) for f in group.get("files", [])]
    return {
        "workDir": group.get("workDir", ""),
        "title": group.get("title", ""),
        "year": group.get("year", ""),
        "kind": group.get("kind", ""),
        "slot": group.get("slot", ""),
        "label": group.get("label", ""),
        "season": group.get("season", ""),
        "episode": group.get("episode", ""),
        "verdict": group.get("verdict", ""),
        "minDiff": group.get("minDiff", group.get("min_diff", 0)),
        "diff": group.get("diff", 0),
        "keep": _keep_drop_names(group.get("keep"), files, "keep"),
        "drop": _keep_drop_names(group.get("drop"), files, "drop"),
        "files": files,
    }


def _list_roots(ctx: Any) -> list[dict[str, Any]]:
    """已授权文件根（files.roots，无 path 字段）；失败返回空列表。"""
    try:
        got = ctx.mt.files.roots()
    except Exception:
        return []
    rows = got.get("items") or [] if isinstance(got, dict) else (got or [])
    return [r for r in rows if isinstance(r, dict)]


def _resolve_local_base(ctx: Any, source: dict[str, Any]) -> str | None:
    """判定 source 是否可走本地快路径；可则返回容器内绝对基目录，不可返回 None。

    严格条件（防把云盘根误当本地）：
      1. files.roots 中该 rootId 的 storageType == "local"；
      2. 根名 == "strm"（deploy grants 授权的 root-strm，path=/strm，
         与 media.series-dedup 的 /strm 挂载点一致）；
      3. 候选目录真实存在：优先 /strm/<path>，其次 /vol1/1000/strm/<path>。
    任一不满足（含 files.roots 调用失败）返回 None，调用方回退 files.scan。
    """
    roots = _list_roots(ctx)
    root = next((r for r in roots if str(r.get("id") or "") == source["rootId"]), None)
    if root is None or root.get("storageType") != "local":
        return None
    if str(root.get("name") or "").strip("/") != "strm":
        return None
    rel = "" if source["path"] in (".", "") else source["path"]
    for prefix in ("/strm", "/vol1/1000/strm"):
        cand = os.path.join(prefix, rel) if rel else prefix
        if os.path.isdir(cand):
            return cand
    return None


def _local_scan_iter(ctx: Any, source: dict[str, Any], base: str):
    """本地流式遍历 .strm（生成器，防 50w+ 全量 entry 撑爆 worker 内存）。

    每 yield 一个与 files.scan 同形 entry；depth 剪枝：跳过已知的非媒体目录
    （.git/.venv/node_modules/__pycache__/mt_plugin 等）避免无谓遍历。
    """
    skip = {".git", ".venv", "node_modules", "__pycache__", ".trash", "tmp"}
    count = 0
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            abs_path = os.path.join(dirpath, name)
            rel_to_base = os.path.relpath(abs_path, base)
            rel = rel_to_base if not (source["path"] not in (".", "")) else os.path.join(source["path"], rel_to_base)
            count += 1
            if count % 20000 == 1:
                _progress(ctx, count, f"本地扫描 {count}")
            yield {
                "rel": rel.replace(os.sep, "/"), "name": name,
                "ref": "", "version": "", "rootId": source["rootId"],
                "_localPath": abs_path,
            }


def _local_scan_strm(ctx: Any, source: dict[str, Any], base: str) -> list[dict[str, Any]]:
    """兼容旧调用：全量本地遍历（仅小目录/测试用，大库用 _local_scan_iter 流式）。"""
    return list(_local_scan_iter(ctx, source, base))


def _read_first_line(ctx: Any, entry: dict[str, Any]) -> str:
    """读 STRM 首行：本地条目直接 open，云端条目走 files.readText。"""
    local_path = entry.get("_localPath")
    if local_path:
        try:
            with open(local_path, "r", encoding="utf-8", errors="ignore") as fh:
                return (fh.readline() or "").strip()
        except Exception:
            return ""
    ref = entry.get("ref")
    if not ref:
        return ""
    try:
        data = ctx.mt.files.read_text(ref=ref)
    except Exception:
        return ""
    text = data.get("text", "") if isinstance(data, dict) else ""
    return text.splitlines()[0].strip() if text else ""


_AUTO_RULE_PRIORITY = ("quality-strm", "quality-strm-tv", "quality-tv-zh", "quality-movie", "quality-movie-zh")


def _pick_auto_rule(all_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """auto 默认：优先本地strm（仅 ISO 槽隔离、minDiff 0，见 docs/strm查重流程.md）。

    宿主 rules.list 首条是 built-in-default（无 slots）或 quality-movie
    （normal 槽按分辨率拆同集），都会漏检/错分组，故显式优先级而非取首条。
    """
    for rid in _AUTO_RULE_PRIORITY:
        for r in all_rules:
            if isinstance(r, dict) and str(r.get("id")) == rid:
                return r
    return all_rules[0] if all_rules else {}


def _resolve_rules(ctx: Any, cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """返回 (候选规则列表, 选中的单条规则 dict)。scan_work 只吃单条 dict。"""
    all_rules = load_all_rules(ctx)
    if not all_rules:
        all_rules = list(DEFAULT_FALLBACK_RULES)
    rule_id = str(cfg.get("ruleId") or "auto")
    if rule_id and rule_id != "auto":
        # get_rule_by_id 官方签名 (rule_id, ctx=None)，无第三个参数
        chosen = get_rule_by_id(rule_id, ctx)
        if isinstance(chosen, dict):
            return [chosen], chosen
        for r in all_rules:
            if isinstance(r, dict) and str(r.get("id")) == rule_id:
                return [r], r
    return list(all_rules), _pick_auto_rule(list(all_rules))


def _evaluate_works(
    ctx: Any,
    works: dict[str, list[dict[str, Any]]],
    rules: Any,
    read_content: bool,
) -> list[dict[str, Any]]:
    """对每个作品目录用 core.workscan.scan_work 评分比对。

    快速原则（先文件名、后读内容）：
      1. 纯本地按文件名预分组：剧集按 (季,集) S01E01、电影按同作品目录，
         连集包/无集号文件不参与（与 scan_work 内部规则一致）。
      2. 预分组后每桶 <2 的作品/集直接跳过，绝不 readText——这是 2003 个
         文件 80 秒的主因（逐文件串行 RPC），现只读真正有重复候选的条目。
      3. 只有候选条目才在 readStrmContent 开启时 readText 读首行提取真实
         扩展名做 ISO 槽隔离；槽位（ISO/DV/HDR…）与总分比较仍由
         scan_work + 规则 scoring 完成，行为与之前一致。
    """
    # rules 可能是列表（_resolve_rules 旧返回）或单 dict：只取第一条 dict
    if isinstance(rules, list):
        rules = next((r for r in rules if isinstance(r, dict)), None)
    scorer_rules = (to_scorer_rules(rules) if isinstance(rules, dict) and rules else to_scorer_rules(_pick_auto_rule(list(DEFAULT_FALLBACK_RULES))))[0]  # to_scorer_rules returns (rules, unsupported, disabled); scan_work 只吃 rules dict
    # 先纯本地预筛：只保留会出现重复组的条目（等价于 scan_work 的分组键去掉槽位）
    candidates: dict[str, list[dict[str, Any]]] = {}
    for work_dir in sorted(works):
        entries = works[work_dir]
        if len(entries) < 2:
            continue
        is_tv = any(parse_strm_name(e["name"]).get("episode") is not None for e in entries)
        buckets: dict[Any, list[dict[str, Any]]] = {}
        for e in entries:
            info = parse_strm_name(e["name"])
            if is_tv:
                if info.get("episode") is None or info.get("isMulti"):
                    continue
                key = (info.get("season"), info.get("episode"))
            else:
                key = ("movie",)
            buckets.setdefault(key, []).append(e)
        keep_entries = [e for bucket in buckets.values() if len(bucket) >= 2 for e in bucket]
        if keep_entries:
            candidates[work_dir] = keep_entries
    groups: list[dict[str, Any]] = []
    total = len(candidates)
    for done, work_dir in enumerate(sorted(candidates), 1):
        entries = candidates[work_dir]
        if read_content:
            for entry in entries:
                text = _read_first_line(ctx, entry)
                entry["realExt"] = extract_real_ext(text) if text else ""
        groups.extend(scan_work(work_dir, entries, scorer_rules))
        _progress(ctx, done, f"查重比对 {done}/{total}", total)
    return groups


def _store_groups(ctx: Any, run_id: str, groups: list[dict[str, Any]]) -> int:
    stored = 0
    for seq, group in enumerate(groups[:MAX_GROUPS_STORED]):
        if _storage_set(ctx, f"scans/{run_id}/groups/{seq:04d}", _compact_group(group)):
            stored += 1
    return stored


def _delete_via_files_batch(
    ctx: Any, candidates: list[dict[str, Any]], purpose: str
) -> dict[str, int]:
    """用 files.batch delete 批量删除（fileRef 优先，destination 兜底）。

    附带文件（.nfo / -mediainfo.json）先 files.stat 定位再列入同一批。
    """
    items: list[dict[str, Any]] = []
    deleted = 0
    skipped = 0
    for cand in candidates[:DELETE_BATCH_LIMIT]:
        ref = cand.get("ref") or ""
        root_id = cand.get("rootId") or ""
        rel = cand.get("rel") or ""
        if ref:
            items.append({"operation": "delete", "fileRef": ref})
        elif root_id and rel:
            items.append({
                "operation": "delete",
                "destination": {"rootId": root_id, "path": rel},
                "recursive": False,
            })
        else:
            skipped += 1
            continue
        for suffix in COMPANION_EXTS:
            companion = rel + suffix if rel else ""
            if not (root_id and companion):
                continue
            try:
                stat = ctx.mt.files.stat(root_id=root_id, path=companion)
            except Exception:
                continue
            if isinstance(stat, dict) and stat.get("ref") and not stat.get("isDir"):
                items.append({"operation": "delete", "fileRef": stat["ref"]})
    if items:
        try:
            result = ctx.mt.files.batch(
                idempotency_key=_new_idempotency_key(ctx, purpose),
                items=items[:DELETE_BATCH_LIMIT],
            )
            deleted = int(result.get("succeeded", len(items))) if isinstance(result, dict) else len(items)
        except Exception as exc:
            _log(ctx, "error", f"files.batch delete failed: {exc}")
            skipped += len(items)
    return {"deleted": deleted, "skipped": skipped, "requested": len(candidates)}



def _classify_strm_url(text: str) -> str:
    """按 STRM 首行内容分组：http(s) 开头为分享（网盘），其余为本地。"""
    t = (text or "").strip()
    return "http" if t.lower().startswith(("http://", "https://")) else "local"


def _same_stem_media_candidates(local_path: str, exts=("mkv", "mp4", "ts", "m2ts", "avi", "iso", "bdmv", "rmvb", "wmv", "flv", "mov")):
    """本地删除：去除 .strm 后缀，同名媒体文件候选（绝对路径列表）。"""
    if not local_path.lower().endswith(".strm"):
        return []
    stem = local_path[:-5]
    return [stem + "." + e for e in exts]


def _map_local_to_cloud(local_strm_abs: str, local_dest: str, cloud_dest: str) -> str | None:
    """series-dedup 地址映射（core/submitter.py:to_mt_path 本地分支 + matcher local/cloud）：
    把 /strm/云盘影视/... 的本地绝对路径换成网盘根下的同相对路径。
    local_dest/cloud_dest 形如 /strm/云盘影视、/小小玻珠/云盘影视（无预设，用户自填）。
    容器内 /strm 与宿主 /vol1/1000/strm 视为同一前缀。返回映射后的 MT 路径（以 / 开头），
    无法映射返回 None。"""
    if not local_dest or not cloud_dest or not local_strm_abs:
        return None
    # 归一本地前缀：/strm 与 /vol1/1000/strm 等价
    cand = local_strm_abs
    for pre in ("/vol1/1000/strm", "/strm"):
        if cand.startswith(pre):
            cand = "/strm" + cand[len(pre):]
            break
    ld = local_dest.rstrip("/")
    if ld.startswith("/vol1/1000/strm"):
        ld = "/strm" + ld[len("/vol1/1000/strm"):]
    if not cand.startswith(ld):
        return None
    sub = cand[len(ld):]
    if not sub.startswith("/"):
        sub = "/" + sub
    return cloud_dest.rstrip("/") + sub


def _delete_by_content(
    ctx: Any, candidates: list[dict[str, Any]], purpose: str,
    local_dest: str = "", cloud_dest: str = "",
) -> dict[str, int]:
    """按 STRM 内容分组删除（用户 m01409 需求 2）：
      - http(s) 分享：删 .strm 本体 + 同名 .nfo + 同名 -mediainfo.json；
      - 本地：用地址映射把网盘路径换成本地同名媒体文件后删除该媒体文件
        （series-dedup matcher/submitter 映射语义），删不到媒体文件时回退删 .strm 本体。
    本地条目（_localPath）直接 os.remove；云端条目走 files.batch destination。
    """
    deleted = 0
    skipped = 0
    # 先按分类收集 files.batch items（云端）与本地 os.remove 列表
    batch_items: list[dict[str, Any]] = []
    local_removes: list[str] = []
    for cand in candidates[:DELETE_BATCH_LIMIT]:
        local_path = cand.get("_localPath") or ""
        # 读内容分类
        text = ""
        if local_path:
            try:
                with open(local_path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = (fh.readline() or "").strip()
            except Exception:
                text = ""
        else:
            text = _read_first_line(ctx, cand)
        kind = _classify_strm_url(text)
        root_id = cand.get("rootId") or ""
        rel = cand.get("rel") or ""
        ref = cand.get("ref") or ""
        if kind == "http":
            # 删 strm + .nfo + -mediainfo.json（同 stem 去 .strm 后缀再加）
            targets = [rel] if rel else []
            if rel:
                stem = rel[:-5] if rel.lower().endswith(".strm") else rel
                targets += [stem + ".nfo", stem + "-mediainfo.json"]
            for t in targets:
                if local_path and t == rel:
                    local_removes.append(local_path)
                elif local_path:
                    # 本地附带文件：与 strm 同目录同 stem
                    stem_lp = local_path[:-5] if local_path.lower().endswith(".strm") else local_path
                    suffix = t[len(rel):] if t.startswith(rel) else (".nfo" if t.endswith(".nfo") else "-mediainfo.json")
                    # t 形如 stem_rel+.nfo / stem_rel+-mediainfo.json，suffix 即 .nfo / -mediainfo.json
                    if t.endswith(".nfo"):
                        suffix = ".nfo"
                    elif t.endswith("-mediainfo.json"):
                        suffix = "-mediainfo.json"
                    local_removes.append(stem_lp + suffix)
                elif ref and t == rel:
                    batch_items.append({"operation": "delete", "fileRef": ref})
                elif root_id and t:
                    batch_items.append({"operation": "delete", "destination": {"rootId": root_id, "path": t}, "recursive": False})
                else:
                    skipped += 1
        else:
            # 本地：映射后删同名媒体文件；映射失败或媒体不存在回退删 strm 本体
            mapped = None
            media_abs = None
            if local_path and local_dest and cloud_dest:
                # local_path 形如 /vol1/1000/strm/云盘影视/.../x.strm -> 映射到 cloud 再反查本地媒体?
                # 实际：本地 strm 内容是本地路径时，直接同目录找同名媒体；映射用于跨根定位
                mapped = _map_local_to_cloud(local_path, local_dest, cloud_dest)
            # 本地同名媒体：strm 去后缀 + 媒体后缀，同目录
            cands = _same_stem_media_candidates(local_path) if local_path else []
            found = next((c for c in cands if os.path.exists(c)), None)
            if found:
                local_removes.append(found)
            elif mapped and local_dest:
                # 映射到网盘路径后，网盘根下同名媒体：转回本地盘符尝试
                # cloud_dest 形如 /小小玻珠/云盘影视，mapped 形如 /小小玻珠/云盘影视/.../x.strm
                # 本地对应：/vol1/1000/strm + mapped 去 cloud 前缀? 仅当 cloud_dest 与 local_dest 同子路径时可换算
                try:
                    sub = mapped[len(cloud_dest.rstrip("/")):]
                    alt_base = local_dest
                    if alt_base.startswith("/strm"):
                        alt_base = "/vol1/1000" + alt_base
                    alt_strm = alt_base + sub
                    for c in _same_stem_media_candidates(alt_strm):
                        if os.path.exists(c):
                            local_removes.append(c)
                            found = c
                            break
                except Exception:
                    pass
                if not found:
                    if local_path:
                        local_removes.append(local_path)
                    elif root_id and rel:
                        batch_items.append({"operation": "delete", "destination": {"rootId": root_id, "path": rel}, "recursive": False})
                    elif ref:
                        batch_items.append({"operation": "delete", "fileRef": ref})
                    else:
                        skipped += 1
            else:
                if local_path:
                    local_removes.append(local_path)
                elif root_id and rel:
                    batch_items.append({"operation": "delete", "destination": {"rootId": root_id, "path": rel}, "recursive": False})
                elif ref:
                    batch_items.append({"operation": "delete", "fileRef": ref})
                else:
                    skipped += 1
    # 本地删除
    for lp in local_removes:
        try:
            if os.path.exists(lp):
                os.remove(lp)
                deleted += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    # 云端批量删除
    if batch_items:
        try:
            result = ctx.mt.files.batch(idempotency_key=_new_idempotency_key(ctx, purpose), items=batch_items[:DELETE_BATCH_LIMIT])
            deleted += int(result.get("succeeded", len(batch_items))) if isinstance(result, dict) else len(batch_items)
        except Exception as exc:
            _log(ctx, "error", f"delete-by-content batch failed: {exc}")
            skipped += len(batch_items)
    return {"deleted": deleted, "skipped": skipped, "requested": len(candidates)}


def _log(ctx: Any, level: str, message: str) -> None:
    """统一日志：ctx.mt.log(level,message)，失败静默（禁止拖慢/崩溃主流程）。"""
    try:
        ctx.mt.log(level=level, message=str(message)[:500])
    except Exception:
        pass


def _progress(ctx: Any, completed: int, message: str, total: int | None = None) -> None:
    """runtime.progress 上报（action 通道，5000 次配额）；失败静默，禁止拖慢主流程。"""
    try:
        kwargs: dict[str, Any] = {"completed": int(completed), "message": str(message)[:200]}
        if total is not None:
            kwargs["total"] = int(total)
        ctx.mt.runtime.progress(**kwargs)
    except Exception:
        pass


def _collect_task_items(ctx: Any, ref: str, total_hint: int | None = None) -> list[dict[str, Any]]:
    """分页拉取异步任务的全部文件条目（单页官方上限 100，不可调大）。"""
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(500):
        try:
            data = ctx.mt.tasks.items(ref=ref, offset=offset, limit=PAGE_SIZE)
        except Exception:
            break
        items = data.get("items", []) if isinstance(data, dict) else []
        out.extend(items)
        _progress(ctx, len(out), "收集扫描结果", total_hint)
        next_offset = data.get("nextOffset") if isinstance(data, dict) else None
        if next_offset is None or next_offset <= offset:
            break
        offset = int(next_offset)
    return out


# ---------------------------------------------------------------------------
# 主插件
# ---------------------------------------------------------------------------
class Plugin(PluginBase):
    def validate_config(self, config: dict[str, Any], ctx: Any = None) -> dict[str, Any] | None:
        """纯校验：不调用任何 SDK（官方 validate_config 无 SDK 权限）。"""
        errors: dict[str, str] = {}
        if config is None:
            return None
        rule_id = config.get("ruleId")
        if rule_id is not None and not isinstance(rule_id, str):
            errors["ruleId"] = "规则 ID 必须是字符串"
        for key in ("readStrmContent",):
            value = config.get(key)
            if value is not None and not isinstance(value, bool):
                errors[key] = "必须是布尔值"
        for key in ("localDestDir", "cloudDestDir"):
            value = config.get(key)
            if value is not None and not isinstance(value, str):
                errors[key] = "必须是字符串"
        for key in ("scanSource", "toolSource"):
            value = config.get(key)
            if value is None:
                continue
            if not isinstance(value, dict):
                errors[key] = "必须是 {rootId, path} 对象"
            elif not str(value.get("rootId") or "").strip():
                errors[key] = "缺少 rootId"
            elif str(value.get("path") or ".").startswith("/") or ".." in str(value.get("path") or ".").split("/"):
                errors[key] = "path 须为相对 root 的路径"
        return {"fieldErrors": errors} if errors else None

    def on_init(self, ctx: Any) -> None:
        # 只补缺失的配置镜像；不覆盖已有值（官方 on_init 语义）。
        try:
            existing = _storage_get(ctx, KV_CONFIG, default=None)
            if not isinstance(existing, dict):
                _storage_set(ctx, KV_CONFIG, dict(DEFAULT_CONFIG))
        except Exception:
            pass

    def on_stop(self, ctx: Any) -> None:
        pass

    # -- 页面（静态：不调 rules.list、不扫盘；绑定 state.*）--------------
    # 风格对齐 media.series-dedup：Alert 提示 + Section 分块 + Card/Metric 统计 + Stack 横排按钮
    def get_page(self, ctx: Any) -> dict[str, Any]:
        cfg = _cfg_view(ctx)
        # 宿主 run.* 只读：动作排队/运行中时按钮 loading+禁用+改文案
        is_scan_running = ui.expr(
            "and",
            ui.expr("or", ui.expr("eq", ui.ref("run.action"), "dup-scan"),
                    ui.expr("eq", ui.ref("run.action"), "dup-scan-collect")),
            ui.expr("or", ui.expr("eq", ui.ref("run.status"), "queued"),
                    ui.expr("eq", ui.ref("run.status"), "running")),
        )
        is_tool_running = ui.expr(
            "and",
            ui.expr("or", ui.expr("eq", ui.ref("run.action"), "tool-scan-non-http"),
                    ui.expr("eq", ui.ref("run.action"), "tool-scan-collect")),
            ui.expr("or", ui.expr("eq", ui.ref("run.status"), "queued"),
                    ui.expr("eq", ui.ref("run.status"), "running")),
        )
        running = ui.expr("or", is_scan_running, is_tool_running)
        fallback_options = [
            {"label": "自动（首条启用评分规则）", "value": "auto"},
            {"label": "内置默认评分", "value": "built-in-default"},
            {"label": "电影-外语", "value": "quality-movie"},
            {"label": "电影-国语", "value": "quality-movie-zh"},
            {"label": "剧集-外语", "value": "quality-strm-tv"},
            {"label": "剧集-国语", "value": "quality-tv-zh"},
            {"label": "本地strm", "value": "quality-strm"},
        ]
        rule_options = ui.expr("if", ui.ref("query.rules.data.ruleOptions"), ui.ref("query.rules.data.ruleOptions"), fallback_options)

        dup_settings = ui.Section(
            ui.DirectoryPicker(label="扫描目录", bind="state.source"),
            ui.Select(label="查重规则", bind="state.ruleId", options=rule_options),
            ui.Checkbox(label="读取 STRM 首行做扩展名槽隔离", bind="state.readStrmContent"),
            ui.Input(label="本地目录（映射，如 /strm/云盘影视）", type="text", bind="state.localDestDir"),
            ui.Input(label="网盘目录（映射，如 /小小玻珠/云盘影视）", type="text", bind="state.cloudDestDir"),
            ui.Stack(
                ui.Button(
                    label=ui.expr("if", is_scan_running, "⏳ 正在查重扫描中...", "开始查重"),
                    variant="default",
                    disabled=is_scan_running,
                    loading=is_scan_running,
                    onClick=[
                        {"type": "invoke", "actionId": "dup-scan", "input": {"source": ui.ref("state.source"), "ruleId": ui.ref("state.ruleId"), "localDestDir": ui.ref("state.localDestDir"), "cloudDestDir": ui.ref("state.cloudDestDir")}},
                        {"type": "query", "queryId": "summary"},
                        {"type": "query", "queryId": "rules"},
                    ],
                ),
                ui.Button(
                    label="刷新页面",
                    variant="ghost",
                    onClick=[{"type": "query", "queryId": "summary"}, {"type": "query", "queryId": "rules"}],
                ),
                direction="row", gap="sm",
            ),
            title="扫描设置",
        )
        dup_overview = ui.Section(
            ui.Card(
                ui.Text(text=ui.ref("query.rules.data.currentRuleSummary")),
                ui.Text(text=ui.ref("query.rules.data.slotsStatusText"), tone="muted", size="sm"),
                ui.Text(text=ui.ref("query.summary.data.overviewLine")),
                title="统计指标",
            ),
            title="查重与比对概览",
        )
        dup_actions = ui.Section(
            ui.Alert(text="⏳ 正在后台扫描，按钮已锁定…", tone="warning", visible=running),
            ui.Stack(
                ui.Button(
                    label="批量洗白（删除低分重复）",
                    variant="outline",
                    onClick=[{"type": "invoke", "actionId": "batch-wash", "input": {"selections": ui.ref("state.selected_wash_ids")}}],
                ),
                ui.Button(
                    label="批量忽略（保留所选）",
                    variant="ghost",
                    onClick=[{"type": "invoke", "actionId": "batch-ignore", "input": {"selections": ui.ref("state.selected_wash_ids")}}],
                ),
                direction="row", gap="sm",
            ),
            title="操作控制台",
        )
        dup_table = ui.Section(
            ui.Table(
                source=ui.ref("query.summary.data.items"),
                columns=[
                    {"label": "作品", "field": "label", "cells": [ui.Text(text=ui.ref("row.label"))]},
                    {"label": "槽位", "field": "slot", "cells": [ui.Text(text=ui.ref("row.slot"))]},
                    {"label": "判定", "field": "verdict", "cells": [ui.Text(text=ui.ref("row.verdict"))]},
                    {"label": "保留", "field": "keep", "cells": [ui.Text(text=ui.ref("row.keep"))]},
                    {"label": "保留分", "field": "keepScore", "cells": [ui.Text(text=ui.ref("row.keepScore"))]},
                    {"label": "删除", "field": "drop", "cells": [ui.Text(text=ui.ref("row.drop"))]},
                    {"label": "删除分", "field": "dropScore", "cells": [ui.Text(text=ui.ref("row.dropScore"))]},
                    {"label": "分差", "field": "diff", "cells": [ui.Text(text=ui.ref("row.diff"))]},
                ],
                rowKey="key",
                selectionBind="state.selected_wash_ids",
            ),
            title="重复结果明细",
        )
        dup_tab = ui.Tab(dup_settings, dup_overview, dup_actions, dup_table, id="tab-dup", label="🎬 STRM 重复查重")

        tool_settings = ui.Section(
            ui.DirectoryPicker(label="工具扫描目录", bind="state.tool_source"),
            ui.Stack(
                ui.Button(
                    label=ui.expr("if", is_tool_running, "⏳ 正在扫描非 HTTP...", "扫描非 HTTP 链接"),
                    variant="default",
                    disabled=is_tool_running,
                    loading=is_tool_running,
                    onClick=[
                        {"type": "invoke", "actionId": "tool-scan-non-http", "input": {"source": ui.ref("state.tool_source")}},
                        {"type": "query", "queryId": "summary"},
                    ],
                ),
                ui.Button(
                    label="刷新页面",
                    variant="ghost",
                    onClick=[{"type": "query", "queryId": "summary"}],
                ),
                direction="row", gap="sm",
            ),
            title="扫描设置",
        )
        tool_overview = ui.Section(
            ui.Card(
                ui.Text(text=ui.ref("query.summary.data.toolSummary")),
                title="统计指标",
            ),
            title="异常概览",
        )
        tool_actions = ui.Section(
            ui.Alert(text="⏳ 正在后台扫描，按钮已锁定…", tone="warning", visible=running),
            ui.Stack(
                ui.Button(
                    label="删除所选异常文件",
                    variant="outline",
                    onClick=[{"type": "invoke", "actionId": "tool-batch-delete", "input": {"selections": ui.ref("state.selected_tool_ids")}}],
                ),
                ui.Button(
                    label="清空异常结果",
                    variant="ghost",
                    onClick=[{"type": "invoke", "actionId": "tool-clear", "input": {}}, {"type": "query", "queryId": "summary"}],
                ),
                direction="row", gap="sm",
            ),
            title="操作控制台",
        )
        tool_table = ui.Section(
            ui.Table(
                source=ui.ref("query.summary.data.toolItems"),
                columns=[
                    {"label": "序号", "field": "seq", "cells": [ui.Text(text=ui.ref("row.seq"))]},
                    {"label": "文件", "field": "name", "cells": [ui.Text(text=ui.ref("row.name"))]},
                    {"label": "相对路径", "field": "rel", "cells": [ui.Text(text=ui.ref("row.rel"))]},
                    {"label": "首行（异常）", "field": "url", "cells": [ui.Text(text=ui.ref("row.url"))]},
                    {"label": "异常类型", "field": "reason", "cells": [ui.Text(text=ui.ref("row.reason"))]},
                ],
                rowKey="key",
                selectionBind="state.selected_tool_ids",
            ),
            title="非 HTTP 异常明细",
        )
        tool_tab = ui.Tab(tool_settings, tool_overview, tool_actions, tool_table, id="tab-tool", label="🔧 扫描非 HTTP")

        return ui.page(
            ui.Tabs(dup_tab, tool_tab, bind="state.activeTab"),
            state={
                "activeTab": "tab-dup",
                "source": cfg.get("scanSource"),
                "tool_source": cfg.get("toolSource"),
                "selected_tool_ids": [],
                "ruleId": cfg.get("ruleId", "auto"),
                "readStrmContent": cfg.get("readStrmContent", True),
                "localDestDir": cfg.get("localDestDir", "") or "",
                "cloudDestDir": cfg.get("cloudDestDir", "") or "",
                "selected_wash_ids": [],
                "busy": False,
            },
            queries=[{"id": "rules", "name": "rules"}, {"id": "summary", "name": "summary"}],
        )


    # -- Queries ---------------------------------------------------------
    @query("rules")
    def rules(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        """只返回查重相关的评分规则（过滤命名规则），供 Select options 绑定。"""
        cfg = _cfg(ctx)
        all_rules = load_all_rules(ctx) or list(DEFAULT_FALLBACK_RULES)
        scoring_rules = [r for r in all_rules if isinstance(r, dict) and isinstance(r.get("scoring"), dict)]
        if not scoring_rules:
            scoring_rules = list(DEFAULT_FALLBACK_RULES)
        rule_options = [{"label": f"{r.get('name') or r.get('id')}（{r.get('id')}）", "value": str(r.get("id"))}
                        for r in scoring_rules if r.get("id")]
        rule_options.insert(0, {"label": "自动（优先本地strm）", "value": "auto"})
        _, chosen = _resolve_rules(ctx, cfg)
        if isinstance(chosen, dict) and chosen:
            scorer_rules, unsupported, _disabled = to_scorer_rules(chosen)
            slots = scorer_rules.get("slots") or []
            slot_names = [s.get("name") for s in slots if isinstance(s, dict) and s.get("name")]
            current_summary = "生效规则：%s（%s）｜分差门槛 %s 分" % (
                chosen.get("name") or chosen.get("id"), chosen.get("id"), scorer_rules.get("min_score_diff", 0))
            slots_text = "槽位：%s%s" % (", ".join(slot_names) or "默认单版本",
                                         ("（不支持维度：%s）" % ", ".join(unsupported)) if unsupported else "")
        else:
            current_summary, slots_text = "生效规则：内置默认", "槽位：默认单版本"
        return {"items": scoring_rules, "ruleOptions": rule_options,
                "currentRuleId": (chosen.get("id") if isinstance(chosen, dict) else "auto"),
                "currentRuleSummary": current_summary, "slotsStatusText": slots_text}

    @query("summary")
    def summary(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        run_id = _storage_get(ctx, KV_CURRENT_SCAN, default=None)
        if not run_id:
            return {"items": [], "toolItems": [], "total": 0, "toolTotal": 0, "overviewLine": "尚未扫描", "toolSummary": "非 HTTP 异常：0（未扫描或无异常）", "message": "未执行扫描"}
        items = _storage_list(ctx, f"scans/{run_id}/groups/", LIST_LIMIT)
        rows = []
        for idx, item in enumerate(items[:100]):
            group = item.get("value", item) if isinstance(item, dict) else {}
            if not isinstance(group, dict):
                continue
            _files = group.get("files", []) if isinstance(group.get("files"), list) else []
            def _score_of(names):
                for _f in _files:
                    if isinstance(_f, dict) and (_f.get("name") in names or _f.get("rel") in names):
                        return _f.get("score", 0)
                return 0
            _keep_names = _keep_drop_names(group.get("keep"), _files, "keep")
            _drop_names = _keep_drop_names(group.get("drop"), _files, "drop")
            rows.append({
                "key": f"{idx}",
                "label": group.get("label") or group.get("workDir", ""),
                "slot": group.get("slot", ""),
                "verdict": group.get("verdict", ""),
                "keep": ", ".join(_keep_names),
                "keepScore": _score_of(set(_keep_names)),
                "drop": ", ".join(_drop_names),
                "dropScore": _score_of(set(_drop_names)),
                "diff": group.get("diff", 0),
                "minDiff": group.get("minDiff", 0),
                "name": "",
                "url": "",
            })
        tool_items = _storage_list(ctx, f"scans/{run_id}/tool/", LIST_LIMIT)
        tool_rows = []
        for idx, item in enumerate(tool_items[:100]):
            row = item.get("value", item) if isinstance(item, dict) else {}
            if isinstance(row, dict):
                url = row.get("url", "") or ""
                low = url.lower()
                if not url:
                    reason = "空文件/无首行"
                elif low.startswith("file://"):
                    reason = "file:// 本地路径"
                elif url.startswith("/") or (len(url) > 2 and url[1] == ":"):
                    reason = "本地绝对路径"
                elif low.startswith(("ftp://", "smb://", "nfs://", "rtsp://", "rtmp://")):
                    reason = "非 HTTP 协议"
                else:
                    reason = "相对路径/其他非 HTTP"
                tool_rows.append({
                    "key": f"tool-{idx}", "seq": idx + 1,
                    "name": row.get("name", ""), "rel": row.get("rel", ""),
                    "url": url, "reason": reason, "ref": row.get("ref", ""),
                })
        scan_summary = _storage_get(ctx, f"scans/{run_id}/summary", default={})
        total = scan_summary.get("storedGroups", len(items)) if isinstance(scan_summary, dict) else len(items)
        tool_count = scan_summary.get("nonHttpCount", len(tool_rows)) if isinstance(scan_summary, dict) else len(tool_rows)
        tool_summary = ("非 HTTP 异常：%s 个（%s）" % (tool_count, "、".join(sorted({r["reason"] for r in tool_rows})) or "未扫描")) if tool_rows else "非 HTTP 异常：0（未扫描或无异常）"
        overview_line = "共 %d 组重复判定（%d 条明细）" % (total, len(rows)) if rows else "本次未发现重复组"
        return {"items": rows, "toolItems": tool_rows, "total": total,
                "toolTotal": tool_count, "overviewLine": overview_line, "toolSummary": tool_summary, "runId": run_id}

    # -- Actions ---------------------------------------------------------
    @action("save-config")
    def save_config(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        cfg = _cfg(ctx)
        for key in CONFIG_KEYS:
            if key in payload:
                cfg[key] = payload[key]
        cfg["readStrmContent"] = bool(cfg.get("readStrmContent", True))
        for _mk in ("localDestDir", "cloudDestDir"):
            if _mk in payload and isinstance(payload[_mk], str):
                cfg[_mk] = payload[_mk].strip()
        if "scanSource" in payload:
            cfg["scanSource"] = _clean_source(payload.get("scanSource"))
        if "toolSource" in payload:
            cfg["toolSource"] = _clean_source(payload.get("toolSource"))
        _storage_set(ctx, KV_CONFIG, {k: cfg.get(k) for k in CONFIG_KEYS})
        return {"ok": True, "config": {k: cfg.get(k) for k in CONFIG_KEYS}}

    @action("select-rule")
    def select_rule(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        rule_id = str(payload.get("ruleId") or "auto")
        cfg = _cfg(ctx)
        cfg["ruleId"] = rule_id
        _storage_set(ctx, KV_CONFIG, cfg)
        return {"ok": True, "ruleId": rule_id}

    @action("dup-clear")
    def dup_clear(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        run_id = _storage_get(ctx, KV_CURRENT_SCAN, default=None)
        if run_id:
            for item in _storage_list(ctx, f"scans/{run_id}/groups/", LIST_LIMIT):
                try:
                    ctx.mt.storage.delete(key=item.get("key", ""))
                except Exception:
                    pass
            _storage_set(ctx, f"scans/{run_id}/summary", {"state": "cleared"})
        _storage_set(ctx, KV_CURRENT_SCAN, None)
        return {"ok": True}

    @action("dup-scan")
    def dup_scan(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        cfg = _cfg(ctx)
        source = _clean_source(payload.get("source")) or cfg.get("scanSource")
        if source is None:
            raise ValueError("请先在页面选择有效的扫描目录（DirectoryPicker）")
        if payload.get("ruleId"):
            cfg["ruleId"] = str(payload["ruleId"])
        if "readStrmContent" in payload:
            cfg["readStrmContent"] = bool(payload["readStrmContent"])
        for _mk in ("localDestDir", "cloudDestDir"):
            if _mk in payload and isinstance(payload[_mk], str) and payload[_mk].strip():
                cfg[_mk] = payload[_mk].strip()
        _persist_sources(ctx, cfg, source=source)
        _storage_set(ctx, KV_CONFIG, {k: cfg.get(k) for k in CONFIG_KEYS})
        run_id = f"scan-{uuid.uuid4().hex[:12]}"
        # 本地快路径优先（local 根 strm）：同步本地遍历后直接比对，不经 tasks。
        local_base = _resolve_local_base(ctx, source)
        if local_base:
            _storage_set(ctx, f"scans/{run_id}/summary", {
                "state": "running", "rootId": source["rootId"], "path": source["path"],
                "mode": "local", "config": {k: cfg[k] for k in CONFIG_KEYS},
            })
            _storage_set(ctx, KV_CURRENT_SCAN, run_id)
            return self._finish_local_scan(ctx, run_id, source, local_base, cfg)
        # 云端/非 strm 根：走宿主 files.scan 异步任务。
        # files.scan 的 query 是任务内搜索词（无官方文档/示例证明可按 *.strm 后缀过滤，
        # 见 wiki business.md/capabilities.md），不可赌；仍全量扫、collect 端客户端过滤 .strm。
        task = ctx.mt.files.scan(
            root_id=source["rootId"],
            path=source["path"],
            video_only=False,  # ⚠️ .strm 非视频容器，True 会被宿主过滤掉（已验证）
            limit=SCAN_LIMIT,
            idempotency_key=_new_idempotency_key(ctx, f"dup-scan:{run_id}"),
        )
        ref = task.get("ref") if isinstance(task, dict) else None
        if not ref:
            raise RuntimeError("files.scan 未返回任务引用")
        _storage_set(ctx, f"scans/{run_id}/summary", {
            "state": "running", "rootId": source["rootId"], "path": source["path"],
            "mode": "cloud", "taskRef": ref, "config": {k: cfg[k] for k in CONFIG_KEYS},
        })
        _storage_set(ctx, KV_CURRENT_SCAN, run_id)
        ctx.mt.tasks.wait(
            refs=[ref],
            completion={"action": "dup-scan-collect", "input": {"runId": run_id}},
        )
        return {"waiting": True}

    def _finish_local_scan(self, ctx: Any, run_id: str, source: dict[str, Any],
                           local_base: str, cfg: dict[str, Any]) -> dict[str, Any]:
        """本地快路径收尾：流式遍历 -> 按作品目录增量预分组比对 -> 存储。

        50w+ 库不能全量 entries + works 双驻留（worker OOM 中断，实测 45w 处退出），
        故边扫边按 work_dir 聚合，仅对候选桶读首行比对；groups 仍全量存 storage（有 MAX_GROUPS_STORED 上限）。
        """
        # 流式按作品目录连续聚合：os.walk 按目录序，同一 work_dir 连续出现，变化即刷（内存 O(单作品) 而非 O(全库)）
        all_rules, chosen = _resolve_rules(ctx, cfg)
        scorer = chosen if isinstance(chosen, dict) and chosen else all_rules
        read_content = bool(cfg.get("readStrmContent", True))
        groups: list[dict[str, Any]] = []
        strm_count = 0
        cur_work = None
        cur_entries: list[dict[str, Any]] = []
        def _flush():
            if cur_entries:
                groups.extend(_evaluate_works(ctx, {cur_work: list(cur_entries)}, scorer, read_content))
        for entry in _local_scan_iter(ctx, source, local_base):
            strm_count += 1
            if strm_count > SCAN_LIMIT * 10:
                break
            wd = _work_dir_of(entry["rel"])
            if wd != cur_work:
                _flush()
                cur_work = wd
                cur_entries = []
            cur_entries.append(entry)
        _flush()
        entries_count = strm_count
        stored = _store_groups(ctx, run_id, groups)
        stats = summarize(groups)
        _storage_set(ctx, f"scans/{run_id}/summary", {
            "state": "done", "rootId": source["rootId"], "path": source["path"],
            "mode": "local", "strmCount": entries_count,
            "storedGroups": stored, "stats": stats,
        })
        _log(ctx, "info", f"dup-scan 本地快路径完成：strm={entries_count} groups={stored}")
        return {"ok": True, "runId": run_id, "storedGroups": stored, "stats": stats,
                "mode": "local", "strmCount": entries_count}

    @continuation("dup-scan-collect")
    def dup_scan_collect(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        run_id = (payload or {}).get("runId") or _storage_get(ctx, KV_CURRENT_SCAN, default="")
        task_refs = []
        try:
            tasks = (ctx.data.get("tasks", []) if isinstance(getattr(ctx, "data", None), dict) else [])
            task_refs = [t.get("ref") for t in tasks if isinstance(t, dict) and t.get("ref")]
        except Exception:
            task_refs = []
        summary = _storage_get(ctx, f"scans/{run_id}/summary", default={}) or {}
        cfg = summary.get("config") or _cfg(ctx)
        read_content = bool(cfg.get("readStrmContent", True))
        all_rules, _chosen = _resolve_rules(ctx, cfg)
        works: dict[str, list[dict[str, Any]]] = {}
        strm_count = 0
        for ref in task_refs:
            for item in _collect_task_items(ctx, ref):
                file = item.get("file") or {}
                name = file.get("name", "")
                if not name.lower().endswith(".strm") or file.get("isDir"):
                    continue
                strm_count += 1
                rel = file.get("path", name)
                work_dir = _work_dir_of(rel)
                works.setdefault(work_dir, []).append({
                    "rel": rel, "name": name,
                    "ref": file.get("ref", ""), "version": file.get("version", ""),
                    "rootId": file.get("rootId", summary.get("rootId", "")),
                })
        groups = _evaluate_works(ctx, works, _chosen if isinstance(_chosen, dict) and _chosen else all_rules, read_content)
        stored = _store_groups(ctx, run_id, groups)
        stats = summarize(groups)
        _storage_set(ctx, f"scans/{run_id}/summary", {
            "state": "done", "rootId": summary.get("rootId", ""), "path": summary.get("path", ""),
            "strmCount": strm_count, "works": len(works),
            "storedGroups": stored, "stats": stats,
        })
        _log(ctx, "info", f"dup-scan 完成：strm={strm_count} works={len(works)} groups={stored}")
        return {"ok": True, "runId": run_id, "storedGroups": stored, "stats": stats}

    @action("tool-scan-non-http")
    def tool_scan_non_http(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        cfg_t = _cfg(ctx)
        source = _clean_source(payload.get("source")) or cfg_t.get("toolSource")
        if source is None:
            raise ValueError("请先选择工具扫描目录（DirectoryPicker）")
        _persist_sources(ctx, cfg_t, tool_source=source)
        _storage_set(ctx, KV_CONFIG, {k: cfg_t.get(k) for k in CONFIG_KEYS})
        run_id = _storage_get(ctx, KV_CURRENT_SCAN, default=None) or f"scan-{uuid.uuid4().hex[:12]}"
        _storage_set(ctx, KV_CURRENT_SCAN, run_id)
        _existing = _storage_get(ctx, f"scans/{run_id}/summary", default={}) or {}
        if not isinstance(_existing, dict) or not _existing:
            _storage_set(ctx, f"scans/{run_id}/summary", {"state": "running", "rootId": source["rootId"], "path": source["path"], "nonHttpCount": 0})
        local_base = _resolve_local_base(ctx, source)
        if local_base:
            return self._finish_local_tool_scan(ctx, run_id, source, local_base)
        task = ctx.mt.files.scan(
            root_id=source["rootId"],
            path=source["path"],
            video_only=False,  # ⚠️ 同上：.strm 必须 video_only=False
            limit=SCAN_LIMIT,
            idempotency_key=_new_idempotency_key(ctx, f"tool-scan:{run_id}"),
        )
        ref = task.get("ref") if isinstance(task, dict) else None
        if not ref:
            raise RuntimeError("files.scan 未返回任务引用")
        ctx.mt.tasks.wait(
            refs=[ref],
            completion={"action": "tool-scan-collect", "input": {"runId": run_id}},
        )
        return {"waiting": True}

    def _finish_local_tool_scan(self, ctx: Any, run_id: str, source: dict[str, Any],
                                local_base: str) -> dict[str, Any]:
        """本地快路径排查非 HTTP：本地遍历 -> 本地 open 首行（与 collect 同语义）。"""
        rows: list[dict[str, Any]] = []
        scanned = 0
        for entry in _local_scan_iter(ctx, source, local_base):
            scanned += 1
            if scanned % 20000 == 1:
                _progress(ctx, scanned, f"本地检查非 HTTP 链接 {scanned}")
            url = _read_first_line(ctx, entry)
            if url and not url.lower().startswith(("http://", "https://")):
                rows.append({"name": entry["name"], "url": url, "ref": "", "version": "",
                             "rootId": source["rootId"], "rel": entry["rel"]})
        for seq, row in enumerate(rows[:MAX_TOOL_ROWS]):
            _storage_set(ctx, f"scans/{run_id}/tool/{seq:04d}", row)
        _prev = _storage_get(ctx, f"scans/{run_id}/summary", default={}) or {}
        if isinstance(_prev, dict):
            _prev["nonHttpCount"] = len(rows)
            _storage_set(ctx, f"scans/{run_id}/summary", _prev)
        _log(ctx, "info", f"tool-scan 本地快路径完成：非 HTTP strm={len(rows)}")
        return {"ok": True, "runId": run_id, "nonHttpCount": len(rows), "mode": "local"}

    @continuation("tool-scan-collect")
    def tool_scan_collect(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        run_id = (payload or {}).get("runId") or _storage_get(ctx, KV_CURRENT_SCAN, default="")
        task_refs = []
        try:
            tasks = (ctx.data.get("tasks", []) if isinstance(getattr(ctx, "data", None), dict) else [])
            task_refs = [t.get("ref") for t in tasks if isinstance(t, dict) and t.get("ref")]
        except Exception:
            task_refs = []
        rows: list[dict[str, Any]] = []
        scanned = 0
        for ref in task_refs:
            for item in _collect_task_items(ctx, ref):
                file = item.get("file") or {}
                name = file.get("name", "")
                if not name.lower().endswith(".strm") or file.get("isDir"):
                    continue
                scanned += 1
                if scanned % 50 == 1:
                    _progress(ctx, scanned, f"检查非 HTTP 链接 {scanned}")
                url = _read_first_line(ctx, {"ref": file.get("ref", "")})
                if url and not url.lower().startswith(("http://", "https://")):
                    rows.append({
                        "name": name, "url": url,
                        "ref": file.get("ref", ""), "version": file.get("version", ""),
                        "rootId": file.get("rootId", ""), "rel": file.get("path", name),
                    })
        for seq, row in enumerate(rows[:MAX_TOOL_ROWS]):
            _storage_set(ctx, f"scans/{run_id}/tool/{seq:04d}", row)
        _prev = _storage_get(ctx, f"scans/{run_id}/summary", default={}) or {}
        if isinstance(_prev, dict):
            _prev["nonHttpCount"] = len(rows)
            _storage_set(ctx, f"scans/{run_id}/summary", _prev)
        _log(ctx, "info", f"tool-scan 完成：非 HTTP strm={len(rows)}")
        return {"ok": True, "runId": run_id, "nonHttpCount": len(rows)}

    def _load_groups(self, ctx: Any) -> tuple[str, list[dict[str, Any]]]:
        run_id = _storage_get(ctx, KV_CURRENT_SCAN, default=None) or ""
        groups: list[dict[str, Any]] = []
        if run_id:
            for item in _storage_list(ctx, f"scans/{run_id}/groups/", LIST_LIMIT):
                value = item.get("value", item) if isinstance(item, dict) else {}
                if isinstance(value, dict):
                    groups.append(value)
        return run_id, groups

    @action("batch-wash")
    def batch_wash(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        selections = payload.get("selections") or []
        run_id, groups = self._load_groups(ctx)
        cfg = _cfg(ctx)
        # 重建 _localPath：stored file 只有 rel/name，scan 时的 base 已记在 summary
        scan_summary = _storage_get(ctx, f"scans/{run_id}/summary", default={}) if run_id else {}
        root_id = (scan_summary.get("rootId") if isinstance(scan_summary, dict) else None) or ""
        scan_path = (scan_summary.get("path") if isinstance(scan_summary, dict) else None) or "."
        local_base = None
        if root_id:
            try:
                local_base = _resolve_local_base(ctx, {"rootId": root_id, "path": scan_path})
            except Exception:
                local_base = None
        candidates: list[dict[str, Any]] = []
        selected = set(str(s) for s in selections) if selections else None
        for idx, group in enumerate(groups):
            if selected is not None and str(idx) not in selected:
                continue
            drop = set(group.get("drop", []))
            if not drop:
                continue
            for file in group.get("files", []):
                if file.get("name") in drop or file.get("rel") in drop:
                    f = dict(file)
                    if local_base and f.get("rel"):
                        f["rootId"] = root_id
                        f["_localPath"] = os.path.join(local_base, f["rel"])
                    candidates.append(f)
        result = _delete_by_content(ctx, candidates, "batch-wash",
                                    local_dest=cfg.get("localDestDir", "") or "",
                                    cloud_dest=cfg.get("cloudDestDir", "") or "")
        _log(ctx, "info", f"batch-wash run={run_id} {result}")
        return {"ok": True, **result}

    @action("batch-ignore")
    def batch_ignore(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        # 保留所选：仅记录，不删除（与旧版行为一致，无文件系统副作用）。
        selections = (payload or {}).get("selections") or []
        _log(ctx, "info", f"batch-ignore 保留 {len(selections)} 项")
        return {"ok": True, "ignored": len(selections)}

    @action("manual-decide")
    def manual_decide(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        keep = payload.get("keep") or []
        drop = payload.get("drop") or []
        candidates = [
            {"name": n, "rel": n, "ref": "", "rootId": ""} for n in drop
        ]
        # 手动删除同样走宿主删除接口；无 ref 时按名称无法定位则跳过。
        runnable = [c for c in candidates if c.get("ref")]
        result = _delete_via_files_batch(ctx, runnable, "manual-decide") if runnable else {
            "deleted": 0, "skipped": len(candidates), "requested": len(candidates)}
        return {"ok": True, "keep": keep, **result}

    @action("tool-batch-delete")
    def tool_batch_delete(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        selections = payload.get("selections") or None
        run_id = _storage_get(ctx, KV_CURRENT_SCAN, default=None) or ""
        candidates: list[dict[str, Any]] = []
        if run_id:
            for idx, item in enumerate(_storage_list(ctx, f"scans/{run_id}/tool/", LIST_LIMIT)):
                value = item.get("value", item) if isinstance(item, dict) else {}
                if selections is not None and f"tool-{idx}" not in {str(x) for x in selections}:
                    continue
                if isinstance(value, dict) and value.get("ref"):
                    candidates.append(value)
        result = _delete_via_files_batch(ctx, candidates, "tool-batch-delete")
        return {"ok": True, **result}

    @action("tool-delete-single")
    def tool_delete_single(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        ref = payload.get("ref") or payload.get("fileRef") or ""
        if not ref:
            raise ValueError("缺少文件引用 ref")
        result = ctx.mt.files.delete(
            file_ref=ref, idempotency_key=_new_idempotency_key(ctx, "tool-delete-single"))
        return {"ok": True, "result": result}

    @action("tool-clear")
    def tool_clear(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        run_id = _storage_get(ctx, KV_CURRENT_SCAN, default=None) or ""
        if run_id:
            for item in _storage_list(ctx, f"scans/{run_id}/tool/", LIST_LIMIT):
                try:
                    ctx.mt.storage.delete(key=item.get("key", ""))
                except Exception:
                    pass
        return {"ok": True}

    # 兼容旧版存储键别名（只读迁移提示，不再写入）。
    KV_GROUPS = "groups"
    KV_META = "meta"
    KV_TOOL_STRMS = "tool_local_strms"
    KV_TOOL_META = "tool_meta"


plugin = Plugin()  # 宿主按 plugin.py:Plugin 加载
