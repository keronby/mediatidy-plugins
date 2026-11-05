# -*- coding: utf-8 -*-
"""比对引擎：把 CD2 目录树 → 作品 → 判定 hit/miss。

判定规则（契约 §2.1，2026-10-29 修订）：
  - 只有视频文件参与判定（is_video）
  - 作品内视频 **全部命中** → 整包删除
  - 任一视频未命中（或缺 sha1）→ 整包搬到目标目录
  - 附属文件（theme.mp3 / nfo / jpg / 字幕）不参与判定，随作品整体动作
  - 无视频的作品 → 标记 no-video，不动
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.library import Sha1Library
from core.works import WorkBuilder

VERDICT_DELETE = "delete"        # 整包删除
VERDICT_KEEP = "keep"            # 保留待 AI 识别（原地不动）
VERDICT_NO_VIDEO = "no-video"    # 无视频，不动
VERDICT_UNRECOGNIZED = "unrecognized"  # 结构无法识别，不动


def judge_works(works: List[Dict[str, Any]], lib: Sha1Library) -> List[Dict[str, Any]]:
    """给每个作品打判定，返回带 verdict 的作品列表。"""
    # 收集所有视频 sha1 一次性批量查询
    all_sha1: List[str] = []
    for w in works:
        for v in w["videos"]:
            if v.sha1:
                all_sha1.append(v.sha1)
    hit_map = lib.contains(all_sha1) if all_sha1 else {}

    out: List[Dict[str, Any]] = []
    for w in works:
        videos = w["videos"]
        vids = len(videos)
        if vids == 0:
            w["verdict"] = VERDICT_NO_VIDEO
            w["hit_count"] = 0
            w["total"] = 0
            w["hits"] = []
            w["misses"] = []
            out.append(w)
            continue

        hits, misses = [], []
        for v in videos:
            s = (v.sha1 or "").strip().lower()
            if s and hit_map.get(s):
                hits.append(v)
            else:
                misses.append(v)

        w["hit_count"] = len(hits)
        w["total"] = vids
        w["hits"] = hits
        w["misses"] = misses
        # 100% 命中才删；缺 sha1 视为未命中（安全侧）
        w["verdict"] = VERDICT_DELETE if len(misses) == 0 else VERDICT_KEEP
        out.append(w)
    return out


def summarize(works: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总统计。"""
    s = {
        "works": len(works),
        "delete_works": 0, "keep_works": 0, "no_video_works": 0, "unrecognized": 0,
        "delete_videos": 0, "keep_videos": 0,
        "hit_videos": 0, "miss_videos": 0, "no_sha1_videos": 0,
        "sidecars_delete": 0, "sidecars_keep": 0,
    }
    for w in works:
        v = w.get("verdict")
        if v == VERDICT_DELETE:
            s["delete_works"] += 1
            s["delete_videos"] += len(w["videos"])
            s["sidecars_delete"] += len(w["sidecars"])
        elif v == VERDICT_KEEP:
            s["keep_works"] += 1
            s["keep_videos"] += len(w["videos"])
            s["sidecars_keep"] += len(w["sidecars"])
        elif v == VERDICT_NO_VIDEO:
            s["no_video_works"] += 1
        else:
            s["unrecognized"] += 1
        s["hit_videos"] += w.get("hit_count", 0)
        # miss_videos 必须累加，否则 hit_rate 的分母只剩 hit_videos，恒为 100%
        s["miss_videos"] += len(w.get("misses") or [])
        for mv in w.get("misses") or []:
            if not mv.sha1:
                s["no_sha1_videos"] += 1
    total_v = s["hit_videos"] + s["miss_videos"]
    s["hit_rate"] = round(s["hit_videos"] / total_v * 100, 1) if total_v else 0.0
    return s


def mixed_works(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """混合作品：有命中也有未命中。这些按 100% 规则是 KEEP，但值得单独列出供复核。"""
    return [w for w in works
            if w.get("verdict") == VERDICT_KEEP and w.get("hit_count", 0) > 0]
