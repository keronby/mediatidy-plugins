# -*- coding: utf-8 -*-
"""media.dup-scan — STRM 重复查重（MT Python SDK v2，1.1.0）

重构要点（对齐官方 Wiki SDK v2）：
- 不再用 os.walk/os.scandir/os.remove/绝对路径访问宿主文件；扫描走
  ctx.mt.files.scan（异步任务）+ tasks.wait + @continuation 分页
  tasks.items；读取 STRM 首行走 files.readText；删除走 files.batch
  (operation=delete) / files.delete，附带文件先 files.stat 定位。
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
import uuid
from typing import Any

from mt_plugin import Plugin as PluginBase, action, continuation, query, ui

from core.nameinfo import extract_real_ext
from core.rules import DEFAULT_FALLBACK_RULES, get_rule_by_id, load_all_rules, to_scorer_rules
from core.workscan import parse_work_dir, scan_work, summarize

MANIFEST_ID = "media.dup-scan"
PLUGIN_VERSION = "1.1.0"

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
        "keep": group.get("keep", []),
        "drop": group.get("drop", []),
        "files": files,
    }


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
    return list(all_rules), (all_rules[0] if all_rules else {})


def _evaluate_works(
    ctx: Any,
    works: dict[str, list[dict[str, Any]]],
    rules: Any,
    read_content: bool,
) -> list[dict[str, Any]]:
    """对每个作品目录用 core.workscan.scan_work 评分比对。

    readStrmContent 开启且同作品 STRM >= 2 时，才对候选文件 readText
    读取首行提取真实扩展名做 ISO 槽隔离；其余用文件后缀兜底。
    """
    # rules 可能是列表（_resolve_rules 旧返回）或单 dict：只取第一条 dict
    if isinstance(rules, list):
        rules = next((r for r in rules if isinstance(r, dict)), None)
    scorer_rules = to_scorer_rules(rules) if isinstance(rules, dict) and rules else to_scorer_rules(DEFAULT_FALLBACK_RULES[0])
    groups: list[dict[str, Any]] = []
    for work_dir in sorted(works):
        entries = works[work_dir]
        if len(entries) < 2:
            continue
        if read_content:
            for entry in entries:
                ref = entry.get("ref")
                if not ref:
                    continue
                try:
                    data = ctx.mt.files.read_text(ref=ref)
                except Exception:
                    continue
                text = data.get("text", "") if isinstance(data, dict) else ""
                entry["realExt"] = extract_real_ext(text) if text else ""
        groups.extend(scan_work(work_dir, entries, scorer_rules))
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
            ctx.log.error(f"files.batch delete failed: {exc}")
            skipped += len(items)
    return {"deleted": deleted, "skipped": skipped, "requested": len(candidates)}


def _collect_task_items(ctx: Any, ref: str) -> list[dict[str, Any]]:
    """分页拉取异步任务的全部文件条目。"""
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(500):
        try:
            data = ctx.mt.tasks.items(ref=ref, offset=offset, limit=PAGE_SIZE)
        except Exception:
            break
        items = data.get("items", []) if isinstance(data, dict) else []
        out.extend(items)
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
    def get_page(self, ctx: Any) -> dict[str, Any]:
        cfg = _cfg_view(ctx)
        # 宿主 run.* 只读：dup-scan 动作排队/运行中时按钮 loading+禁用+改文案
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
        # 真实规则下拉：options 绑 query.rules.data.ruleOptions（query 已做过滤/拼标签），
        # fallback 保留 auto/内置默认，query 未加载时仍可点
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
        children = [
            ui.Text(text="STRM 重复查重", size="xl"),
            ui.Text(text="选择扫描目录（DirectoryPicker 返回 {rootId, path}），扫描走宿主 files.scan 异步任务；结果按 scans/<runId>/groups 分页存储。", tone="muted"),
            ui.DirectoryPicker(label="扫描目录", bind="state.source"),
            ui.Select(label="查重规则", bind="state.ruleId", options=rule_options),
            ui.Text(text=ui.ref("query.rules.data.currentRuleSummary"), tone="muted"),
            ui.Text(text=ui.ref("query.rules.data.slotsStatusText"), tone="muted"),
            ui.Checkbox(label="读取 STRM 首行做扩展名槽隔离", bind="state.readStrmContent"),
            ui.Button(
                label=ui.expr("if", is_scan_running, "⏳ 正在查重扫描中...", "开始查重"),
                disabled=is_scan_running,
                loading=is_scan_running,
                onClick=[
                    {"type": "invoke", "actionId": "dup-scan", "input": {"source": ui.ref("state.source"), "ruleId": ui.ref("state.ruleId")}},
                    {"type": "query", "queryId": "summary"},
                    {"type": "query", "queryId": "rules"},
                ],
            ),
            ui.Text(text="扫描中…（files.scan 异步任务，收集完成后表格自动刷新）", visible=is_scan_running, tone="muted"),
            ui.Table(
                source=ui.ref("query.summary.data.items"),
                columns=[
                    {"label": "作品", "cells": [ui.Text(text=ui.ref("row.label"))]},
                    {"label": "槽位", "cells": [ui.Text(text=ui.ref("row.slot"))]},
                    {"label": "判定", "cells": [ui.Text(text=ui.ref("row.verdict"))]},
                    {"label": "保留", "cells": [ui.Text(text=ui.ref("row.keep"))]},
                    {"label": "删除", "cells": [ui.Text(text=ui.ref("row.drop"))]},
                    {"label": "分差", "cells": [ui.Text(text=ui.ref("row.minDiff"))]},
                ],
                rowKey="key",
                selectionBind="state.selected_wash_ids",
            ),
            ui.Button(
                label="批量洗白（删除低分重复）",
                onClick=[{"type": "invoke", "actionId": "batch-wash", "input": {"selections": ui.ref("state.selected_wash_ids")}}],
            ),
            ui.Button(
                label="批量忽略（保留所选）",
                onClick=[{"type": "invoke", "actionId": "batch-ignore", "input": {"selections": ui.ref("state.selected_wash_ids")}}],
            ),
        ]
        # 扫描非 HTTP 单独成页：目录/按钮/结果区/统计都在 Tab 内
        tool_tab = ui.Tab(
            ui.Text(text="扫描 STRM 首行非 http/https（本地路径、相对路径、空文件等异常）", tone="muted"),
            ui.DirectoryPicker(label="工具扫描目录", bind="state.tool_source"),
            ui.Button(
                label=ui.expr("if", is_tool_running, "⏳ 正在扫描非 HTTP...", "扫描非 HTTP 链接"),
                disabled=is_tool_running,
                loading=is_tool_running,
                onClick=[
                    {"type": "invoke", "actionId": "tool-scan-non-http", "input": {"source": ui.ref("state.tool_source")}},
                    {"type": "query", "queryId": "summary"},
                ],
            ),
            ui.Text(text=ui.ref("query.summary.data.toolSummary"), tone="muted"),
            ui.Text(text="扫描中…（files.scan 异步任务，收集完成后结果区自动刷新）", visible=is_tool_running, tone="muted"),
            ui.Table(
                source=ui.ref("query.summary.data.toolItems"),
                columns=[
                    {"label": "序号", "cells": [ui.Text(text=ui.ref("row.seq"))]},
                    {"label": "文件", "cells": [ui.Text(text=ui.ref("row.name"))]},
                    {"label": "相对路径", "cells": [ui.Text(text=ui.ref("row.rel"))]},
                    {"label": "首行（异常）", "cells": [ui.Text(text=ui.ref("row.url"))]},
                    {"label": "异常类型", "cells": [ui.Text(text=ui.ref("row.reason"))]},
                ],
                rowKey="key",
                selectionBind="state.selected_tool_ids",
            ),
            ui.Button(
                label="删除所选异常文件",
                onClick=[{"type": "invoke", "actionId": "tool-batch-delete", "input": {"selections": ui.ref("state.selected_tool_ids")}}],
            ),
            ui.Button(
                label="清空异常结果",
                onClick=[{"type": "invoke", "actionId": "tool-clear", "input": {}}, {"type": "query", "queryId": "summary"}],
            ),
            id="tab-tool",
            label="🔧 扫描非 HTTP",
        )
        dup_tab = ui.Tab(*children, id="tab-dup", label="🎬 STRM 重复查重")
        return ui.page(
            ui.Tabs(dup_tab, tool_tab, bind="state.activeTab"),
            state={
                "activeTab": "tab-dup",
                "source": cfg.get("scanSource"),
                "tool_source": cfg.get("toolSource"),
                "selected_tool_ids": [],
                "ruleId": cfg.get("ruleId", "auto"),
                "readStrmContent": cfg.get("readStrmContent", True),
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
        rule_options.insert(0, {"label": "自动（首条启用评分规则）", "value": "auto"})
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
            return {"items": [], "toolItems": [], "total": 0, "toolTotal": 0, "toolSummary": "非 HTTP 异常：0（未扫描或无异常）", "message": "未执行扫描"}
        items = _storage_list(ctx, f"scans/{run_id}/groups/", LIST_LIMIT)
        rows = []
        for idx, item in enumerate(items[:100]):
            group = item.get("value", item) if isinstance(item, dict) else {}
            if not isinstance(group, dict):
                continue
            rows.append({
                "key": f"{idx}",
                "label": group.get("label") or group.get("workDir", ""),
                "slot": group.get("slot", ""),
                "verdict": group.get("verdict", ""),
                "keep": ", ".join(group.get("keep", [])),
                "drop": ", ".join(group.get("drop", [])),
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
        return {"items": rows, "toolItems": tool_rows, "total": total,
                "toolTotal": tool_count, "toolSummary": tool_summary, "runId": run_id}

    # -- Actions ---------------------------------------------------------
    @action("save-config")
    def save_config(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        payload = payload or {}
        cfg = _cfg(ctx)
        for key in CONFIG_KEYS:
            if key in payload:
                cfg[key] = payload[key]
        cfg["readStrmContent"] = bool(cfg.get("readStrmContent", True))
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
        _persist_sources(ctx, cfg, source=source)
        _storage_set(ctx, KV_CONFIG, {k: cfg.get(k) for k in CONFIG_KEYS})
        run_id = f"scan-{uuid.uuid4().hex[:12]}"
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
            "taskRef": ref, "config": {k: cfg[k] for k in CONFIG_KEYS},
        })
        _storage_set(ctx, KV_CURRENT_SCAN, run_id)
        ctx.mt.tasks.wait(
            refs=[ref],
            completion={"action": "dup-scan-collect", "input": {"runId": run_id}},
        )
        return {"waiting": True}

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
        ctx.log.info(f"dup-scan 完成：strm={strm_count} works={len(works)} groups={stored}")
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
        for ref in task_refs:
            for item in _collect_task_items(ctx, ref):
                file = item.get("file") or {}
                name = file.get("name", "")
                if not name.lower().endswith(".strm") or file.get("isDir"):
                    continue
                try:
                    data = ctx.mt.files.read_text(ref=file.get("ref", ""))
                except Exception:
                    continue
                text = data.get("text", "") if isinstance(data, dict) else ""
                url = text.splitlines()[0].strip() if text else ""
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
        ctx.log.info(f"tool-scan 完成：非 HTTP strm={len(rows)}")
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
                    candidates.append(file)
        result = _delete_via_files_batch(ctx, candidates, "batch-wash")
        ctx.log.info(f"batch-wash run={run_id} {result}")
        return {"ok": True, **result}

    @action("batch-ignore")
    def batch_ignore(self, payload: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        # 保留所选：仅记录，不删除（与旧版行为一致，无文件系统副作用）。
        selections = (payload or {}).get("selections") or []
        ctx.log.info(f"batch-ignore 保留 {len(selections)} 项")
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
