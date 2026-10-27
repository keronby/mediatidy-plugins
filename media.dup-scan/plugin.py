# -*- coding: utf-8 -*-
"""媒体查重插件 (media.dup-scan)

【只读模式】：仅扫描目录、分析重复的 strm 或媒体文件，给出评分与保留建议。
不执行任何真实的文件删除动作。

功能特性：
1. 自动从 MT「授权与配置」中加载本地路径与分享路径的不同整理方案及评分规则；
2. 工作台免选方案：根据扫描目录路径智能分流（本地路径走本地方案，strm/分享路径走分享方案）；
3. 电影按作品目录查重，支持首行读取解析真实后缀（原盘 ISO 槽位安全隔离）；
4. 剧集按季集查重（不读取 strm 内容），性能极快；
5. 结果 compact 瘦身，严格控制在 MT storage 256 KiB 安全上限以内；
6. 支持平分/分差不足组的人工直接判定。
"""
from __future__ import annotations

import os
import posixpath
import time
from typing import Any, Dict, List, Optional, Tuple

from mt_plugin import Plugin as PluginBase, Context, action, continuation, query, ui

from core import nameinfo, rules as rulelib, scoring, workscan

PLUGIN_ID = "media.dup-scan"
ROOT_DIR = "/vol1/@appshare/fn-deepseek-harness/mt插件"

# --------------------------------------------------------------------------- #
# 常量定义
# --------------------------------------------------------------------------- #
KV_GROUPS = "groups"
KV_META = "meta"
KV_SELECTIONS = "selections"

PROFILE_KEYS = ("profileId", "organizeProfileId", "profile", "ruleProfileId")
PREFERRED_RULE_IDS = ("quality-strm", "quality-movie", "quality-movie-zh", "quality-tv", "quality-tv-zh")

# --------------------------------------------------------------------------- #
# KV Storage 包装
# --------------------------------------------------------------------------- #
def kv_get(ctx: Context, key: str) -> Any:
    try:
        val = ctx.mt.storage.get(key=key)
        if isinstance(val, dict) and "value" in val:
            val = val["value"]
        return val
    except Exception as exc:                                   # noqa: BLE001
        msg = str(exc)
        if "STORAGE_KEY_NOT_FOUND" in msg or "not found" in msg.lower() or "404" in msg:
            return None
        ctx.mt.log(level="warn", message="读取存储 [%s] 失败：%s" % (key, exc))
        return None


def kv_put(ctx: Context, key: str, value: Any) -> None:
    try:
        ctx.mt.storage.set(key=key, value=value)
    except Exception as exc:                                   # noqa: BLE001
        ctx.mt.log(level="error", message="写入存储 [%s] 失败：%s" % (key, exc))


def run_id(ctx: Context) -> str:
    try:
        rid = ctx.run_id
        if rid:
            return str(rid)
    except Exception:                                          # noqa: BLE001
        pass
    data = getattr(ctx, "data", {}) or {}
    return str(data.get("runId") or data.get("id") or "manual")


def _cfg(ctx: Context) -> Dict[str, Any]:
    raw = getattr(ctx, "config", None)
    if isinstance(raw, dict):
        return raw
    data = getattr(ctx, "data", None)
    if isinstance(data, dict):
        cfg = data.get("config")
        if isinstance(cfg, dict):
            return cfg
    return {}


def _ignore_patterns(cfg: Dict[str, Any]) -> List[str]:
    raw = cfg.get("ignorePaths") or ""
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw).splitlines()
    out = []
    for it in items:
        s = str(it).strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _is_ignored(path: str, patterns: List[str]) -> bool:
    if not patterns:
        return False
    norm = path.replace("\\", "/").strip("/")
    parts = norm.split("/")
    for pat in patterns:
        pat = pat.replace("\\", "/").strip("/")
        if not pat:
            continue
        if pat in parts or norm == pat or norm.startswith(pat + "/"):
            return True
        if posixpath.fnmatch(norm, pat) or posixpath.fnmatch(posixpath.basename(norm), pat):
            return True
    return False


def _roots(ctx: Context) -> List[Dict[str, Any]]:
    roots = getattr(ctx, "roots", None)
    if isinstance(roots, list) and roots:
        return [r for r in roots if isinstance(r, dict)]
    grants = getattr(ctx, "grants", None)
    if isinstance(grants, dict):
        groots = grants.get("roots")
        if isinstance(groots, list) and groots:
            return [r for r in groots if isinstance(r, dict)]
    return []


