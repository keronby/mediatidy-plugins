# -*- coding: utf-8 -*-
"""MediaTidy 剧集查重（影视目录重整分流）插件入口。"""

from __future__ import annotations

import os
import sys
import logging
from typing import Dict, Any, List, Tuple, Optional

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from mt_plugin import Context, Plugin as PluginBase, action, query, ui
from core.scanner import scan_media_library, resolve_local_path
from core.tmdb import query_mt_tmdb_caches, refresh_tmdb_batch, MTClient
from core.matcher import match_duplicate_groups
from core.submitter import submit_tasks

logger = logging.getLogger("media-series-dedup")

# 存储键
STORAGE_KEY_CONFIG = "series_dedup_config"
STORAGE_KEY_STATE = "series_dedup_state"
STORAGE_KEY_LOGS = "series_dedup_logs"
STORAGE_KEY_FULL_GROUPS = "series_dedup_full_groups"

DEFAULT_CONFIG = {
    "debugMode": True,  # 默认只读调试模式
    "rootPath": "/strm/云盘影视/电视剧",
    "localProfileId": "1359",
    "localDestDir": "/strm/云盘影视",
    "cloudProfileId": "854",
    "cloudDestDir": "/小小玻珠/云盘影视",
    "forceRefreshTmdb": True,
}


def _get_config(ctx: Context) -> Dict[str, Any]:
    cfg = ctx.mt.storage.get(STORAGE_KEY_CONFIG)
    if isinstance(cfg, dict):
        res = dict(DEFAULT_CONFIG)
        res.update(cfg)
        return res
    return dict(DEFAULT_CONFIG)


def _get_state(ctx: Context) -> Dict[str, Any]:
    state = ctx.mt.storage.get(STORAGE_KEY_STATE)
    if isinstance(state, dict):
        return state
    return {
        "status": "idle",
        "total_dirs_scanned": 0,
        "duplicate_groups_count": 0,
        "diff_groups_count": 0,
        "category_conflict_count": 0,
        "mixed_groups_count": 0,
        "need_organize_dirs_count": 0,
        "groups": [],
        "diff_dirs": [],
        "last_scan_time": "",
    }


