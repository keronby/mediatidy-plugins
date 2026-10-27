# -*- coding: utf-8 -*-
"""片名全等比对、年份比对、分类冲突判定与重整标记。"""

import re
from typing import Dict, List, Any, Tuple

from core.scanner import clean_title_invisible_chars


def evaluate_dir_match(
    item: Dict[str, Any],
    latest_title: str,
    latest_year: Any
) -> Tuple[bool, bool, str]:
    """比对单个目录的片名与年份是否与 TMDB 官方最新数据一致。
    返回: (is_matched: bool, need_organize: bool, diff_reason: str)
    """
    clean_local = clean_title_invisible_chars(item.get("local_title") or "")
    clean_latest = clean_title_invisible_chars(latest_title or "")
    local_year = item.get("year")

    # 如果 TMDB 没有最新标题，无法判定不一致，保持放行
    if not clean_latest:
        return True, False, ""

    reasons = []

    # 1. 严格全等比对片名
    if clean_local != clean_latest:
        reasons.append(f"片名不符 (当前为 '{clean_local}'，TMDB应为 '{clean_latest}')")

    # 2. 年份比对
    if latest_year is not None:
        try:
            target_year = int(latest_year)
            if local_year is None:
                reasons.append(f"年份缺失 (TMDB应为 {target_year})")
            elif int(local_year) != target_year:
                reasons.append(f"年份不符 (当前为 {local_year}，TMDB应为 {target_year})")
        except Exception:
            pass

    if reasons:
        return False, True, " · ".join(reasons)

    return True, False, ""


def match_duplicate_groups(
    groups: List[Dict[str, Any]],
    tmdb_caches: Dict[int, Dict[str, Any]],
    local_profile_id: int = 1359,
    local_dest_dir: str = "/strm/云盘影视",
    cloud_profile_id: int = 854,
    cloud_dest_dir: str = "/小小玻珠/云盘影视"
) -> List[Dict[str, Any]]:
    """对扫描出的重复组进行比对，标记差异与分流建议。"""
    evaluated_groups = []

    for group in groups:
        tid = group["tmdb_id"]
        tmdb_info = tmdb_caches.get(tid, {})
        latest_title = tmdb_info.get("title", "")
        latest_year = tmdb_info.get("year")
        poster_path = tmdb_info.get("poster_path", "")

        dirs = group["dirs"]
        categories = sorted(set(d.get("category", "") for d in dirs))
        has_category_conflict = len(categories) > 1

        group_has_diff = has_category_conflict
        group_need_organize_dirs = 0

        evaluated_dirs = []
        for d in dirs:
            is_matched, need_organize, diff_reason = evaluate_dir_match(d, latest_title, latest_year)

            # 分类冲突追加
            if has_category_conflict:
                other_cats = [c for c in categories if c != d.get("category")]
                conflict_msg = f"分类冲突 (本目录: {d.get('category')} ➜ 组内存在其他分类: {', '.join(other_cats)})"
                if diff_reason:
                    diff_reason = f"{diff_reason} · {conflict_msg}"
                else:
                    diff_reason = conflict_msg
                need_organize = True

            if need_organize:
                group_has_diff = True
                group_need_organize_dirs += 1

            # 判定分流与禁用
            link_type = d.get("link_type", "other")
            is_mixed = (link_type == "mixed" or group.get("group_link_type") == "mixed")
            disabled = is_mixed
            disabled_reason = "含混合链接(暂不支持自动处理)" if is_mixed else ""

            # 建议的 profile 和目标目录
            if link_type == "cloudnas":
                suggested_profile = cloud_profile_id
                suggested_dest = cloud_dest_dir
            else:
                suggested_profile = local_profile_id
                suggested_dest = local_dest_dir

            evaluated_dirs.append({
                "dir_name": d["dir_name"],
                "dir_path": d["dir_path"],
                "category": d.get("category", ""),
                "local_title": d.get("local_title", ""),
                "local_year": d.get("year"),
                "latest_title": latest_title,
                "latest_year": latest_year,
                "link_type": link_type,
                "cloudnas_path": d.get("cloudnas_path", ""),
                "strm_count": d.get("strm_count", 0),
                "is_matched": is_matched and not has_category_conflict,
                "need_organize": need_organize,
                "diff_reason": diff_reason,
                "disabled": disabled,
                "disabled_reason": disabled_reason,
                "suggested_profile_id": suggested_profile,
                "suggested_dest_dir": suggested_dest,
            })

        evaluated_groups.append({
            "tmdb_id": tid,
            "media_type": group.get("media_type", "tv"),
            "title": latest_title or dirs[0].get("local_title", ""),
            "year": latest_year or dirs[0].get("year"),
            "poster_path": poster_path,
            "group_link_type": group.get("group_link_type", "other"),
            "category_conflict": has_category_conflict,
            "has_diff": group_has_diff,
            "need_organize_count": group_need_organize_dirs,
            "dirs": evaluated_dirs,
        })

    return evaluated_groups
