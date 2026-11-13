# -*- coding: utf-8 -*-
"""片名全等比对、年份比对、分类冲突判定、混合链保护与重整建议。"""

import re
from typing import Dict, List, Any, Tuple
from .scanner import clean_title_invisible_chars, STANDARD_DIR_RE

TMDB_STYLES = {
    "style_curly": {
        "label": "【花括号标准】片名 (年份) {tmdb-ID}  (推荐)",
        "example": "茶之味 (2004) {tmdb-2002}",
        "pattern": re.compile(r"^(.+?)\s*\((19\d\d|20\d\d)\)\s*\{tmdb-(\d+)\}$", re.IGNORECASE),
        "format": lambda title, year, tid: f"{title} ({year}) {{tmdb-{tid}}}",
    },
    "style_bracket_tmdbid": {
        "label": "【方括号 tmdbid】片名 (年份) [tmdbid=ID]",
        "example": "茶之味 (2004) [tmdbid=2002]",
        "pattern": re.compile(r"^(.+?)\s*\((19\d\d|20\d\d)\)\s*\[tmdbid=(\d+)\]$", re.IGNORECASE),
        "format": lambda title, year, tid: f"{title} ({year}) [tmdbid={tid}]",
    },
    "style_bracket_dash": {
        "label": "【方括号 tmdb】片名 (年份) [tmdb-ID]",
        "example": "茶之味 (2004) [tmdb-2002]",
        "pattern": re.compile(r"^(.+?)\s*\((19\d\d|20\d\d)\)\s*\[tmdb-(\d+)\]$", re.IGNORECASE),
        "format": lambda title, year, tid: f"{title} ({year}) [tmdb-{tid}]",
    },
    "style_dot": {
        "label": "【点号后缀】片名 (年份).tmdb-ID",
        "example": "茶之味 (2004).tmdb-2002",
        "pattern": re.compile(r"^(.+?)\s*\((19\d\d|20\d\d)\)\.tmdb-(\d+)$", re.IGNORECASE),
        "format": lambda title, year, tid: f"{title} ({year}).tmdb-{tid}",
    },
    "style_no_year": {
        "label": "【无年份花括号】片名 {tmdb-ID}",
        "example": "茶之味 {tmdb-2002}",
        "pattern": re.compile(r"^(.+?)\s*\{tmdb-(\d+)\}$", re.IGNORECASE),
        "format": lambda title, year, tid: f"{title} {{tmdb-{tid}}}",
    },
}


STYLE_IDS = {"style_curly": "brace", "style_bracket_tmdbid": "bracket_id",
             "style_bracket_dash": "bracket_dash", "style_dot": "dot_suffix",
             "style_no_year": "brace_noyear"}
from .console_rules import build_target_dir_name, evaluate_dir_match as _console_evaluate


def format_target_name(title: str, year: Any, tid: Any, style_key: str = "style_curly") -> str:
    return build_target_dir_name(title, year, tid, STYLE_IDS.get(style_key, style_key)) or ""


def evaluate_dir_match(item: Dict[str, Any], latest_title: str, latest_year: Any,
                       target_style: str = "style_curly") -> Tuple[bool, bool, str]:
    return _console_evaluate(item, latest_title, latest_year,
                             STYLE_IDS.get(target_style, target_style),
                             item.get("expected_category"))


