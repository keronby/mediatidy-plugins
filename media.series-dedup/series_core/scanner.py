# -*- coding: utf-8 -*-
"""影视目录极速扫描、TMDB ID 与年份解析、目录合规性判定及 STRM 属性分析。"""

import os
import re
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

# 全面适配各种 TMDB 标签写法：
# {tmdb-数字}, [tmdb=数字], [tmdbid-数字], [tmdbid=数字], (tmdb:数字), 【tmdb_数字】 等
from .console_rules import TMDB_TAG_RE, parse_media_dir as _console_parse

YEAR_RE = re.compile(r"(?:[\[(（【](\d{4})[\])）】]|\b((?:19|20)\d{2})\b)")

# 用户指定的标准合规目录正则：片名 (年份) {tmdb-数字}
STANDARD_DIR_RE = re.compile(r"^(.+?)\s*\(((?:19|20)\d{2})\)\s*\{tmdb-(\d+)\}$")

# 严谨的电视剧分季与特别篇子目录正则（防止 S4侠降魔记 等以S+数字开头的电影被误判为季目录）
SEASON_DIR_RE = re.compile(
    r"^(?:season[\s._-]*\d+|specials?|sp\d*|ova\d*|oad\d*|featurettes|s\d{1,2}(?:[\s\-_\.]|$)|cd[\s._-]*\d+|第\s*[0-9一二三四五六七八九十]+\s*[季部](?:[\s\-_\.]|$)|特别篇\d*)",
    re.I
)

# 常见剧集辅助目录（不含正片媒体，不应阻碍剧集作品目录判定）
AUXILIARY_DIR_RE = re.compile(
    r"^(?:subs|subtitles|extrafanart|extrathumbs|sample|samples|trailers?|behind the scenes|deleted scenes|scenes|interviews|shorts?)$",
    re.I
)

# 剧集集号特征正则（S01E01, 1x01, EP01, 第01集, SP01）
EPISODE_FILE_RE = re.compile(
    r"[Ss](\d{1,3})\s*[Ee](\d{1,5})|(?<!\d)(\d{1,2})x(\d{2,5})(?!\d)|(?<![A-Za-z0-9])[Ee][Pp]?(\d{1,5})(?![0-9A-Za-z])|第\s*(\d{1,5})\s*集|(?<![A-Za-z0-9])(?:SP|Specials?|特别篇)[\s._-]*\d+",
    re.I
)

# 已知分类目录库名称（绝对排除将其误判为作品）
KNOWN_CATEGORY_NAMES = {
    "电影", "电视剧", "动漫", "综艺", "纪录片",
    "华语电影", "外语电影", "日韩电影", "欧美电影", "特摄电影", "动画电影", "纪录电影", "港台电影",
    "国产剧", "日韩剧", "欧美剧", "港台剧", "特摄剧", "海外剧", "泰剧", "英剧",
    "国漫", "日番", "欧美漫", "儿童", "里番",
    "媒体库", "sharestrm", "strm",
}


