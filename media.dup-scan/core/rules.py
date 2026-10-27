# -*- coding: utf-8 -*-
"""把 MT 的质量评分规则翻译成这个插件能算的形式。

为什么不直接吃 MT 的规则对象：
  MT 下发的 scoring 是**驼峰**键（videoCodec / colorDepth / fileExtension /
  videoBitrate …），而且维度比我们能从**文件名**里读出来的多。
  如果直接混用，不认识的键会被静默忽略，权重合计悄悄变少，
  最后算出来的分和 MT 整理时的判断对不上 —— 这是最坏的情况。

所以这里做三件事：
  1. 驼峰 → 蛇形归一，并建立别名表（audioCodec 就是从文件名读不到的）。
  2. **每个维度必须有归属**：能算的 / 算不了的 / MT 里已禁用的，
     一个都不许静默丢掉，上层要把「算不了」吼给用户看。
  3. 权重保持 MT 的原始数值，不归一化、不补齐 ——
     分差要和 MT 的 min_score_diff 同一把尺子才有意义。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 能从 strm 文件名里解析出来的维度（与 nameinfo.parse 的键名一致，蛇形）
SUPPORTED_DIMS = {
    "resolution",     # 2160p / 1080p
    "video_codec",    # H.265 / H.264
    "hdr",            # Dolby Vision P7 / HDR10 / SDR
    "bitrate",        # 25.2Mbps
    "source",         # BluRay Remux / WEB-DL
    "audio_codec",    # TrueHD / DTS-HD MA
    "color_depth",    # 10-bit
    "frame_rate",     # 24fps
    "file_extension",  # strm
}

# MT 驼峰 → 本项目蛇形
_DIM_ALIAS = {
    "videoCodec": "video_codec",
    "audioCodec": "audio_codec",
    "colorDepth": "color_depth",
    "frameRate": "frame_rate",
    "fileExtension": "file_extension",
    "fileSize": "file_size",
    "videoBitrate": "bitrate",
    "audioChannels": "audio_channels",
}

# 明确知道算不了、且必须报出来的（fileSize 需要真实字节数，strm 没有）
KNOWN_UNSUPPORTED = {"file_size", "audio_channels"}


def normalize_dim(key: str) -> str:
    return _DIM_ALIAS.get(key, key)


def to_scorer_rules(rule: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """把 MT 的一条质量规则转成本插件评分器能用的结构。

    返回 (rules, unsupported_dims, disabled_dims)：
      rules = {
        "entry_id", "entry_name", "min_score_diff",
        "scoring": {dim: {weight, priority, unknown_policy}},
        "exclude": {resolutions, codecs, hdr_types},
        "slots": [...], "unmatched": "best"|"skip",
      }
      unsupported_dims：MT 里启用、但本插件从文件名算不出来的维度
      disabled_dims：MT 里本就 enabled=false 的维度（不算问题）
    """
    rule = rule or {}
    scoring_in = rule.get("scoring") or {}
    scoring: Dict[str, Dict[str, Any]] = {}
    unsupported: List[str] = []
    disabled: List[str] = []

    for raw_key, spec in scoring_in.items():
        if not isinstance(spec, dict):
            continue
        dim = normalize_dim(raw_key)
        enabled = bool(spec.get("enabled"))
        weight = spec.get("weight")
        try:
            weight = float(weight) if weight is not None else 0.0
        except (TypeError, ValueError):
            weight = 0.0

        if not enabled or weight <= 0:
            disabled.append(dim)
            continue
        if dim not in SUPPORTED_DIMS:
            unsupported.append(dim)
            continue
        scoring[dim] = {
            "weight": weight,
            "priority": list(spec.get("priority") or []),
            "unknown_policy": (spec.get("unknownPolicy") or "zero"),
        }

    # 规则里没写 bitrate 的 priority：数值越大越好
    if "bitrate" in scoring:
        scoring["bitrate"].setdefault("numeric", True)

    ver = rule.get("version") or {}
    exclude_in = rule.get("exclude") or {}
    rules = {
        "entry_id": rule.get("id") or rule.get("entry_id") or "",
        "entry_name": rule.get("name") or rule.get("entry_name") or "",
        "min_score_diff": _as_float(rule.get("minScoreDiff") or rule.get("min_score_diff"), 0.0),
        "scoring": scoring,
        "exclude": {
            "resolutions": list(exclude_in.get("resolutions") or []),
            "codecs": list(exclude_in.get("codecs") or []),
            "hdr_types": list(exclude_in.get("hdrTypes") or exclude_in.get("hdr_types") or []),
        },
        "slots": list(ver.get("slots") or []),
        "unmatched": ver.get("unmatched") or "best",
        "version_enabled": bool(ver.get("enabled", True)),
    }
    return rules, unsupported, disabled


def _as_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def weights_table(rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    """给 UI 用的权重表，按权重降序。"""
    rows = []
    for dim, spec in (rules.get("scoring") or {}).items():
        pri = spec.get("priority") or []
        rows.append({
            "dimension": dim,
            "weight": spec.get("weight"),
            "priority": " > ".join(pri) if pri else "（数值越大越好）",
            "unknown": spec.get("unknown_policy") or "zero",
        })
    rows.sort(key=lambda r: -(r.get("weight") or 0))
    return rows
