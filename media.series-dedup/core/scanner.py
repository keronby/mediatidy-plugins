# -*- coding: utf-8 -*-
"""目录扫描、TMDB ID 提取与归组、STRM 链接属性分析。"""

import os
import re
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

TMDB_TAG_RE = re.compile(r"[\[{(（]\s*tmdb(?:id|_id)?\s*[-=_:\s]\s*(\d+)\s*[\]})）]", re.I)
YEAR_RE = re.compile(r"(?:[\[(（](\d{4})[\])）]|\b((?:19|20)\d{2})\b)")


def clean_title_invisible_chars(s: str) -> str:
    """清洗零宽不可见字符（\\u200b~\\u200f, \\ufeff 等脏字符）。"""
    if not s:
        return ""
    cleaned = re.sub(r"[\u200b-\u200f\ufeff\u2060\u202a-\u202e]", "", s)
    return cleaned.strip()


def resolve_local_path(path: Optional[str] = None) -> str:
    """路径映射解析：兼容宿主路径与容器路径。"""
    p = (path or "").strip()
    if not p:
        p = "/strm/云盘影视/电视剧"

    if os.path.exists(p):
        return p

    if p.startswith("/strm"):
        mapped = "/vol1/1000" + p
        if os.path.exists(mapped):
            return mapped

    if p.startswith("/vol1/1000/strm"):
        mapped = p[len("/vol1/1000"):]
        if os.path.exists(mapped):
            return mapped

    return p


def parse_media_dir(d_name: str) -> Optional[Dict[str, Any]]:
    """解析影视目录名称，提取纯标题、年份、TMDB ID。"""
    m = TMDB_TAG_RE.search(d_name)
    if not m:
        return None
    tmdb_id = int(m.group(1))
    prefix = d_name[:m.start()].strip(" .-_")

    year = None
    title = prefix

    # 1. 尝试匹配末尾的 (2023) 或 [2023]
    tail_year_m = re.search(r"[\s._-]*[\[(（](\d{4})[\])）]\s*$", prefix)
    if tail_year_m:
        year = int(tail_year_m.group(1))
        title = prefix[:tail_year_m.start()].strip(" .-_")
    else:
        # 2. 尝试匹配末尾的点分/空格年份，如 .2023
        tail_bare_year = re.search(r"[\s._-]+((?:19|20)\d{2})\s*$", prefix)
        if tail_bare_year:
            year = int(tail_bare_year.group(1))
            title = prefix[:tail_bare_year.start()].strip(" .-_")
        else:
            # 3. 扫描 prefix 中的任意年份
            all_years = list(YEAR_RE.finditer(prefix))
            if all_years:
                last_y = all_years[-1]
                year = int(last_y.group(1) or last_y.group(2))
                if last_y.start() > 1:
                    cand_title = prefix[:last_y.start()].strip(" .-_")
                    if cand_title:
                        title = cand_title

    if not title:
        title = prefix or d_name

    title = clean_title_invisible_chars(title)

    return {
        "title": title,
        "local_title": title,
        "year": year,
        "tmdb_id": tmdb_id,
    }


def analyze_strm_files(dir_path: str) -> Tuple[str, str, int]:
    """分析目录下的 .strm 文件，判断链接类型。
    返回: (link_type: 'http' | 'cloudnas' | 'mixed' | 'empty' | 'other', sample_cloudnas_path, strm_count)
    """
    types_found = set()
    sample_cloudnas_dir = ""
    strm_count = 0

    if not os.path.exists(dir_path):
        return "empty", "", 0

    for root, _, files in os.walk(dir_path):
        for f in files:
            if not f.lower().endswith(".strm"):
                continue
            strm_count += 1
            fp = os.path.join(root, f)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    first_line = (fh.readline() or "").strip()
            except Exception:
                continue

            if first_line.startswith("http://") or first_line.startswith("https://"):
                types_found.add("http")
            elif any(k in first_line for k in ["/cloudnas", "/vol1", "/115open", "CloudNAS"]):
                types_found.add("cloudnas")
                if not sample_cloudnas_dir:
                    # 提取云盘目录路径（去掉文件名）
                    parent = os.path.dirname(first_line)
                    sample_cloudnas_dir = parent
            else:
                types_found.add("other")

            # 采样优化：如果已经找到了不同类型（mixed），可提前返回
            if len(types_found) > 1:
                return "mixed", sample_cloudnas_dir, strm_count

    if not types_found:
        return "empty", "", strm_count
    if len(types_found) == 1:
        return list(types_found)[0], sample_cloudnas_dir, strm_count
    return "mixed", sample_cloudnas_dir, strm_count