def match_duplicate_groups(
    groups: List[Dict[str, Any]],
    tmdb_caches: Dict[Any, Dict[str, Any]],
    local_profile_id: int = 0,
    local_dest_dir: str = "",
    cloud_profile_id: int = 0,
    cloud_dest_dir: str = "",
    target_style: str = "style_curly",
) -> List[Dict[str, Any]]:
    """对扫描出的电影与剧集同类同 ID 分组进行比对，
    严格区分电影与剧集，比较最新 TMDB 官方标题，依据选定风格进行命名校验，标记比对失败方与重新提交整理建议。
    """
    evaluated_groups = []

    for group in groups:
        tid = group["tmdb_id"]
        m_type = group.get("media_type", "tv")
        group_key = group.get("group_key") or f"{m_type}:{tid}"
        media_type_desc = group.get("media_type_desc") or ("🎬 电影" if m_type == "movie" else "📺 剧集")

        # 优先按 (media_type, tid) 精准匹配 TMDB 最新官方信息
        tmdb_info = tmdb_caches.get((m_type, tid)) or tmdb_caches.get(group_key) or tmdb_caches.get(tid, {})
        latest_title = tmdb_info.get("title", "")
        latest_year = tmdb_info.get("year")
        poster_path = tmdb_info.get("poster_path", "")

        dirs = group["dirs"]
        categories = sorted(set(d.get("category", "") for d in dirs))
        has_category_conflict = len(categories) > 1

        # 混合组等待后续逐文件评分；未经评分不允许目录级操作。
        is_group_mixed = group.get("is_mixed", False) or group.get("group_link_type") == "mixed"

        group_has_diff = False
        group_need_organize_dirs = 0

        evaluated_dirs = []
        for d in dirs:
            is_matched, need_organize, diff_reason = evaluate_dir_match(d, latest_title, latest_year, target_style)

            # Different categories are observable, not proof that either is wrong.
            # Only verified MT parse-test results may mark a category error.
            link_type = d.get("link_type", "other")
            is_mixed_link = (link_type == "mixed" or is_group_mixed)

            # 核心业务保护：唯一不处理的只有混合路径链接的分组，防止破坏现有挂载
            if is_mixed_link:
                disabled = True
                disabled_reason = "混合组待质量评分"
                diff_reason = "混合组待逐文件评分"
                need_organize = False  # 未评分不得自动整理
                compare_verdict = "待评分"
                assigned_profile_id = None
                assigned_dest_dir = None
                route_desc = "已锁定"
            else:
                disabled = False
                disabled_reason = ""
                # 比对判定结论：根据与最新 TMDB 官方标题比较的结果标记
                if need_organize:
                    compare_verdict = "❌ 比对失败（需重新提交整理）"
                else:
                    compare_verdict = "✅ 比对成功（符合最新标准）"

                # 根据 strm 内的真实链接形态指派对应的整理方案与目标路径
                # 二分核心契约：除明确以 http:// 或 https:// 开头的分享直链外，全部走云盘挂载方案
                if link_type == "http":
                    assigned_profile_id = local_profile_id
                    assigned_dest_dir = local_dest_dir
                    route_desc = f"🌐 HTTP直链 ➔ 方案 {local_profile_id}"
                else:
                    assigned_profile_id = cloud_profile_id
                    assigned_dest_dir = cloud_dest_dir
                    route_desc = f"☁️ 本地/云盘挂载 ➔ 方案 {cloud_profile_id}"

            if need_organize and not disabled:
                group_has_diff = True
                group_need_organize_dirs += 1

            # 预测重整后的标准目标规范名（根据用户选定的目标风格格式化）
            target_title = clean_title_invisible_chars(latest_title) if latest_title else clean_title_invisible_chars(d.get("local_title") or "")
            target_year = latest_year or d.get("year")
            standard_target_name = format_target_name(target_title, target_year, tid, target_style)

            evaluated_dirs.append({
                "dir_name": d["dir_name"],
                "dir_path": d["dir_path"],
                "category": d.get("category", "未分类"),
                "cat_status": d.get("cat_status", "unknown"),
                "has_cat_error": d.get("has_cat_error", False),
                "expected_category": d.get("expected_category", ""),
                "media_type": m_type,
                "media_type_desc": media_type_desc,
                "local_title": d["local_title"],
                "year": d.get("year"),
                "tmdb_id": tid,
                "link_type": link_type,
                "strm_count": d.get("strm_count", 0),
                "formats": list(d.get("formats") or []),
                "format_label": " / ".join(d.get("formats") or []) or d.get("format_label") or "未识别（地址无扩展名或旧扫描）",
                "strm_sample": d.get("strm_sample", ""),
                "cloudnas_path": d.get("cloudnas_path", ""),
                "is_matched": is_matched,
                "naming_need_organize": evaluate_dir_match(d, latest_title, latest_year, target_style)[1],
                "need_organize": need_organize,
                "compare_verdict": compare_verdict,
                "disabled": disabled,
                "disabled_reason": disabled_reason,
                "diff_reason": diff_reason,
                "standard_target_name": standard_target_name,
                "assigned_profile_id": assigned_profile_id,
                "assigned_dest_dir": assigned_dest_dir,
                "route_desc": route_desc,
            })

        # 只要组内存在需要重整的目录，或者组本身是重复的，就标记 group_has_diff
        if group.get("is_duplicate", False) and not is_group_mixed:
            group_has_diff = True

        status_text = "待重整" if (group_has_diff and not is_group_mixed) else ("混合链已保护" if is_group_mixed else "合规一致")

        full_official_title = f"{latest_title} ({latest_year})" if (latest_title and latest_year) else (latest_title or group["title"])

        evaluated_groups.append({
            "tmdb_id": tid,
            "media_type": m_type,
            "media_type_desc": media_type_desc,
            "group_key": group_key,
            "title": latest_title or group["title"],
            "year": latest_year or group.get("year"),
            "official_title": full_official_title,
            "poster_path": poster_path,
            "dirs": evaluated_dirs,
            "dirs_count": len(evaluated_dirs),
            "group_link_type": group.get("group_link_type", "other"),
            "is_mixed": is_group_mixed,
            "is_duplicate": group.get("is_duplicate", False),
            "has_diff": group_has_diff,
            "need_organize_count": group_need_organize_dirs,
            "status": status_text,
        })

    return evaluated_groups