def detect_media_type(full_path: str) -> str:
    """根据路径层级与特征智能判定是电影(movie)还是剧集(tv)。
    如果路径层级无法判定（例如直接挂载在通用媒体库根下），智能探查目录内容特征。
    """
    p = (full_path or "").replace("\\", "/").lower()
    # 路径中出现电影明确特征
    if any(k in p for k in [
        "/电影/", "/movies/", "/movie/", "/动画电影/", "/外语电影/",
        "/华语电影/", "/日韩电影/", "/特摄电影/", "/纪录电影/", "/r级电影/", "/演唱会/"
    ]):
        return "movie"
    # 路径中出现剧集/电视剧/动漫/综艺特征
    if any(k in p for k in [
        "/电视剧/", "/tv/", "/剧集/", "/series/", "/国产剧/", "/日韩剧/",
        "/欧美剧/", "/港台剧/", "/特摄剧/", "/纪录片/", "/综艺/",
        "/动漫/", "/anime/", "/国漫/", "/日番/", "/欧美漫/", "/儿童/"
    ]):
        return "tv"

    # 如果带有明确电影字样判为 movie
    if "电影" in p:
        return "movie"

    # 路径特征不明确时，探查真实存在的目录内容特征
    real_path = resolve_local_path(full_path)
    if os.path.isdir(real_path):
        try:
            sub_items = os.listdir(real_path)
            for it in sub_items:
                sub_fp = os.path.join(real_path, it)
                if os.path.isdir(sub_fp):
                    if SEASON_DIR_RE.match(it):
                        return "tv"
                elif it.lower().endswith((".strm", ".mkv", ".mp4", ".iso", ".ts")):
                    if EPISODE_FILE_RE.search(it):
                        return "tv"
            # 如果目录下只有视频文件且完全没有季集特征，倾向于 movie
            vid_files = [it for it in sub_items if it.lower().endswith((".strm", ".mkv", ".mp4", ".iso", ".ts"))]
            if vid_files and not any(EPISODE_FILE_RE.search(f) for f in vid_files):
                return "movie"
        except Exception:
            pass

    return "tv"


def clean_title_invisible_chars(s: str) -> str:
    """清洗零宽不可见字符（\u200b~\u200f, \ufeff 等脏字符）。"""
    if not s:
        return ""
    cleaned = re.sub(r"[\u200b-\u200f\ufeff\u2060\u202a-\u202e]", "", s)
    return cleaned.strip()


def resolve_local_path(path: Optional[str] = None) -> str:
    """Use the explicit mount unchanged; report missing paths to the caller."""
    return (path or "").strip()


def parse_media_dir(d_name: str) -> Dict[str, Any]:
    """Console parser with the v1.1.12 result fields retained for callers."""
    parsed = _console_parse(clean_title_invisible_chars(d_name))
    if parsed is None:
        return {"is_valid": False, "has_tmdb": False, "is_standard": False,
                "dir_name": d_name, "title": d_name, "year": None, "tmdb_id": None,
                "style_issue": "未标注 TMDB", "error_reason": "未标注 TMDB 标签"}
    standard = bool(STANDARD_DIR_RE.fullmatch(clean_title_invisible_chars(d_name)))
    return dict(parsed, is_valid=True, has_tmdb=True, is_standard=standard,
                dir_name=d_name, local_title=parsed["title"], error_reason="",
                style_issue="" if standard else "命名非标准规范")


def analyze_strm_files(dir_path: str, formats=None) -> Tuple[str, str, int, str]:
    """分析目录下的媒体或 .strm 文件，判断链接类型并抽样。
    规则严格遵从二分法则：
      1. 首行以 http:// 或 https:// 开头 ➔ 属于 'http'（分享直链）；
      2. 除 http 以外的所有路径（包含 /CloudNAS、/CloudNas、本地挂载绝对路径）或直接是实体视频文件 ➔ 全部属于 'cloudnas'（本地/云盘挂载链）；
      3. 同一目录下两者皆有 ➔ 属于 'mixed'；
      4. 既无视频也无 strm 的空壳目录 ➔ 属于 'empty'。
    返回: (link_type: 'http' | 'cloudnas' | 'mixed' | 'empty', sample_cloudnas_path, strm_count, sample_strm_line)
    """
    from urllib.parse import urlsplit, unquote
    if formats is None:
        formats = set()
    def note_format(value):
        ext = os.path.splitext(unquote(urlsplit(value).path).lower())[1]
        if ext in {".iso", ".mkv", ".mp4", ".ts", ".avi", ".mov", ".flv", ".wmv"}:
            formats.add(ext[1:].upper())
    types_found = set()
    sample_cloudnas_dir = ""
    sample_strm_line = ""
    strm_count = 0
    video_count = 0

    real_dir = resolve_local_path(dir_path)
    if not os.path.exists(real_dir):
        return "empty", "", 0, ""

    video_exts = {".mkv", ".mp4", ".ts", ".iso", ".avi", ".mov", ".flv", ".wmv"}

    for root, _, files in os.walk(real_dir):
        for f in files:
            lower_f = f.lower()
            if lower_f.endswith(".strm"):
                strm_count += 1
                fp = os.path.join(root, f)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        first_line = (fh.readline() or "").strip()
                except Exception:
                    continue

                note_format(first_line)
                if not sample_strm_line and first_line:
                    sample_strm_line = first_line

                # 二分法核心：只要不是 http 开头，就是本地/云盘路径
                if first_line.startswith("http://") or first_line.startswith("https://"):
                    types_found.add("http")
                elif first_line:
                    types_found.add("cloudnas")
                    if not sample_cloudnas_dir:
                        sample_cloudnas_dir = os.path.dirname(first_line)
            else:
                _, ext = os.path.splitext(lower_f)
                if ext in video_exts:
                    note_format(f)
                    video_count += 1
                    types_found.add("cloudnas")
                    if not sample_cloudnas_dir:
                        sample_cloudnas_dir = root


    if not types_found:
        return "empty", sample_cloudnas_dir, strm_count + video_count, sample_strm_line
    if len(types_found) == 1:
        return list(types_found)[0], sample_cloudnas_dir, strm_count + video_count, sample_strm_line
    return "mixed", sample_cloudnas_dir, strm_count + video_count, sample_strm_line