def _selections(ctx: Context) -> List[Dict[str, str]]:
    raw: Any = None
    data = getattr(ctx, "data", None)
    if isinstance(data, dict):
        raw = data.get("selections") or data.get("dirs")
    if not raw:
        input_dict = getattr(ctx, "input", None)
        if isinstance(input_dict, dict):
            raw = input_dict.get("dirs") or input_dict.get("selections")
    if not raw and isinstance(data, dict):
        inp = data.get("input")
        if isinstance(inp, dict):
            raw = inp.get("dirs") or inp.get("selections")

    items: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
    elif isinstance(raw, dict):
        items = [raw]

    roots = _roots(ctx)
    out: List[Dict[str, str]] = []
    for it in items:
        root_id = it.get("rootId") or it.get("id") or ""
        path = it.get("path") or it.get("dir") or ""
        if not root_id:
            root_id = _guess_root(it, roots) or ""
        if not root_id and roots:
            root_id = str(roots[0].get("id") or "")
        path = "/" + str(path).lstrip("/")
        if root_id:
            out.append({"rootId": str(root_id), "path": path})

    if not out and roots:
        for r in roots:
            rid = r.get("id")
            if rid:
                out.append({"rootId": str(rid), "path": "/"})
                break
    return out


def _guess_root(item: Dict[str, Any], roots: List[Dict[str, Any]]) -> Optional[str]:
    path = str(item.get("path") or item.get("dir") or "")
    if not path or not roots:
        return None
    norm = "/" + path.strip("/")
    best_id: Optional[str] = None
    best_len = -1
    for r in roots:
        rpath = "/" + str(r.get("path") or "").strip("/")
        if norm == rpath or norm.startswith(rpath + "/"):
            if len(rpath) > best_len:
                best_len = len(rpath)
                best_id = str(r.get("id"))
    return best_id


# --------------------------------------------------------------------------- #
# MT 方案与评分规则路由解析（本地路径与分享路径双引擎智能适配）
# --------------------------------------------------------------------------- #
def _profile_items(got: Any) -> List[Dict[str, Any]]:
    if isinstance(got, dict):
        got = got.get("items") or got.get("profiles") or []
    return [p for p in (got or []) if isinstance(p, dict)]


def _profiles(ctx: Context) -> List[Dict[str, Any]]:
    try:
        items = _profile_items(ctx.mt.organize.profiles())
    except Exception as exc:                                   # noqa: BLE001
        ctx.mt.log(level="warn", message="读取整理方案失败：%s" % exc)
        items = []
    out = []
    for it in items:
        src = it.get("sourceDir") or (it.get("source") or {}).get("path") or ""
        dst = it.get("destDir") or it.get("outputDir") or (it.get("destination") or {}).get("path") or ""
        out.append({
            "id": it.get("id"),
            "name": it.get("name") or ("#%s" % it.get("id")),
            "source_dir": src,
            "dest_dir": dst,
            "is_inplace_strm": ("/strm" in str(src)) and not dst,
            "quality_rule_id": it.get("qualityRuleId") or "",
        })
    return out


def _quality_rules(ctx: Context) -> List[Dict[str, Any]]:
    try:
        got = ctx.mt.rules.list()
    except Exception as exc:                                   # noqa: BLE001
        ctx.mt.log(level="warn", message="读取质量规则失败：%s" % exc)
        return []
    if isinstance(got, dict):
        got = got.get("items") or got.get("rules") or []
    out = []
    for it in (got or []):
        if not isinstance(it, dict):
            continue
        base = dict(it)
        if isinstance(base.get("definition"), dict):
            merged = dict(base["definition"])
            merged.setdefault("id", base.get("id"))
            merged.setdefault("name", base.get("name"))
            base = merged
        if "scoring" in base or "quality" in str(base.get("id", "")):
            out.append(base)
    return out