def scan_media_library(
    root_path: str,
    stop_check_fn=None,
    progress_callback=None
) -> Dict[str, Any]:
    """遍历媒体库目录，按 TMDB ID 归组。
    返回:
      total_dirs_scanned: int
      groups: List[Dict] # 每个 group 包含 tmdb_id, dirs: List[Dict], group_link_type
    """
    resolved_root = resolve_local_path(root_path)
    if not os.path.exists(resolved_root):
        raise FileNotFoundError(f"扫描根路径不存在: {root_path} (解析为: {resolved_root})")

    # 判断类型：movie 还是 tv
    lower_path = resolved_root.lower()
    default_media_type = "movie" if any(k in lower_path for k in ["movie", "电影"]) else "tv"

    items_by_tid = defaultdict(list)
    total_dirs = 0

    # 遍历顶层分类或直接作品
    try:
        entries = sorted(os.listdir(resolved_root))
    except Exception as e:
        raise RuntimeError(f"读取目录失败: {resolved_root}, 错误: {e}")

    for entry in entries:
        if stop_check_fn and stop_check_fn():
            break
        entry_path = os.path.join(resolved_root, entry)
        if not os.path.isdir(entry_path):
            continue

        # 检查该 entry 本身是否就是作品目录（带有 tmdb 标签）
        parsed = parse_media_dir(entry)
        if parsed:
            total_dirs += 1
            items_by_tid[parsed["tmdb_id"]].append({
                "category": "根目录",
                "dir_name": entry,
                "dir_path": entry_path,
                "local_title": parsed["title"],
                "year": parsed["year"],
                "tmdb_id": parsed["tmdb_id"],
                "media_type": default_media_type,
            })
            continue

        # 否则该 entry 是分类目录（如 国产剧 / 日韩剧 等），遍历分类下的子目录
        category_name = entry
        try:
            subs = sorted(os.listdir(entry_path))
        except Exception:
            continue

        for sub in subs:
            if stop_check_fn and stop_check_fn():
                break
            sub_path = os.path.join(entry_path, sub)
            if not os.path.isdir(sub_path):
                continue

            sub_parsed = parse_media_dir(sub)
            if sub_parsed:
                total_dirs += 1
                items_by_tid[sub_parsed["tmdb_id"]].append({
                    "category": category_name,
                    "dir_name": sub,
                    "dir_path": sub_path,
                    "local_title": sub_parsed["title"],
                    "year": sub_parsed["year"],
                    "tmdb_id": sub_parsed["tmdb_id"],
                    "media_type": default_media_type,
                })
            else:
                # 递归第 3 层（如 分类 / 字母或子分类 / 作品）
                try:
                    deeper = sorted(os.listdir(sub_path))
                except Exception:
                    continue
                for d in deeper:
                    d_path = os.path.join(sub_path, d)
                    if not os.path.isdir(d_path):
                        continue
                    d_parsed = parse_media_dir(d)
                    if d_parsed:
                        total_dirs += 1
                        items_by_tid[d_parsed["tmdb_id"]].append({
                            "category": f"{category_name}/{sub}",
                            "dir_name": d,
                            "dir_path": d_path,
                            "local_title": d_parsed["title"],
                            "year": d_parsed["year"],
                            "tmdb_id": d_parsed["tmdb_id"],
                            "media_type": default_media_type,
                        })

    # 提取多目录重复组 (len >= 2)
    duplicate_groups = []
    single_dir_count = 0

    for tid, dirs in items_by_tid.items():
        if len(dirs) < 2:
            single_dir_count += 1
            continue

        # 对组内每个目录分析 STRM 链接属性
        group_link_types = set()
        for item in dirs:
            link_type, cloud_path, strm_cnt = analyze_strm_files(item["dir_path"])
            item["link_type"] = link_type
            item["cloudnas_path"] = cloud_path
            item["strm_count"] = strm_cnt
            group_link_types.add(link_type)

        # 判定组级别链接属性
        if "mixed" in group_link_types or len(group_link_types) > 1:
            group_link_type = "mixed"
        elif "cloudnas" in group_link_types:
            group_link_type = "cloudnas"
        elif "http" in group_link_types:
            group_link_type = "http"
        else:
            group_link_type = "other"

        duplicate_groups.append({
            "tmdb_id": tid,
            "media_type": dirs[0]["media_type"],
            "dirs": dirs,
            "group_link_type": group_link_type,
            "category_conflict": len(set(d["category"] for d in dirs)) > 1,
        })

    return {
        "root_path": root_path,
        "resolved_root": resolved_root,
        "total_dirs_scanned": total_dirs,
        "duplicate_groups_count": len(duplicate_groups),
        "single_dir_count": single_dir_count,
        "groups": duplicate_groups,
    }