def scan_media_library(
    root_paths: Any,
    stop_check_fn=None,
    progress_callback=None,
    *, max_depth: int = 64, ignore_paths=None,
) -> Dict[str, Any]:
    """极速遍历媒体库目录，识别 TMDB ID、年份，识别非标与重复目录并按 TMDB 聚类。支持传入单路径或路径列表。"""
    if isinstance(root_paths, (list, tuple, set)):
        paths = [p for p in root_paths if p]
    elif isinstance(root_paths, str) and root_paths.strip():
        paths = [root_paths.strip()]
    else:
        paths = []

    if not paths:
        raise ValueError("未指定扫描目录")

    items_by_key = defaultdict(list)
    unidentified_dirs = []
    total_dirs = 0
    scan_errors = []
    visited_dirs = 0
    depth_limited_dirs = 0
    ignored = [os.path.abspath(p) for p in (ignore_paths or [])]
    visited = set()
    def record_error(path, exc):
        message = f"读取目录失败：{path}: {type(exc).__name__}: {exc}"
        scan_errors.append(message)
        if progress_callback:
            progress_callback({"phase": "warning", "message": message})

    def _inspect_entry(entry_name: str, entry_full_path: str, category_name: str):
        nonlocal total_dirs
        total_dirs += 1
        parsed = parse_media_dir(entry_name)

        if not parsed["is_valid"]:
            # 真正未标注 TMDB 的目录
            unidentified_dirs.append({
                "dir_name": entry_name,
                "dir_path": entry_full_path,
                "category": category_name,
                "reason": parsed["error_reason"],
                "tmdb_id": None,
                "year": None,
                "status": "unidentified",
                "disabled": True,
            })
            return

        m_type = detect_media_type(entry_full_path)
        items_by_key[(m_type, parsed["tmdb_id"])].append({
            "category": category_name,
            "dir_name": entry_name,
            "dir_path": entry_full_path,
            "local_title": parsed["title"],
            "year": parsed["year"],
            "tmdb_id": parsed["tmdb_id"],
            "is_standard": parsed.get("is_standard", False),
            "style_issue": parsed.get("style_issue", ""),
            "media_type": m_type,
            "media_type_desc": "🎬 电影" if m_type == "movie" else "📺 剧集",
        })

    for r_path in paths:
        if stop_check_fn and stop_check_fn():
            break
        resolved_root = resolve_local_path(r_path)
        if not os.path.isdir(resolved_root):
            scan_errors.append(
                f"扫描目录不存在：{r_path}（容器内解析为 {resolved_root}）。"
                "请填写容器内实际挂载路径（如 /strm/...），不是宿主机 NAS 路径。"
            )
            continue

        lower_path = resolved_root.lower()
        default_media_type = "movie" if any(k in lower_path for k in ["movie", "电影"]) else "tv"

        # 智能通用多层级作品扫描引擎：支持任意深度分类目录（如 媒体库 ➔ 电视剧 ➔ 国产剧 ➔ 作品）
        def _walk_category(curr_dir: str, depth: int, cat_name: str):
            nonlocal visited_dirs, depth_limited_dirs
            absolute = os.path.abspath(curr_dir)
            if any(absolute == p or absolute.startswith(p + os.sep) for p in ignored):
                return
            if os.path.islink(curr_dir) or absolute in visited:
                return
            visited.add(absolute)
            if stop_check_fn and stop_check_fn():
                return
            if depth > max_depth:
                depth_limited_dirs += 1
                scan_errors.append(f"达到扫描深度限制 {max_depth}：{curr_dir}")
                return

            try:
                dir_entries = sorted(os.listdir(curr_dir))
                visited_dirs += 1
                if progress_callback and (visited_dirs == 1 or visited_dirs % 100 == 0):
                    progress_callback({"phase": "scanning", "visited_dirs": visited_dirs,
                                       "recognized_works": total_dirs, "path": curr_dir})
            except OSError as exc:
                record_error(curr_dir, exc)
                return

            d_name = os.path.basename(curr_dir)
            clean_dname = d_name.strip().lower()

            # 0. 已知分类目录或根目录绝不作为单部作品判定
            is_known_category = (clean_dname in KNOWN_CATEGORY_NAMES)

            # 1a. 季目录本身绝不作为作品（v1.1.10）：Season 1 / S01 等无 TMDB 标签
            # 若走到这里直接返回，作品根由其父目录的 rule2 判定，避免行里出现 .../Season 1
            if depth > 0 and not is_known_category and not TMDB_TAG_RE.search(d_name) \
                    and SEASON_DIR_RE.match(d_name.strip()):
                return

            # 1. 如果当前目录名直接命中 TMDB 标签，且不是分类名，认定为独立影视作品目录！立即记录并停止向下深钻
            if not is_known_category and TMDB_TAG_RE.search(d_name):
                _inspect_entry(d_name, curr_dir, cat_name or d_name)
                return

            sub_dirs = []
            has_media = False
            for e in dir_entries:
                fp = os.path.join(curr_dir, e)
                if os.path.isdir(fp):
                    sub_dirs.append((e, fp))
                elif e.lower().endswith((".strm", ".mkv", ".mp4", ".iso", ".ts")):
                    has_media = True

            # 检查子目录中是否包含带有 TMDB 标签的独立子作品
            tmdb_child_count = sum(1 for s in sub_dirs if TMDB_TAG_RE.search(s[0]))

            # 过滤辅助目录（如 subs, subtitles, extrafanart）后检查分季目录
            content_sub_dirs = [s for s in sub_dirs if not AUXILIARY_DIR_RE.match(s[0].strip())]

            # 2. 子目录无 TMDB 独立作品且含季目录：当前目录即剧集作品根（v1.1.10 放宽）
            # 原要求"全部子目录都是季目录"，作品根下多一个 Extras/Subs 类目录就失效，
            # 于是继续深钻把 Season 1 当叶子误报。现允许非季目录仅为辅助目录。
            _AUX_EXTRA_RE = re.compile(
                r"^(?:extras?|subs?|subtitles?|extrafanart|extrathumbs|sample|samples|"
                r"trailers?|behind the scenes|deleted scenes|scenes|interviews|shorts?|"
                r"artwork|fanart|poster|posters|metadata|nfo|info|disc|bonus|specials?\s*material)$",
                re.I,
            )
            _non_season = [s for s in content_sub_dirs if not SEASON_DIR_RE.match(s[0])]
            _only_aux = all(_AUX_EXTRA_RE.match(s[0].strip()) for s in _non_season)
            if (not is_known_category and tmdb_child_count == 0
                and content_sub_dirs
                and any(SEASON_DIR_RE.match(s[0]) for s in content_sub_dirs)
                and _only_aux):
                _inspect_entry(d_name, curr_dir, cat_name or d_name)
                return

            # 3. 如果没有子目录（叶子目录）
            if not sub_dirs:
                if has_media and not is_known_category:
                    # 包含媒体文件但没有 TMDB 标签，记为真正未标注异常目录
                    _inspect_entry(d_name, curr_dir, cat_name or d_name)
                return

            # 4. 否则当前目录是分类目录（例如 华语电影、电视剧、国产剧 等），继续递归子目录
            curr_cat = cat_name if cat_name else (d_name if depth > 0 else "")
            for sub_name, sub_path in sub_dirs:
                if stop_check_fn and stop_check_fn():
                    break
                _walk_category(sub_path, depth + 1, curr_cat)

        _walk_category(resolved_root, 0, "")

    # 聚类结果组装：
    # 按照 (媒体类型 media_type, TMDB ID) 作为严格同类分组依据！
    # 电影与剧集严格分开，绝不混淆统计！
    groups = []
    for (m_type, tid), dir_list in items_by_key.items():
        if stop_check_fn and stop_check_fn():
            break

        # Media dedup compares distinct work directories, never files within one work.
        dir_list = list({os.path.normpath(os.path.abspath(d["dir_path"])): d for d in dir_list}.values())
        if len(dir_list) < 2:
            continue
        is_duplicate = True
        has_non_standard = any(not d.get("is_standard") for d in dir_list)

        # 抽样分析该组各个目录的 STRM 链接形态
        link_types = set()
        enhanced_dirs = []
        for d in dir_list:
            formats = set()
            l_type, c_path, s_cnt, s_sample = analyze_strm_files(d["dir_path"], formats)
            d["formats"] = sorted(formats)
            d["format_label"] = " / ".join(sorted(formats)) or "未知"
            d["link_type"] = l_type
            d["cloudnas_path"] = c_path
            d["strm_count"] = s_cnt
            d["strm_sample"] = s_sample
            link_types.add(l_type)
            enhanced_dirs.append(d)

        # 核心保护逻辑：如果同一组内存在混合链路（既有 http 分享又有 cloudnas），判定为 mixed
        is_mixed = ("mixed" in link_types) or ("http" in link_types and "cloudnas" in link_types)
        group_link_type = "mixed" if is_mixed else (list(link_types)[0] if link_types else "other")
        # A correctly named singleton may itself contain competing links. Inspect
        # contents before filtering; otherwise its mixed files are never scored.
        if not (is_duplicate or has_non_standard or is_mixed):
            continue

        rep_dir = enhanced_dirs[0]
        groups.append({
            "tmdb_id": tid,
            "media_type": m_type,
            "media_type_desc": "🎬 电影" if m_type == "movie" else "📺 剧集",
            "group_key": f"{m_type}:{tid}",
            "title": rep_dir["local_title"],
            "year": rep_dir.get("year"),
            "dirs": enhanced_dirs,
            "dirs_count": len(enhanced_dirs),
            "group_link_type": group_link_type,
            "is_mixed": is_mixed,
            "is_duplicate": is_duplicate,
            "has_non_standard": has_non_standard,
        })

    return {
        "groups": groups,
        "failed_dirs": unidentified_dirs,  # 真正未标注 TMDB 的目录
        "total_dirs_scanned": total_dirs,
        "scan_errors": scan_errors,
        "visited_dirs": visited_dirs,
        "recognized_works": total_dirs,
        "unidentified_count": len(unidentified_dirs),
        "depth_limited_dirs": depth_limited_dirs,
        "cancelled": bool(stop_check_fn and stop_check_fn()),
        "duplicate_groups_count": sum(1 for g in groups if g["is_duplicate"]),
    }
