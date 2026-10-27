# -*- coding: utf-8 -*-
"""按 MT 的质量规则给单个 strm 打分。

评分方式对齐 MT Wiki「质量评分规则」公式（10倍放大模式）：
  总分 = Σ（维度权重 × 维度位置分）
  priority 有 N 项时，第 1 项位置分为 10，第 2 项为 10 × (N-1) / N，最后一项为 10 / N，未命中为 0。
  维度得分 = weight × 位置分 = 10 * (N - idx) / N * weight
  最高档拿满 10 × weight，最低档拿 10 × weight / N。
  未写 priority 的数值型维度（bitrate）用对数映射到位置分 0 ~ 10，得分 0 ~ 10 × weight。

未知策略：`unknownPolicy` 决定维度值缺失时怎么办
  - "zero"     → 该维度记 0（默认，和 MT 一致）
  - "best"     → 该维度按最高档算（10 × weight）
  - "skip"     → 该维度整个不计入分母
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def _dim_value(info: Dict[str, Any], dim: str) -> Optional[Any]:
    return info.get(dim)


def _match_priority(value: Any, priority: List[str]) -> Optional[int]:
    """返回命中的档位下标（0 = 最好），未命中返回 None。
    字符串匹配不区分大小写，并支持以空格或 + 分隔的前缀匹配（对齐 MT 规则）。
    例如 priority 为 "Dolby Vision" 可匹配 "Dolby Vision P8"，"Remux" 匹配 "Remux+BluRay"。
    """
    if value is None or not priority:
        return None
    v = str(value).strip().lower()
    for i, p in enumerate(priority):
        p_str = str(p).strip().lower()
        if p_str == v:
            return i
        if v.startswith(p_str + " ") or v.startswith(p_str + "+"):
            return i
        if p_str.startswith(v + " ") or p_str.startswith(v + "+"):
            return i
    return None


def _score_priority(value: Any, priority: List[str], weight: float) -> Tuple[float, str]:
    idx = _match_priority(value, priority)
    n = len(priority)
    if idx is None:
        return 0.0, "%s 未命中优先级（%s）" % (value, " > ".join(priority))
    # MT 官方规则：第 1 项位置分为 10，第 2 项为 10*(N-1)/N ... 10倍放大模式
    pos_score = 10.0 * (n - idx) / n
    score = pos_score * weight
    return score, "%s 命中第 %d 档（%s，位置分 %.2f）" % (value, idx + 1, priority[idx], pos_score)


# bitrate 的对数映射：1Mbps → 1 分，100Mbps → 10 分（满档 10 分位置分）
_BR_MIN, _BR_MAX = 1.0, 100.0


def _score_bitrate(value: Any, weight: float) -> Tuple[float, str]:
    try:
        br = float(value)
    except (TypeError, ValueError):
        return 0.0, "码率未知"
    if br <= 0:
        return 0.0, "码率无效"
    lo, hi = math.log10(_BR_MIN), math.log10(_BR_MAX)
    t = (math.log10(br) - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    pos_score = 10.0 * t
    score = pos_score * weight
    return score, "%.1fMbps（位置分 %.2f）" % (br, pos_score)


# 文件大小：strm 没有真实字节数，用码率 × 时长近似？—— 不近似，直接不支持。
# 若规则里有 file_size，会在 rules.py 里被归入 unsupported 并告警。


def score(info: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[float, List[str]]:
    """给一个文件的解析结果打分。

    返回 (总分, 逐维度理由列表)。理由用于向用户解释「为什么判它赢」。
    """
    scoring = rules.get("scoring") or {}
    exclude = rules.get("exclude") or {}
    total = 0.0
    why: List[str] = []

    # 排除项：命中直接判 0 分（MT 的 exclude 语义就是「这类不要」）
    ex_res = {str(x).lower() for x in (exclude.get("resolutions") or [])}
    ex_codec = {str(x).lower() for x in (exclude.get("codecs") or [])}
    ex_hdr = {str(x).lower() for x in (exclude.get("hdr_types") or [])}

    res = info.get("resolution")
    if res and str(res).lower() in ex_res:
        return 0.0, ["命中排除分辨率：%s" % res]
    codec = info.get("video_codec")
    if codec and str(codec).lower() in ex_codec:
        return 0.0, ["命中排除编码：%s" % codec]
    hdr = info.get("hdr")
    if hdr and str(hdr).lower() in ex_hdr:
        return 0.0, ["命中排除 HDR：%s" % hdr]

    for dim, spec in scoring.items():
        weight = float(spec.get("weight") or 0)
        policy = (spec.get("unknown_policy") or "zero").lower()
        value = _dim_value(info, dim)

        if value is None:
            if policy == "best":
                dim_score = 10.0 * weight
                total += dim_score
                why.append("%s 未知（按 best 记满 %g）" % (dim, dim_score))
            elif policy == "skip":
                why.append("%s 未知（跳过）" % dim)
            else:
                why.append("%s 未知（记 0）" % dim)
            continue

        if dim == "bitrate" or spec.get("numeric"):
            s, r = _score_bitrate(value, weight)
        else:
            pri = spec.get("priority") or []
            if pri:
                s, r = _score_priority(value, pri, weight)
            else:
                # 有值但没给优先级：按命中满档 10 处理
                s, r = 10.0 * weight, "%s=%s（规则无优先级表，记满档 10）" % (dim, value)
        total += s
        why.append("%s: %s → +%.2f" % (dim, r, s))

    return total, why


def max_score(rules: Dict[str, Any]) -> float:
    """理论满分（10倍放大模式），用于把分数换算成百分比展示。"""
    return sum(10.0 * float((s or {}).get("weight") or 0)
               for s in (rules.get("scoring") or {}).values())
