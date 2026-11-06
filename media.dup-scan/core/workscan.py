# -*- coding: utf-8 -*-
"""在**单个 tmdb 作品目录内部**找重复的 strm，并按 MT 规则判胜负。

范围与边界（都已和用户确认）：
  * 只处理「同一个作品目录」内部的重复。跨目录的同 tmdb 重复
    （如「乘风」vs「乘风破浪的姐姐」）不在本插件职责内。
  * 连集包（S01E01-E08）与单集（S01E01）的重叠**不算重复**，
    直接跳过、也不在结果里显示 —— 涉及这种重叠的整个集号都跳过，
    因为「删掉连集包」会连带丢掉后面几集。
  * 电影：同一作品目录下有多个 strm 就是一组重复。
  * 剧集：按 (季, 集) 分组，同一集有多个 strm 才是重复。

胜负判定：
  * 每个文件按 MT 生效规则打分，分高者胜。
  * 分差 < 规则的 min_score_diff 时**不判胜负**（标 tie），
    避免因为解析不到的细节把好版本删掉。
  * MT 规则 `version.slots` 里的槽位（如 ISO）只用于识别「这是另一种载体」，
    同一槽位内的文件才互相比较。

本模块纯逻辑，不碰宿主 SDK，可在本地目录上直接跑。
"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import nameinfo, scoring

# 作品目录判定：目录名里带 {tmdb-123} 或 [tmdbid=123]
# 方括号与花括号两种命名风格均兼容识别
_RE_TMDB_DIR = re.compile(r'[\[{]\s*tmdb(?:id)?\s*[-=:]\s*(\d+)\s*[\]}]', re.I)


def parse_work_dir(dirname: str) -> Optional[Dict[str, Any]]:
    """从一个目录名解析出作品标识。

    `重案行动组 (1996) {tmdb-10723}` → {tmdb: 10723, title: 重案行动组, year: 1996}
    解析不到 tmdb 返回 None。
    """
    m = _RE_TMDB_DIR.search(dirname or "")
    if not m:
        return None
    title = _RE_TMDB_DIR.sub("", dirname).strip()
    year = None
    ym = re.search(r'\((\d{4})\)', title)
    if ym:
        year = int(ym.group(1))
        title = title.replace(ym.group(0), "").strip()
    return {"tmdb": int(m.group(1)), "title": title or dirname, "year": year}


def _slot_of(info: Dict[str, Any], slots: List[Dict[str, Any]]) -> str:
    """判断一个文件落在哪个版本槽位（如 ISO / normal / default）。"""
    ext = (info.get("fileExtension") or info.get("file_extension") or "").lower().lstrip(".")
    res = (info.get("resolution") or "").lower()

    for slot in slots or []:
        name = slot.get("name") or "?"
        match = slot.get("match") or {}

        # 1. 匹配文件后缀（如 ISO）
        exts = [str(x).lower().lstrip(".") for x in (match.get("file_extension") or match.get("fileExtension") or [])]
        if exts and ext in exts:
            return str(name)

        # 2. 匹配分辨率（如 normal 槽位）
        resolutions = [str(x).lower() for x in (match.get("resolution") or match.get("resolutions") or [])]
        if resolutions and res in resolutions:
            return str(name)

        # 3. 槽位名带 ISO/原盘，且后缀是 iso/bdmv
        if name.upper() in ("ISO", "BDMV", "原盘") and ext in ("iso", "bdmv"):
            return str(name)

    # 兜底：如果规则里没有显式定义 ISO 槽位，但文件源后缀确实是 iso，自动作为独立 ISO 槽位隔离
    if ext == "iso":
        return "ISO"
    return "default"


def _attach_snake(info: Dict[str, Any]) -> Dict[str, Any]:
    """把 nameinfo.parse 的驼峰键补一份蛇形，供 scoring 使用。"""
    out = dict(info)
    out["video_codec"] = info.get("videoCodec")
    out["audio_codec"] = info.get("audioCodec")
    out["color_depth"] = info.get("colorDepth")
    out["frame_rate"] = info.get("frameRate")
    out["file_extension"] = info.get("fileExtension")
    return out


class _File:
    __slots__ = ("rel", "name", "info", "score", "why", "slot")

    def __init__(self, rel: str, name: str, info: Dict[str, Any],
                 score: float, why: List[str], slot: str):
        self.rel, self.name, self.info = rel, name, info
        self.score, self.why, self.slot = score, why, slot


def _score_files(rels: List[str], rules: Dict[str, Any],
                 names: Optional[Dict[str, str]] = None,
                 ext_map: Optional[Dict[str, str]] = None,
                 info_map: Optional[Dict[str, Dict[str, Any]]] = None) -> List[_File]:
    """给一组文件打分。

    names 是由 rel → 文件名 的映射；缺省时用 rel 的 basename。
    ext_map 是由 rel → 真实扩展名 的映射（从 strm 内容提取）。
    info_map 是由 rel → 预解析 info 的映射（调用方已 parse 过时复用，
    避免候选文件重复 full parse）；缺失条目回退原 parse 路径。
    """
    slots = rules.get("slots") or []
    names = names or {}
    ext_map = ext_map or {}
    info_map = info_map or {}
    out: List[_File] = []
    for rel in rels:
        name = names.get(rel) or posixpath.basename(rel)
        real_ext = ext_map.get(rel)
        cached = info_map.get(rel)
        if cached is not None and cached.get("_realExt") == (real_ext or ""):
            info = {k: v for k, v in cached.items() if not k.startswith("_")}
        else:
            info = nameinfo.parse(name, real_ext=real_ext)
        sn = _attach_snake(info)
        sc, why = scoring.score(sn, rules)
        slot = _slot_of(info, slots)
        if real_ext and real_ext != "strm":
            why.append("真实后缀: %s" % real_ext)
        out.append(_File(rel, name, sn, sc, why, slot))
    return out


def _decide(files: List[_File], min_diff: float) -> Tuple[Optional[_File], Optional[_File], str, float]:
    """返回 (保留, 删除, 结论, 分差)。

    结论取值：
      keep  —— 有明确赢家，删 loser
      tie   —— 分差不足，不判胜负
      same  —— 内容完全等价（各维度都一样），建议人工确认
    """
    if len(files) < 2:
        return None, None, "none", 0.0
    ordered = sorted(files, key=lambda f: -f.score)
    best, second = ordered[0], ordered[1]
    diff = best.score - second.score

    # info 已经在 _attach_snake 里补过蛇形键，这里必须用蛇形，
    # 否则每一项都取到 None，所有组都会被误判成「完全等价」。
    same = all(best.info.get(k) == second.info.get(k) for k in
               ("resolution", "video_codec", "hdr", "bitrate", "audio_codec", "source"))
    # 至少要有一个维度是真读到的，否则「全都读不到」也会被当成 same
    known = [k for k in ("resolution", "video_codec", "hdr", "bitrate")
             if best.info.get(k) is not None]
    if not known:
        same = False
    if same and abs(diff) < 1e-9:
        return best, second, "same", diff
    if diff >= min_diff:
        return best, second, "keep", diff
    return best, second, "tie", diff


def scan_work(work_dir: str, entries: Iterable[Dict[str, Any]],
              rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    """扫描一个作品目录，返回重复组列表。

    entries：该作品目录下的文件条目，每条至少要有 {"rel": 相对作品目录的路径,
             "name": 文件名}，且都已确认为 .strm。
    """
    min_diff = float(rules.get("min_score_diff") or 0)
    work = parse_work_dir(posixpath.basename(work_dir.rstrip("/"))) or {}

    # 按 (槽位, 季, 集) 或 (槽位, 单片) 分组
    # 单遍 parse：先全部 parse 并缓存，再由缓存判定 is_tv，避免 is_tv
    # 预检 + 分组循环两次 full parse（候选作品集多时是主要 CPU 成本）。
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}

    info_map: Dict[str, Dict[str, Any]] = {}
    parsed: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for e in entries:
        name = e["name"]
        real_ext = e.get("realExt")
        info = nameinfo.parse(name, real_ext=real_ext)
        info_map[e["rel"]] = dict(info, _realExt=real_ext or "")
        parsed.append((e, info))
    is_tv = any(info.get("episode") is not None for _, info in parsed)

    for e, info in parsed:
        slot = _slot_of(info, rules.get("slots") or [])
        ep = info.get("episode")
        season = info.get("season")

        if is_tv:
            if ep is None:
                continue          # 剧集目录里没有集号的文件不参与查重
            if info.get("isMulti"):
                # 连集包：不参与比较，也不显示（用户明确要求）
                continue
            key = (slot, season, ep)
        else:
            key = (slot,)
        groups.setdefault(key, []).append(e)

    out: List[Dict[str, Any]] = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        rels = [it["rel"] for it in items]
        names = {it["rel"]: (it.get("name") or it["rel"]) for it in items}
        ext_map = {it["rel"]: it.get("realExt") for it in items if it.get("realExt")}
        files = _score_files(rels, rules, names, ext_map=ext_map, info_map=info_map)
        keep, drop, verdict, diff = _decide(files, min_diff)
        if verdict == "none":
            continue

        slot = key[0]
        label = ("S%02dE%02d" % (key[1] or 0, key[2] or 0)) if is_tv and len(key) == 3 else "单片"

        out.append({
            "workDir": work_dir,
            "tmdb": work.get("tmdb"),
            "title": work.get("title"),
            "year": work.get("year"),
            "kind": "tv" if is_tv else "movie",
            "slot": slot,
            "label": label,
            "season": key[1] if is_tv and len(key) == 3 else None,
            "episode": key[2] if is_tv and len(key) == 3 else None,
            "verdict": verdict,
            "minDiff": min_diff,
            "diff": round(diff, 2),
            "keep": _file_dict(keep) if keep else None,
            "drop": _file_dict(drop) if drop else None,
            "files": [_file_dict(f) for f in sorted(files, key=lambda f: -f.score)],
        })
    out.sort(key=lambda g: (g["kind"], g.get("season") or 0, g.get("episode") or 0))
    return out


def _file_dict(f: Optional[_File]) -> Optional[Dict[str, Any]]:
    if f is None:
        return None
    return {
        "rel": f.rel,
        "name": f.name,
        "score": round(f.score, 2),
        "why": f.why,
        "slot": f.slot,
        "fileExtension": f.info.get("fileExtension"),
        "resolution": f.info.get("resolution"),
        "videoCodec": f.info.get("videoCodec"),
        "hdr": f.info.get("hdr"),
        "bitrate": f.info.get("bitrate"),
        "source": f.info.get("source"),
        "audioCodec": f.info.get("audioCodec"),
        "audioChannels": f.info.get("audioChannels"),
        "colorDepth": f.info.get("colorDepth"),
        "frameRate": f.info.get("frameRate"),
    }


def summarize(groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """统计汇总，给 UI 用。"""
    wash = [g for g in groups if g["verdict"] == "keep"]
    tie = [g for g in groups if g["verdict"] == "tie"]
    same = [g for g in groups if g["verdict"] == "same"]
    return {
        "groups": len(groups),
        "washGroups": len(wash),
        "tieGroups": len(tie),
        "sameGroups": len(same),
        "washFiles": len(wash),
        "movies": sum(1 for g in groups if g["kind"] == "movie"),
        "tv": sum(1 for g in groups if g["kind"] == "tv"),
    }