def _compact_for_storage(evaluated_groups: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """精炼数据结构，避免超过 MT Storage 256 KiB 限制。"""
    compact_groups = []
    diff_dirs = []

    for g in evaluated_groups[:100]:
        compact_groups.append({
            "tmdb_id": g["tmdb_id"],
            "title": g["title"],
            "year": g.get("year"),
            "group_link_type": g["group_link_type"],
            "has_diff": g["has_diff"],
            "category_conflict": g["category_conflict"],
            "dirs_count": len(g.get("dirs", [])),
            "need_organize_count": g.get("need_organize_count", 0),
        })

        for d in g.get("dirs", []):
            if d.get("need_organize") or g.get("has_diff"):
                diff_dirs.append({
                    "tmdb_id": g["tmdb_id"],
                    "category": d.get("category"),
                    "dir_name": d.get("dir_name"),
                    "local_title": d.get("local_title"),
                    "latest_title": d.get("latest_title"),
                    "local_year": d.get("local_year"),
                    "latest_year": d.get("latest_year"),
                    "link_type": d.get("link_type"),
                    "diff_reason": d.get("diff_reason") or "无需重整",
                    "need_organize": d.get("need_organize", False),
                    "disabled": d.get("disabled", False),
                    "suggested_profile_id": d.get("suggested_profile_id"),
                    "suggested_dest_dir": d.get("suggested_dest_dir"),
                })
                if len(diff_dirs) >= 300:
                    break

    return compact_groups, diff_dirs


class Plugin(PluginBase):
    """剧集查重（影视目录重整分流）插件。"""

    def on_init(self, ctx: Context) -> None:
        logger.info("media.series-dedup 插件已初始化")

    def get_page(self, ctx: Context):
        cfg = _get_config(ctx)
        state = _get_state(ctx)

        is_debug = cfg.get("debugMode", True)

        top_banner = ui.Alert(
            text="当前处于【只读调试模式】：提交整理操作仅记录模拟日志，绝不会实际调用 MT 变更接口或修改文件。可安全测试全库比对。",
            tone="warning"
        ) if is_debug else ui.Alert(
            text="【实机运行模式】：提交整理操作将真实分流投递到 MT 整理任务中！",
            tone="danger"
        )

        overview_section = ui.Section(
            ui.Text(text=f"最后扫描时间: {state.get('last_scan_time') or '尚未扫描'}"),
            ui.Card(
                ui.Text(text=f"• 扫描作品目录总数: {state.get('total_dirs_scanned', 0)}"),
                ui.Text(text=f"• 发现重复作品组 (≥2目录): {state.get('duplicate_groups_count', 0)} 组"),
                ui.Text(text=f"• 存在片名/年份差异作品: {state.get('diff_groups_count', 0)} 组"),
                ui.Text(text=f"• 存在分类冲突作品: {state.get('category_conflict_count', 0)} 组"),
                ui.Text(text=f"• 混合链接作品 (自动拦截保护): {state.get('mixed_groups_count', 0)} 组"),
                ui.Text(text=f"• 待重整目录总计: {state.get('need_organize_dirs_count', 0)} 个"),
                title="统计指标",
            ),
            title="查重与比对概览",
        )

        action_buttons = ui.Section(
            ui.Button(
                label="开始扫描与比对",
                variant="primary",
                onClick=[
                    {"type": "invoke", "actionId": "scan", "input": {}},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            ui.Button(
                label="强制刷新 TMDB 缓存",
                variant="outline",
                onClick=[
                    {"type": "invoke", "actionId": "refresh-tmdb", "input": {}},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            ui.Button(
                label="模拟/执行整理投递",
                variant="outline",
                onClick=[
                    {"type": "invoke", "actionId": "submit-organize", "input": {}},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            ui.Button(
                label="清空扫描数据",
                variant="ghost",
                onClick=[
                    {"type": "invoke", "actionId": "clear-history", "input": {}},
                    {"type": "query", "queryId": "status"}
                ]
            ),
            ui.Button(
                label="刷新页面",
                variant="ghost",
                onClick=[
                    {"type": "query", "queryId": "status"}
                ]
            ),
            title="操作控制台",
        )

        def tcol(title: str, field: str):
            return {
                "label": title,
                "field": field,
                "cells": [ui.Text(text=ui.ref("row." + field))]
            }

        groups_table = ui.Table(
            source=ui.ref("query.status.state.groups"),
            rowKey="tmdb_id",
            columns=[
                tcol("TMDB ID", "tmdb_id"),
                tcol("作品名称", "title"),
                tcol("年份", "year"),
                tcol("链接形态", "group_link_type"),
                tcol("目录数量", "dirs_count"),
                tcol("待重整目录", "need_organize_count"),
                tcol("分类冲突", "category_conflict"),
            ]
        )

        diff_table = ui.Table(
            source=ui.ref("query.status.state.diff_dirs"),
            rowKey="dir_name",
            columns=[
                tcol("TMDB ID", "tmdb_id"),
                tcol("所属分类", "category"),
                tcol("目录名称", "dir_name"),
                tcol("本地片名", "local_title"),
                tcol("TMDB最新", "latest_title"),
                tcol("本地年份", "local_year"),
                tcol("TMDB年份", "latest_year"),
                tcol("链接类型", "link_type"),
                tcol("差异与判定", "diff_reason"),
                tcol("建议方案", "suggested_profile_id"),
            ]
        )

        return ui.page(
            top_banner,
            overview_section,
            action_buttons,
            ui.Section(groups_table, title="重复作品组 (≥2 目录)"),
            ui.Section(diff_table, title="待重整目录明细与差异比对"),
            queries=[
                {"id": "status", "name": "status"},
                {"id": "profiles", "name": "profiles"},
            ]
        )

    # ── 查询 Queries ─────────────────────────────────────────────

    @query("status")
    def status(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        state = _get_state(ctx)
        cfg = _get_config(ctx)
        return {
            "config": cfg,
            "state": state,
        }

    @query("profiles")
    def profiles(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        try:
            cli = MTClient()
            res = cli.request("/api/organize/profiles", method="GET")
            data = res.get("data") or []
            p_list = [{"id": str(p.get("id")), "name": p.get("name")} for p in data]
            return {"profiles": p_list}
        except Exception as e:
            logger.warning("获取整理方案失败: %s", e)
            return {"profiles": []}

    # ── 动作 Actions ─────────────────────────────────────────────

    @action("scan")
    def scan(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _get_config(ctx)
        root = (input.get("rootPath") or cfg.get("rootPath") or "").strip()
        if not root:
            root = DEFAULT_CONFIG["rootPath"]

        ctx.mt.log(level="info", message=f"开始扫描媒体库: {root}")
        scan_res = scan_media_library(root)
        groups = scan_res.get("groups", [])

        tmdb_ids = [g["tmdb_id"] for g in groups]
        caches = query_mt_tmdb_caches(tmdb_ids)

        if cfg.get("forceRefreshTmdb") and tmdb_ids:
            ctx.mt.log(level="info", message=f"比对前强制同步 TMDB 官方缓存，共 {len(tmdb_ids)} 个条目")
            refreshed = refresh_tmdb_batch(tmdb_ids)
            caches.update(refreshed)

        evaluated = match_duplicate_groups(
            groups,
            caches,
            local_profile_id=int(cfg.get("localProfileId", 1359)),
            local_dest_dir=cfg.get("localDestDir", "/strm/云盘影视"),
            cloud_profile_id=int(cfg.get("cloudProfileId", 854)),
            cloud_dest_dir=cfg.get("cloudDestDir", "/小小玻珠/云盘影视"),
        )

        diff_groups_cnt = sum(1 for g in evaluated if g.get("has_diff"))
        cat_conflict_cnt = sum(1 for g in evaluated if g.get("category_conflict"))
        mixed_cnt = sum(1 for g in evaluated if g.get("group_link_type") == "mixed")
        need_org_dirs_cnt = sum(g.get("need_organize_count", 0) for g in evaluated)

        compact_groups, diff_dirs = _compact_for_storage(evaluated)

        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        new_state = {
            "status": "done",
            "total_dirs_scanned": scan_res.get("total_dirs_scanned", 0),
            "duplicate_groups_count": len(evaluated),
            "diff_groups_count": diff_groups_cnt,
            "category_conflict_count": cat_conflict_cnt,
            "mixed_groups_count": mixed_cnt,
            "need_organize_dirs_count": need_org_dirs_cnt,
            "groups": compact_groups,
            "diff_dirs": diff_dirs,
            "last_scan_time": now_str,
        }

        ctx.mt.storage.set(STORAGE_KEY_STATE, new_state)
        ctx.mt.storage.set(STORAGE_KEY_FULL_GROUPS, evaluated[:150])

        ctx.mt.log(level="info", message=f"扫描完成: 发现 {len(evaluated)} 组重复，{diff_groups_cnt} 组差异，待重整 {need_org_dirs_cnt} 个目录")
        return {
            "total_dirs": scan_res.get("total_dirs_scanned", 0),
            "duplicate_groups": len(evaluated),
            "diff_groups": diff_groups_cnt,
        }

    @action("refresh-tmdb")
    def refresh_tmdb(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        raw_ids = (input.get("tmdbIds") or "").strip()
        state = _get_state(ctx)

        if raw_ids:
            ids_to_refresh = [int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit()]
        else:
            ids_to_refresh = [g["tmdb_id"] for g in state.get("groups", [])]

        if not ids_to_refresh:
            return {"refreshed": 0}

        ctx.mt.log(level="info", message=f"执行手动强制刷新 TMDB 缓存: {len(ids_to_refresh)} 条")
        res = refresh_tmdb_batch(ids_to_refresh)
        return {"refreshed": len(res)}

    @action("submit-organize")
    def submit_organize(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _get_config(ctx)
        is_dry_run = cfg.get("debugMode", True)

        if input.get("forceDryRun") is not None:
            is_dry_run = bool(input.get("forceDryRun"))

        evaluated = ctx.mt.storage.get(STORAGE_KEY_FULL_GROUPS) or []
        if not evaluated:
            return {"submitted": 0, "simulated": 0, "skipped": 0}

        logs = []
        res = submit_tasks(
            evaluated,
            dry_run=is_dry_run,
            log_callback=lambda m: (logs.append(m), ctx.mt.log(level="info", message=m))
        )

        ctx.mt.storage.set(STORAGE_KEY_LOGS, logs[-100:])
        return {
            "submitted": res.get("submitted", 0),
            "simulated": res.get("simulated", 0),
            "skipped": res.get("skipped", 0),
        }

    @action("stop")
    def stop(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        return {}

    @action("clear-history")
    def clear_history(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        ctx.mt.storage.delete(STORAGE_KEY_STATE)
        ctx.mt.storage.delete(STORAGE_KEY_FULL_GROUPS)
        ctx.mt.storage.delete(STORAGE_KEY_LOGS)
        ctx.mt.log(level="info", message="已清空所有剧集查重扫描历史与日志")
        return {}

    @action("save-config")
    def save_config(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _get_config(ctx)
        for k in DEFAULT_CONFIG.keys():
            if k in input and input[k] is not None:
                cfg[k] = input[k]
        ctx.mt.storage.set(STORAGE_KEY_CONFIG, cfg)
        ctx.mt.log(level="info", message="剧集查重配置已保存")
        return {}