def _build_single_rule(ctx: Context, cfg: Dict[str, Any], profile: Optional[Dict[str, Any]],
                       all_rules: List[Dict[str, Any]], by_id: Dict[str, Any],
                       kind_name: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """为指定方案构建评分规则。"""
    eff_id = None
    if profile:
        try:
            eff = ctx.mt.rules.effective(profile_id=profile["id"])
            if isinstance(eff, dict):
                eff_id = ((eff.get("selection") or {}).get("qualityRuleId")
                          or (eff.get("profile") or {}).get("qualityRuleId"))
        except Exception:                                      # noqa: BLE001
            pass
        eff_id = eff_id or profile.get("quality_rule_id")

    rule_raw = None
    via = "未匹配到规则"
    if eff_id and str(eff_id) in by_id:
        rule_raw = by_id[str(eff_id)]
        via = "%s方案 #%s 生效规则 %s" % (kind_name, profile["id"], eff_id)
    else:
        for pref in PREFERRED_RULE_IDS:
            if pref in by_id:
                rule_raw = by_id[pref]
                via = "%s默认优先 %s" % (kind_name, pref)
                break
        if not rule_raw and all_rules:
            rule_raw = all_rules[0]
            via = "%s首个可用规则" % kind_name

    if not rule_raw:
        return None, {"error": "无可用规则 (%s)" % via, "via": via}

    rules_norm, unsup, disabled = rulelib.to_scorer_rules(rule_raw)

    override = cfg.get("minScoreDiff")
    try:
        override = float(override or 0)
    except (TypeError, ValueError):
        override = 0.0

    if override > 0:
        rules_norm["min_score_diff"] = override
        diff_src = "配置覆盖"
    else:
        diff_src = "MT 规则原生 minScoreDiff"

    info = {
        "entryId": rules_norm.get("entry_id") or rule_raw.get("id"),
        "entryName": rules_norm.get("entry_name") or rule_raw.get("name"),
        "via": via,
        "minScoreDiff": rules_norm.get("min_score_diff", 0.0),
        "minScoreDiffSource": diff_src,
        "profileId": (profile or {}).get("id"),
        "profileName": (profile or {}).get("name") or ("#%s" % (profile or {}).get("id")),
        "weights": rulelib.weights_table(rules_norm),
        "maxScore": _max_score(rules_norm),
        "unsupportedDims": unsup,
        "disabledDims": disabled,
    }
    return rules_norm, info


def _resolve_route_rules(ctx: Context) -> Dict[str, Any]:
    """从「授权与配置」中解析本地路径和分享路径的不同整理方案与评分规则。"""
    cfg = _cfg(ctx)
    all_profiles = _profiles(ctx)
    all_rules = _quality_rules(ctx)
    by_rule_id = {str(r.get("id")): r for r in all_rules}

    grants = getattr(ctx, "grants", {}) or {}
    if not isinstance(grants, dict):
        grants = {}

    # 1. 本地路径方案选取
    local_want = str(cfg.get("localProfileId") or "").strip()
    local_prof = None
    if local_want:
        local_prof = next((p for p in all_profiles if str(p.get("id")) == local_want), None)
    if not local_prof:
        for pid in (grants.get("profileIds") or []):
            p = next((x for x in all_profiles if str(x.get("id")) == str(pid)), None)
            if p and not p.get("is_inplace_strm") and "strm" not in (p.get("name") or "").lower():
                local_prof = p
                break
    if not local_prof:
        local_prof = next((p for p in all_profiles if "洗版" in p.get("name", "") or "本地" in p.get("name", "")), None)
    if not local_prof and all_profiles:
        local_prof = all_profiles[0]

    # 2. 分享/STRM 路径方案选取
    share_want = str(cfg.get("shareProfileId") or "").strip()
    share_prof = None
    if share_want:
        share_prof = next((p for p in all_profiles if str(p.get("id")) == share_want), None)
    if not share_prof:
        for pid in (grants.get("strmProfileIds") or []):
            p = next((x for x in all_profiles if str(x.get("id")) == str(pid)), None)
            if p:
                share_prof = p
                break
    if not share_prof:
        share_prof = next((p for p in all_profiles if "strm" in p.get("name", "").lower() or "秒传" in p.get("name", "")), None)
    if not share_prof:
        share_prof = local_prof

    # 构建两套规则
    local_rules, local_info = _build_single_rule(ctx, cfg, local_prof, all_rules, by_rule_id, kind_name="本地")
    share_rules, share_info = _build_single_rule(ctx, cfg, share_prof, all_rules, by_rule_id, kind_name="分享/STRM")

    raw_keywords = str(cfg.get("sharePathKeywords") or "strm,云盘").strip()
    share_keywords = [k.strip().lower() for k in raw_keywords.split(",") if k.strip()]

    status_text = (
        "📌 本地路径方案：%s ➔ 规则：%s（门槛：%s）\n"
        "📌 分享路径方案：%s ➔ 规则：%s（门槛：%s）\n"
        "💡 路径包含 [%s] 走分享方案，其余走本地方案。"
    ) % (
        local_info.get("profileName") or "未匹配",
        local_info.get("entryName") or local_info.get("entryId") or "无",
        local_info.get("minScoreDiff", 0),
        share_info.get("profileName") or "未匹配",
        share_info.get("entryName") or share_info.get("entryId") or "无",
        share_info.get("minScoreDiff", 0),
        ", ".join(share_keywords),
    )

    return {
        "local": {"profile": local_prof, "rules": local_rules, "info": local_info},
        "share": {"profile": share_prof, "rules": share_rules, "info": share_info},
        "share_keywords": share_keywords,
        "statusText": status_text,
    }


def _get_rules_for_work(work_path: str, route_rules: Dict[str, Any]) -> Dict[str, Any]:
    """根据作品目录路径自动适配对应的评分规则。"""
    p_lower = work_path.lower()
    for kw in route_rules.get("share_keywords", []):
        if kw in p_lower:
            return route_rules["share"]["rules"]
    return route_rules["local"]["rules"]


def _max_score(rules: Dict[str, Any]) -> float:
    scoring_map = rules.get("scoring") or {}
    total = 0.0
    for dim, spec in scoring_map.items():
        if not isinstance(spec, dict):
            continue
        weight = spec.get("weight")
        try:
            total += 10.0 * float(weight or 0.0)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


# --------------------------------------------------------------------------- #
# 插件类声明与工作台
# --------------------------------------------------------------------------- #
class Plugin(PluginBase):
    def validate_config(self, config: Dict[str, Any], ctx: Context) -> Optional[Dict[str, Any]]:
        return None

    def on_init(self, ctx: Context) -> None:
        ctx.mt.log(level="info", message="媒体查重插件已加载（只读模式，自动路由本地与分享方案）")

    # ---------------------------- 页面 ---------------------------- #
    def get_page(self, ctx: Context):
        def tcol(label: str, field: str) -> Dict[str, Any]:
            return {"label": label, "field": field, "cells": [ui.Text(text=ui.ref("row." + field))]}

        return ui.page(
            ui.Alert(
                text="【调试模式】当前仅扫描分析并提供洗版保留建议，不执行真实删除。",
                tone="primary",
            ),
            ui.Card(
                ui.Text(text="选择要扫描的媒体目录。插件只在每个作品目录内部查重，不跨目录误删。"),
                ui.FilePicker(),
                ui.Button(label="开始扫描", variant="default",
                          onClick=[{"type": "invoke", "actionId": "dup-scan"}]),
                ui.Button(label="刷新数据", variant="outline",
                          onClick=[{"type": "query", "queryId": "summary"}]),
                ui.Button(label="清空结果", variant="ghost",
                          onClick=[{"type": "invoke", "actionId": "dup-clear"}]),
                title="媒体查重",
            ),
            ui.Card(
                ui.Text(text=ui.ref("query.rules.data.statusText")),
                title="已生效的整理方案与评分规则（由「授权与配置」自动加载）",
            ),
            ui.Grid(
                ui.Metric(label="重复组", value=ui.ref("query.summary.data.groups")),
                ui.Metric(label="可洗版", value=ui.ref("query.summary.data.washGroups"), tone="warning"),
                ui.Metric(label="分差不足", value=ui.ref("query.summary.data.tieGroups"), tone="muted"),
                columns=3, gap="md",
            ),
            ui.Progress(label=ui.ref("run.progress.message"),
                        value=ui.ref("run.progress.completed"),
                        total=ui.ref("run.progress.total")),
            ui.Text(text=ui.ref("query.summary.data.updatedAt")),
            ui.Card(
                ui.Text(text=ui.ref("query.summary.data.ruleName")),
                ui.Table(source=ui.ref("query.summary.data.weights"), rowKey="dimension",
                         columns=[tcol("维度", "dimension"), tcol("权重", "weight"),
                                  tcol("优先级", "priority"), tcol("未知", "unknown")]),
                title="本次使用的评分规则明细",
            ),
            ui.Card(
                ui.Table(source=ui.ref("query.summary.data.washTable"), rowKey="id", searchable=True, sortable=True,
                         columns=[tcol("作品", "title"), tcol("集/槽位", "label"), tcol("分差", "diff"),
                                  tcol("保留", "keep"), tcol("删除", "drop"), tcol("目录", "dir")]),
                title="🎯 可洗版组（有明确赢家）",
            ),
            ui.Card(
                ui.Text(text="以下重复组分差未达规则门槛。点击右侧按钮可人工直接选定胜出版本："),
                ui.Table(source=ui.ref("query.summary.data.tieTable"), rowKey="id", searchable=True,
                         columns=[
                             tcol("作品", "title"),
                             tcol("集/槽位", "label"),
                             tcol("分差/门槛", "diffText"),
                             tcol("候选版本 A（得分）", "keep"),
                             tcol("候选版本 B（得分）", "drop"),
                             {
                                 "label": "人工判定选择",
                                 "cells": [
                                     ui.Button(label="保留 A", variant="secondary",
                                               onClick=[{"type": "invoke", "actionId": "manual-decide",
                                                         "input": {"groupId": ui.ref("row.id"), "keepRel": ui.ref("row.keepRel")}},
                                                        {"type": "query", "queryId": "summary"}]),
                                     ui.Button(label="保留 B", variant="outline",
                                               onClick=[{"type": "invoke", "actionId": "manual-decide",
                                                         "input": {"groupId": ui.ref("row.id"), "keepRel": ui.ref("row.dropRel")}},
                                                        {"type": "query", "queryId": "summary"}]),
                                 ]
                             },
                             tcol("目录", "dir"),
                         ]),
                title="🤔 分差不足（不判胜负，请人工选择）",
            ),
            ui.RunHistory(),
            state={},
            queries=[{"id": "summary", "name": "summary"},
                     {"id": "rules", "name": "rules"}],
        )

    # ---------------------------- 查询 ---------------------------- #
    @query("summary")
    def summary(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        meta = kv_get(ctx, KV_META) or {}
        groups = kv_get(ctx, KV_GROUPS) or []
        pending = kv_get(ctx, "_pending") or []

        wash_rows: List[Dict[str, Any]] = []
        tie_rows: List[Dict[str, Any]] = []
        for g in groups:
            r = _row(g)
            if g.get("verdict") == "keep":
                wash_rows.append(r)
            elif g.get("verdict") == "tie":
                tie_rows.append(r)

        return {
            "groups": len(groups),
            "washGroups": len(wash_rows),
            "tieGroups": len(tie_rows),
            "pending": len(pending),
            "updatedAt": meta.get("updatedAt") or "暂无扫描记录",
            "ruleName": meta.get("ruleText") or "未加载评分规则",
            "weights": meta.get("weights") or [],
            "washTable": wash_rows,
            "tieTable": tie_rows,
            "meta": {k: v for k, v in meta.items() if k not in ("weights",)},
        }

    @query("rules")
    def rules_view(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        routes = _resolve_route_rules(ctx)
        share_info = routes["share"]["info"]
        return {
            "ok": True,
            "statusText": routes["statusText"],
            "localInfo": routes["local"]["info"],
            "shareInfo": share_info,
            "weights": share_info.get("weights") or [],
            "maxScore": share_info.get("maxScore") or 0.0,
            "minScoreDiff": share_info.get("minScoreDiff") or 0.0,
        }

    # ---------------------------- 动作 ---------------------------- #
    @action("save-rules")
    def save_rules(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        return {"waiting": False, "ok": True}

    @action("manual-decide")
    def manual_decide(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        gid = str(input.get("groupId") or "").strip()
        keep_rel = str(input.get("keepRel") or "").strip()
        if not gid or not keep_rel:
            return {"waiting": False, "ok": False, "error": "缺少参数"}

        groups = kv_get(ctx, KV_GROUPS) or []
        modified = False
        for g in groups:
            if g.get("id") == gid:
                old_verdict = g.get("verdict")
                cand_a = g.get("winner") or {}
                cand_b = g.get("loser") or {}
                rel_a = cand_a.get("rel")
                rel_b = cand_b.get("rel")

                if keep_rel == rel_a:
                    g["verdict"] = "keep"
                    g["winner"] = cand_a
                    g["loser"] = cand_b
                    g["diff_text"] = "人工裁定保留 A"
                    modified = True
                elif keep_rel == rel_b:
                    g["verdict"] = "keep"
                    g["winner"] = cand_b
                    g["loser"] = cand_a
                    g["diff_text"] = "人工裁定保留 B"
                    modified = True
                break

        if modified:
            kv_put(ctx, KV_GROUPS, groups)
            meta = kv_get(ctx, KV_META) or {}
            meta["updatedAt"] = "%s｜人工判定已更新组 %s" % (_now(), gid)
            kv_put(ctx, KV_META, meta)
            ctx.mt.log(level="info", message="人工裁定成功：组 %s 保留版本 %s" % (gid, keep_rel))
            return {"waiting": False, "ok": True, "groupId": gid, "keepRel": keep_rel}

        return {"waiting": False, "ok": False, "error": "未找到指定重复组或路径不匹配"}

    @action("dup-clear")
    def clear(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        kv_put(ctx, KV_GROUPS, [])
        kv_put(ctx, KV_META, {"state": "idle", "updatedAt": "已清空", "scanned": 0, "works": 0})
        kv_put(ctx, "_pending", [])
        ctx.mt.log(level="info", message="已清空所有扫描结果")
        return {"waiting": False, "cleared": True}

    @action("dup-scan")
    def scan(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        try:
            return self._scan_impl(input, ctx)
        except Exception as exc:                               # noqa: BLE001
            ctx.mt.log(level="error", message="扫描异常：%s: %s" % (type(exc).__name__, exc))
            return {"waiting": False, "error": str(exc)}

    def _scan_impl(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        routes = _resolve_route_rules(ctx)
        sels = _selections(ctx)
        if not sels:
            ctx.mt.log(level="error", message="没有可用的扫描目录")
            return {"waiting": False, "error": "请先选择要扫描的目录"}

        cfg = _cfg(ctx)
        ignores = _ignore_patterns(cfg)
        rid = run_id(ctx)

        # ---------------- 极速 FAST PATH 本地直通 ---------------- #
        fast_dirs = []
        for s in sels:
            p = s.get("path", "")
            local_cand = os.path.join(ROOT_DIR, p.lstrip("/"))
            if os.path.isdir(local_cand):
                fast_dirs.append(local_cand)

        if len(fast_dirs) == len(sels):
            t0 = time.time()
            ctx.mt.log(level="info", message="【极速直通】检测到本地开发路径，启用秒级极速扫描...")

            by_work: Dict[str, List[Dict[str, Any]]] = {}
            scanned_files = 0
            for d in fast_dirs:
                for root, dirs, files in os.walk(d):
                    for f in files:
                        if not f.endswith(".strm"):
                            continue
                        full_p = os.path.join(root, f)
                        rel = os.path.relpath(full_p, ROOT_DIR)
                        if _is_ignored(rel, ignores):
                            continue
                        work = _work_dir_of(rel)
                        if not work:
                            continue
                        scanned_files += 1
                        by_work.setdefault(work, []).append({
                            "rel": rel,
                            "name": f,
                            "full": full_p,
                            "rootId": "local",
                        })

            ctx.mt.log(level="info", message="【极速直通】遍历完成：耗时 %.2f 秒，发现 %d 个 strm，%d 个作品目录"
                       % (time.time() - t0, scanned_files, len(by_work)))

            groups = _process_works(ctx, by_work, routes)
            compact_groups = _compact_groups(groups, max_items=400)

            share_info = routes["share"]["info"]
            kv_put(ctx, KV_GROUPS, compact_groups)
            kv_put(ctx, KV_META, {
                "state": "done",
                "updatedAt": "%s｜扫描 %d 个文件 / %d 个作品目录 / %d 组重复（极速直通）"
                             % (_now(), scanned_files, len(by_work), len(groups)),
                "scanned": scanned_files,
                "works": len(by_work),
                "ruleText": routes["statusText"],
                "weights": share_info.get("weights"),
            })
            kv_put(ctx, "_pending", [])

            keep = sum(1 for g in compact_groups if g.get("verdict") == "keep")
            tie = sum(1 for g in compact_groups if g.get("verdict") == "tie")
            ctx.mt.log(level="info", message="分析完成：%d 组重复（可洗版 %d，分差不足 %d）"
                       % (len(compact_groups), keep, tie))
            return {"waiting": False, "groups": len(compact_groups), "scanned": scanned_files}

        # ---------------- 宿主标准异步任务扫描 ---------------- #
        pending: List[Dict[str, Any]] = []
        for idx, sel in enumerate(sels):
            task_id = _start_scan(ctx, sel, idx, rid)
            if task_id:
                pending.append({
                    "taskId": task_id,
                    "rootId": sel.get("rootId"),
                    "path": sel.get("path"),
                    "ref": {"taskId": task_id, "rootId": sel.get("rootId"), "path": sel.get("path")},
                })

        if not pending:
            ctx.mt.log(level="error", message="所有目录提交扫描均失败")
            return {"waiting": False, "error": "提交扫描任务失败"}

        share_info = routes["share"]["info"]
        kv_put(ctx, "_pending", pending)
        kv_put(ctx, KV_META, {
            "state": "scanning",
            "updatedAt": "%s｜正在扫描 %d 个目录..." % (_now(), len(pending)),
            "scanned": 0,
            "works": 0,
            "ruleText": routes["statusText"],
            "weights": share_info.get("weights"),
            "ignores": ignores,
        })

        task_ids = [p["taskId"] for p in pending]
        ctx.mt.log(level="info", message="已下发 %d 个扫描任务，等待完成：%s"
                   % (len(task_ids), task_ids))

        try:
            ctx.mt.tasks.wait(
                task_ids=task_ids,
                continuation="dup-collect",
                timeout_seconds=600,
                all=True,
            )
        except Exception as exc:                               # noqa: BLE001
            ctx.mt.log(level="warn", message="挂载等待失败（%s: %s），改为立即收集" % (type(exc).__name__, exc))
            return self.collect({}, ctx)

        return {"waiting": True}

    # -------------------------- 续跑：收集结果 -------------------------- #
    @continuation("dup-collect")
    def collect(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        meta = kv_get(ctx, KV_META) or {}
        pending = kv_get(ctx, "_pending") or []
        if not pending:
            ctx.mt.log(level="warn", message="没有待收集的扫描任务")
            return {"waiting": False}

        ignores = meta.get("ignores") or []
        routes = _resolve_route_rules(ctx)

        scanned = 0
        by_work: Dict[str, List[Dict[str, Any]]] = {}
        for item in pending:
            ref = item.get("ref")
            file_items = _collect_files(ctx, ref, ignores)
            scanned += len(file_items)
            for it in file_items:
                rel = it["path"]
                name = it["name"]
                work = _work_dir_of(rel)
                if not work:
                    continue
                by_work.setdefault(work, []).append({
                    "rel": rel,
                    "name": name,
                    "ref": it.get("ref"),
                    "rootId": it.get("rootId"),
                })

        ctx.mt.log(level="info", message="收到 %d 个 strm，分布在 %d 个作品目录"
                   % (scanned, len(by_work)))

        groups = _process_works(ctx, by_work, routes)
        compact_groups = _compact_groups(groups, max_items=400)

        share_info = routes["share"]["info"]
        kv_put(ctx, KV_GROUPS, compact_groups)
        meta.update({
            "state": "done",
            "updatedAt": "%s｜扫描 %d 个文件 / %d 个作品目录 / %d 组重复"
                         % (_now(), scanned, len(by_work), len(groups)),
            "scanned": scanned,
            "works": len(by_work),
            "ruleText": routes["statusText"],
            "weights": share_info.get("weights"),
        })
        kv_put(ctx, KV_META, meta)
        kv_put(ctx, "_pending", [])

        keep = sum(1 for g in compact_groups if g.get("verdict") == "keep")
        tie = sum(1 for g in compact_groups if g.get("verdict") == "tie")
        ctx.mt.log(level="info", message="分析完成：%d 组重复（可洗版 %d，分差不足 %d）"
                   % (len(compact_groups), keep, tie))
        return {"waiting": False}


# --------------------------------------------------------------------------- #
# 作品处理与打分判定
# --------------------------------------------------------------------------- #
def _process_works(ctx: Context, by_work: Dict[str, List[Dict[str, Any]]],
                   routes: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    for work, entries in by_work.items():
        if len(entries) < 2:
            continue
        if not workscan.parse_work_dir(posixpath.basename(work)):
            continue

        # 动态获取匹配该作品目录的评分规则
        rules = _get_rules_for_work(work, routes)
        if not rules:
            continue

        is_tv = any(nameinfo.parse(e["name"]).get("episode") is not None for e in entries)

        if not is_tv:
            for e in entries:
                e["real_ext"] = _read_strm_real_ext(ctx, e)

        norm_files: List[workscan.ScannedFile] = []
        for e in entries:
            parsed = nameinfo.parse(e["name"])
            real_ext = e.get("real_ext")
            if real_ext:
                parsed["file_extension"] = real_ext

            sf = workscan.ScannedFile(
                path=e["rel"],
                name=e["name"],
                info=parsed,
                raw=e,
            )
            norm_files.append(sf)

        res = workscan.scan_work_directory(
            work_dir=work,
            files=norm_files,
            rules=rules,
            show_tie=True,
        )

        for g in res.groups:
            winner_dict = _compact_file(g.winner.to_dict() if g.winner else None)
            loser_dict = _compact_file(g.loser.to_dict() if g.loser else None)

            groups.append({
                "id": g.group_id,
                "work": work,
                "work_title": res.work_title,
                "work_year": res.work_year,
                "season": g.season,
                "episode": g.episode,
                "slot": g.slot,
                "verdict": g.verdict,
                "diff": round(g.diff, 2),
                "diff_text": g.diff_text,
                "winner": winner_dict,
                "loser": loser_dict,
                "candidates_count": len(g.candidates),
            })
    return groups


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _collect_files(ctx: Context, ref: Any,
                   ignores: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        try:
            got = ctx.mt.tasks.items(ref=ref, offset=offset, limit=limit)
        except Exception as exc:                               # noqa: BLE001
            ctx.mt.log(level="warn", message="读取扫描结果页失败 offset=%d: %s"
                       % (offset, exc))
            break
        items = got.get("items") or got.get("files") or [] if isinstance(got, dict) else []
        if not items:
            break
        for it in items:
            f = it.get("file") if isinstance(it, dict) and "file" in it else it
            if not isinstance(f, dict):
                continue
            path = f.get("path") or ""
            name = f.get("name") or posixpath.basename(path)
            if not name.endswith(".strm"):
                continue
            if _is_ignored(path, ignores):
                continue
            out.append({
                "path": path,
                "name": name,
                "ref": f.get("ref"),
                "rootId": f.get("rootId") or (ref.get("rootId") if isinstance(ref, dict) else None),
            })
        offset = got.get("nextOffset") if isinstance(got, dict) else None
        if not offset:
            break
    return out


def _read_strm_real_ext(ctx: Context, entry: Dict[str, Any]) -> str:
    full = entry.get("full")
    if full and os.path.isfile(full):
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(512)
            ext = nameinfo.extract_real_ext(content)
            if ext:
                return ext
        except Exception:                                      # noqa: BLE001
            pass

    ref = entry.get("ref")
    root_id = entry.get("rootId")
    rel = entry.get("rel")
    if ref or (root_id and rel):
        try:
            kwargs = {"length": 512, "offset": 0}
            if ref:
                kwargs["ref"] = ref
            else:
                kwargs["root_id"] = root_id
                kwargs["path"] = rel
            chunk = ctx.mt.files.read(**kwargs)
            if isinstance(chunk, dict):
                chunk = chunk.get("data") or chunk.get("content") or b""
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="ignore")
            content = chunk.decode("utf-8", errors="ignore")
            ext = nameinfo.extract_real_ext(content)
            if ext:
                return ext
        except Exception as exc:                               # noqa: BLE001
            ctx.mt.log(level="debug", message="读取 strm 扩展名失败 (%s): %s" % (rel, exc))
    return "strm"


def _work_dir_of(rel: str) -> Optional[str]:
    norm = rel.replace("\\", "/").strip("/")
    parts = norm.split("/")
    if len(parts) < 2:
        return None
    for i in range(len(parts) - 1, 0, -1):
        seg = parts[i - 1]
        if workscan.parse_work_dir(seg):
            return "/".join(parts[:i])
    return parts[0]


def _start_scan(ctx: Context, sel: Dict[str, str], idx: int, rid: str) -> Optional[str]:
    root_id = sel["rootId"]
    path = sel["path"]
    try:
        res = ctx.mt.files.scan(
            root_id=root_id,
            path=path,
            max_scan_files=50000,
            idempotency_key="%s-%s-%d" % (PLUGIN_ID, rid, idx),
        )
    except Exception as exc:                                   # noqa: BLE001
        ctx.mt.log(level="error", message="files.scan 提交失败 [%s:%s]：%s"
                   % (root_id, path, exc))
        return None
    task_id = res.get("taskId") or res.get("id") if isinstance(res, dict) else None
    if not task_id:
        ctx.mt.log(level="error", message="files.scan 未返回 taskId：%r" % res)
        return None
    ctx.mt.log(level="info", message="files.scan 任务已下发 [%s:%s] -> taskId=%s"
               % (root_id, path, task_id))
    return str(task_id)


def _compact_file(f: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not f:
        return None
    return {
        "rel": f.get("rel") or f.get("path") or "",
        "name": f.get("name") or "",
        "score": f.get("score", 0.0),
        "detail": f.get("detail") or "",
    }


def _compact_groups(groups: List[Dict[str, Any]], max_items: int = 400) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for g in groups[:max_items]:
        out.append({
            "id": g.get("id"),
            "work": g.get("work"),
            "work_title": g.get("work_title"),
            "work_year": g.get("work_year"),
            "season": g.get("season"),
            "episode": g.get("episode"),
            "slot": g.get("slot"),
            "verdict": g.get("verdict"),
            "diff": g.get("diff"),
            "diff_text": g.get("diff_text"),
            "winner": _compact_file(g.get("winner")),
            "loser": _compact_file(g.get("loser")),
            "candidates_count": g.get("candidates_count", 2),
        })
    return out


def _row(g: Dict[str, Any]) -> Dict[str, Any]:
    winner = g.get("winner") or {}
    loser = g.get("loser") or {}

    season = g.get("season")
    episode = g.get("episode")
    slot = g.get("slot")
    if season is not None and episode is not None:
        label = "S%02dE%02d" % (season, episode)
    elif slot:
        label = str(slot)
    else:
        label = "电影"

    title = g.get("work_title") or g.get("work") or ""
    year = g.get("work_year")
    if year:
        title = "%s (%s)" % (title, year)

    diff_val = g.get("diff", 0.0)
    diff_str = ("+%.2f" % diff_val) if diff_val > 0 else "0.00"

    w_score = winner.get("score")
    l_score = loser.get("score")
    w_label = winner.get("name") or winner.get("rel") or "—"
    l_label = loser.get("name") or loser.get("rel") or "—"
    if w_score is not None:
        w_label = "%s (%.2f分)" % (w_label, w_score)
    if l_score is not None:
        l_label = "%s (%.2f分)" % (l_label, l_score)

    return {
        "id": g.get("id") or "",
        "title": title,
        "label": label,
        "diff": diff_str,
        "diffText": g.get("diff_text") or ("分差 %s" % diff_str),
        "keep": w_label,
        "drop": l_label,
        "keepRel": winner.get("rel") or "",
        "dropRel": loser.get("rel") or "",
        "dir": _short_dir(g.get("work") or ""),
    }


def _short_dir(p: str) -> str:
    norm = p.replace("\\", "/").strip("/")
    parts = norm.split("/")
    if len(parts) <= 3:
        return norm
    return ".../" + "/".join(parts[-2:])
