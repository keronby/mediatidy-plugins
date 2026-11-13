#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STRM & 媒体去重洗版伴随文件清理工具 - MT 洗版评分引擎。

遵循 MediaTidy 质量评分规则语义：
- 每个评分维度独立拥有 unknown_policy（ignore / zero），决定「任一侧未知/缺失」时如何比较。
  - ignore（默认）：任一侧未知时，该维度对整组双方都跳过，不参与比较与分差。
  - zero：维度继续参与，未知侧记 0 分，已知侧正常评分。
- 评分维度集合动态取自规则文件的 scoring 块，支持 resolution / video_codec / hdr /
  source / audio_codec / bitrate / subtitle 等任意维度。
- 槽位（version.slots）按 file_extension / filename_keywords 路由到不同评分配置。
"""

import math
from typing import Dict, Any, List, Optional, Tuple

from .models import MediaFile, MediaMeta

# 判定「未知/无法判断」的哨兵值（区别于「确认不存在」）
_UNKNOWN_RESOLUTION = "unknown"


class _RuleView:
    """规则条目视图：把某个 quality 条目当作独立的规则配置暴露给槽位匹配/评分。

    只覆盖 slot/unmatched/scoring/exclude/min_score_diff 相关字段，
    使 QualityScorer 的既有方法（_match_slot/_effective_specs/slot_tier）能
    基于正确条目工作，而无需大改。
    """
    def __init__(self, owner: "QualityScorer", entry: Dict[str, Any]):
        self._owner = owner
        self.slots = entry.get("slots", []) or []
        self.unmatched = entry.get("unmatched", "skip")
        self.scoring_specs = entry.get("scoring", {}) or owner.scoring_specs
        self.exclude_specs = entry.get("exclude", {}) or owner.exclude_specs
        self.min_score_diff = float(entry.get("min_score_diff", owner.min_score_diff))
        self.entries = {}
        self.entry_order = []

    def _match_slot(self, media_file: MediaFile) -> Optional[Dict[str, Any]]:
        return self._owner._match_slot(media_file)

    def _effective_specs(self, media_file: MediaFile) -> Tuple[Dict[str, Any], Dict[str, Any], float]:
        return self._owner._effective_specs(media_file)

    def slot_tier(self, media_file: MediaFile) -> Optional[str]:
        return self._owner.slot_tier(media_file)

    def _is_excluded_by(self, meta: MediaMeta, exclude_specs: Dict[str, Any]) -> bool:
        return self._owner._is_excluded_by(meta, exclude_specs)

    def _score_dimension_spec(self, dim_name: str, value: str, scoring_specs: Dict[str, Any]) -> float:
        return self._owner._score_dimension_spec(dim_name, value, scoring_specs)

    def _score_bitrate_spec(self, bitrate_kbps, scoring_specs: Dict[str, Any]) -> float:
        return self._owner._score_bitrate_spec(bitrate_kbps, scoring_specs)

    def _score_subtitle(self, media_file: MediaFile, scoring_specs: Dict[str, Any]) -> float:
        return self._owner._score_subtitle(media_file, scoring_specs)

    def _is_unknown(self, dim: str, value: Any) -> bool:
        return self._owner._is_unknown(dim, value)

    def _dim_value(self, meta: MediaMeta, dim: str) -> Any:
        return self._owner._dim_value(meta, dim)

    def is_excluded(self, meta: MediaMeta) -> bool:
        return self._owner._is_excluded_by(meta, self.exclude_specs)


class QualityScorer:
    """遵循 MediaTidy 规范的媒体质量评分与洗版判定引擎。"""

    def __init__(self, rule_config: Dict[str, Any], prefer_real_media: bool = False, real_media_bonus: float = 0.0, force_dedup: bool = False):
        # STRM-only 模式：库内全部为 .strm，无真实视频 vs strm 竞争，
        # 故默认关闭实体视频偏好与加分，纯按 MT 质量规则打分。
        self.rule_config = rule_config
        self.prefer_real_media = prefer_real_media
        self.real_media_bonus = real_media_bonus
        # 强制查重删除：本地 strm 场景，凡同目录/同标题聚合出的重复，不分评分分差一律判洗版。
        self.force_dedup = force_dedup
        self.min_score_diff = float(rule_config.get("min_score_diff", 8.0))
        self.scoring_specs = rule_config.get("scoring", {})
        self.exclude_specs = rule_config.get("exclude", {})
        # MT version.slots 多槽位（按 file_extension / filename 路由）
        self.slots = rule_config.get("slots", []) or []
        # unmatched 策略：skip（不匹配槽位的资源跳过）/ best（归入同一未匹配组取最优）
        self.unmatched = rule_config.get("unmatched", "skip")
        # 多条目规则（movie/tv 分别一套），用于按媒体类型选择槽位/评分配置
        self.entries = rule_config.get("entries", {}) or {}
        self.entry_order = rule_config.get("order", []) or []

    def _entry_for(self, media_file: MediaFile) -> "QualityScorer":
        """按文件媒体类型返回对应的规则条目视图（movie→movie 条目，tv→tv 条目）。

        返回自身或一个轻量「规则视图」，便于槽位匹配/评分按正确条目进行。
        """
        mt = getattr(media_file.meta, "media_type", None)
        if not self.entries or mt not in ("movie", "tv"):
            return self
        wanted = mt
        for eid in self.entry_order:
            e = self.entries.get(eid)
            if not e:
                continue
            eid_l = eid.lower()
            if wanted == "tv" and "tv" in eid_l:
                return _RuleView(self, e)
            if wanted == "movie" and "movie" in eid_l:
                return _RuleView(self, e)
        return self

    # ------------------------------------------------------------------ 槽位
    def _match_slot(self, media_file: MediaFile) -> Optional[Dict[str, Any]]:
        """根据文件扩展名/文件名/分辨率匹配到对应槽位，返回槽位配置或 None（用默认）。

        优先级：
        1. ISO 目标格式（strm 内容指向 .iso / 真实 .iso 文件）→ 优先路由到 name 含 "iso" 的槽位，
           使 ISO 与 mkv/mp4 等常规格式走不同评分槽位、互不一起查重。
        2. 常规槽位按 file_extension / resolution / filename_keywords 匹配。
           对 strm 文件，file_extension 用「有效扩展名」target_ext（读 strm 内容确认指向文件后缀），
           而非自身 .strm，从而让 ISO 槽位能通过 file_extension: [iso] 正确命中。
        """
        view = self._view_for(media_file)
        slots = view.slots
        if not slots:
            return None

        # ISO 单独槽位：读 strm 内容确认指向文件后缀为 iso 的文件，走独立槽位
        if getattr(media_file.meta, "target_format", "standard") == "iso":
            for slot in slots:
                if "iso" in str(slot.get("name", "")).lower():
                    return slot

        # 有效扩展名：strm 用 target_ext（内容指向文件后缀），否则用自身后缀
        ext = (media_file.meta.target_ext or "").lower() or media_file.extension.lstrip(".").lower()
        fname = media_file.path.name
        resolution = (media_file.meta.resolution or "").lower()

        for slot in slots:
            # 跳过 iso 槽（它仅用于 ISO 目标格式，不参与常规匹配）
            if "iso" in str(slot.get("name", "")).lower():
                continue
            match = slot.get("match", {}) or {}

            # file_extension 匹配（strm 用有效扩展名）
            exts = match.get("file_extension") or []
            if isinstance(exts, str):
                exts = [exts]
            if exts and ext in [str(e).lower().lstrip(".") for e in exts]:
                return slot

            # resolution 匹配
            res_list = match.get("resolution") or []
            if isinstance(res_list, str):
                res_list = [res_list]
            if res_list and resolution and resolution in [str(r).lower() for r in res_list]:
                return slot

            # 文件名关键词匹配
            fk = match.get("filename_keywords") or []
            if isinstance(fk, str):
                fk = [fk]
            for kw in fk:
                if kw and kw.lower() in fname.lower():
                    return slot
        return None

    def _effective_specs(self, media_file: MediaFile) -> Tuple[Dict[str, Any], Dict[str, Any], float]:
        """返回 (scoring_specs, exclude_specs, min_score_diff)，考虑槽位覆盖与 media_type 条目。"""
        view = self._view_for(media_file)
        slot = self._match_slot(media_file)
        if slot:
            return (
                slot.get("scoring") or view.scoring_specs,
                slot.get("exclude") or view.exclude_specs,
                float(slot.get("min_score_diff", view.min_score_diff)),
            )
        return view.scoring_specs, view.exclude_specs, view.min_score_diff

    # ------------------------------------------------------------ 槽位分组
    def slot_tier(self, media_file: MediaFile) -> Optional[str]:
        """返回该文件所属的「槽位档」键，用于分组（不同档互不竞争洗版）。

        按 media_type 选择条目后：
          - 命中 ISO 槽 → "iso"（ISO 单独一档）
          - 命中 normal 等其它槽 → 该槽名小写（如 "normal"）
          - 未命中任何槽：
              * unmatched == "best" → "unmatched"（所有未匹配的归入同一个未匹配档）
              * unmatched == "skip" → None（不参与分组/洗版，直接跳过）
        返回 None 表示该文件应被跳过（unmatched: skip 且未命中任何槽）。
        """
        view = self._view_for(media_file)
        slot = self._match_slot(media_file)
        if slot:
            name = str(slot.get("name", "")).lower()
            return "iso" if "iso" in name else name
        # 未命中任何槽位
        if view.unmatched == "best":
            return "unmatched"
        return None

    def _view_for(self, media_file: MediaFile):
        """返回该文件对应的规则视图（movie/tv 条目），未命中条目则返回 self。"""
        view = self._entry_for(media_file)
        return view if view is not self else self

    def is_skipped(self, media_file: MediaFile) -> bool:
        """该文件是否因 unmatched: skip 且未命中槽位而应被跳过。"""
        return self.slot_tier(media_file) is None

    # ---------------------------------------------------------------- 排除
    def is_excluded(self, meta: MediaMeta) -> bool:
        """按默认排除规则检查（兼容旧调用）。"""
        return self._is_excluded_by(meta, self.exclude_specs)

    def _is_excluded_by(self, meta: MediaMeta, exclude_specs: Dict[str, Any]) -> bool:
        if not exclude_specs:
            return False
        if meta.resolution in exclude_specs.get("resolutions", []):
            return True
        if meta.video_codec in exclude_specs.get("codecs", []):
            return True
        if meta.hdr in exclude_specs.get("hdr_types", []):
            return True
        return False

    # ---------------------------------------------------------- 未知值判定
    @staticmethod
    def _dim_value(meta: MediaMeta, dim: str) -> Any:
        """取某维度的原始值。"""
        if dim == "bitrate":
            return meta.bitrate_kbps
        if dim == "subtitle":
            return None  # 特殊维度，单独处理
        return getattr(meta, dim, None)

    @staticmethod
    def _is_unknown(dim: str, value: Any) -> bool:
        """判断该维度取值是否「未知/无法判断」（区别于「确认不存在」）。"""
        if dim == "bitrate":
            return value is None
        if dim == "subtitle":
            return False  # 有/无字幕始终是确定事实
        if value is None:
            return True
        if isinstance(value, str):
            s = value.strip()
            return s == "" or s.lower() == _UNKNOWN_RESOLUTION
        return False

    # ------------------------------------------------------------ 维度打分
    def _score_dimension_spec(self, dim_name: str, value: str, scoring_specs: Dict[str, Any]) -> float:
        """按指定 scoring 规则计算单个维度得分（未知值记 0，其余按 priority 匹配）。"""
        spec = scoring_specs.get(dim_name, {})
        if not spec or not spec.get("enabled", True):
            return 0.0

        weight = float(spec.get("weight", 1.0))
        priority: List[str] = spec.get("priority", [])
        if not priority:
            return 0.0
        if self._is_unknown(dim_name, value):
            return 0.0

        val_lower = value.strip().lower()
        total_items = len(priority)

        for idx, item in enumerate(priority):
            item_lower = item.strip().lower()
            if item_lower == val_lower or item_lower in val_lower or val_lower in item_lower:
                # MT 官方公式：位置分为 10 分制（第1项=10，第2项=10*(N-1)/N，…，最后一项=10/N）
                # 总分 = Σ(维度权重 × 位置分)，未命中=0。
                # 例 priority[2160p,1080p,720p] 位置分 10/6.67/3.33，weight=20 时得分 200/133.33/66.67。
                position_score = 10.0 * (total_items - idx) / float(total_items)
                return round(weight * position_score, 2)

        # 已知但不在优先级表 → 未命中得 0 分（MT 语义）
        return 0.0

    def _score_bitrate_spec(self, bitrate_kbps: Optional[int], scoring_specs: Dict[str, Any]) -> float:
        """码率得分：对数压缩映射到 [0, weight]。"""
        spec = scoring_specs.get("bitrate", {})
        if not spec or not spec.get("enabled", True):
            return 0.0
        weight = float(spec.get("weight", 25.0))
        if bitrate_kbps is None:
            return 0.0

        mbps = bitrate_kbps / 1000.0
        lo, hi = 1.0, 50.0
        if mbps <= lo:
            return 0.0
        ratio = math.log(mbps / lo) / math.log(hi / lo)
        ratio = max(0.0, min(1.0, ratio))
        # MT 官方：bitrate 连续数值公式，最高位置分 10（约 100Mbps 满分），
        # 得分 = weight × 位置分，位置分 = 10 × ratio（对数压缩映射到 [0,10]）。
        return round(weight * ratio * 10.0, 2)

    def _score_subtitle(self, media_file: MediaFile, scoring_specs: Dict[str, Any]) -> float:
        """字幕维度：有伴随字幕得满分，无字幕得 0 分（两者都是确定事实）。

        MT 官方位置分 10 分制：有字幕 = 位置分 10 × weight。
        """
        spec = scoring_specs.get("subtitle", {})
        if not spec or not spec.get("enabled", False):
            return 0.0
        has_sub = any(c.suffix.lower() in [".srt", ".ass", ".ssa", ".sub"] for c in media_file.companions)
        return float(spec.get("weight", 5.0)) * 10.0 if has_sub else 0.0

    # -------------------------------------------------------------- 单文件
    def score_file(self, media_file: MediaFile) -> float:
        """公开打分入口：返回单个文件的总分（供混合链接比对复用）。"""
        score, _details = self._score_file(media_file)
        return float(score)

    def _score_file(self, media_file: MediaFile, disabled_dims: Optional[set] = None) -> Tuple[float, Dict[str, Any]]:
        """计算单个文件得分。disabled_dims 为被 unknown_policy=ignore 禁用的维度集合。

        返回 (总分, details)。details 每个维度形如 {"val":..., "score":..., "known":bool, "disabled":bool}。
        """
        meta = media_file.meta
        disabled = disabled_dims or set()
        scoring_specs, exclude_specs, min_diff = self._effective_specs(media_file)
        slot = self._match_slot(media_file)

        details: Dict[str, Any] = {}
        total = 0.0

        if slot:
            details["slot"] = {"val": slot.get("name", "normal")}

        # 排除规则
        if self._is_excluded_by(meta, exclude_specs):
            return -999.0, {"excluded": True}

        # 动态遍历规则中启用的维度
        for dim, spec in scoring_specs.items():
            if not isinstance(spec, dict) or not spec.get("enabled", True):
                continue
            if dim in disabled:
                details[dim] = {"val": self._dim_value(meta, dim), "score": 0.0, "known": False, "disabled": True}
                continue

            if dim == "bitrate":
                val = meta.bitrate_kbps
                known = not self._is_unknown(dim, val)
                sc = self._score_bitrate_spec(val, scoring_specs)
                details[dim] = {"val": val, "score": sc, "known": known}
                total += sc
            elif dim == "subtitle":
                sc = self._score_subtitle(media_file, scoring_specs)
                details[dim] = {"val": None, "score": sc, "known": True}
                total += sc
            else:
                val = self._dim_value(meta, dim)
                known = not self._is_unknown(dim, val)
                sc = self._score_dimension_spec(dim, val, scoring_specs)
                details[dim] = {"val": val, "score": sc, "known": known}
                total += sc

        # 实体视频偏好加分
        if not media_file.is_strm:
            details["real_media_bonus"] = self.real_media_bonus
            total += self.real_media_bonus

        return round(total, 2), details

    def calculate_score(self, media_file: MediaFile) -> float:
        """计算单个媒体文件得分（单文件场景，无组级 unknown_policy 处理）。"""
        score, details = self._score_file(media_file)
        media_file.score = score
        media_file.score_details = details
        return score

    # -------------------------------------------------------------- 组比较
    def _disabled_dims_for_group(self, files: List[MediaFile]) -> Tuple[set, Dict[str, str]]:
        """根据组内各维度 unknown_policy 计算应禁用的维度集合。

        ignore：任一侧未知 → 整组跳过该维度。
        zero：不跳过，未知侧记 0 分。
        返回 (disabled_dims, policy_map)。
        """
        disabled: set = set()
        policy_map: Dict[str, str] = {}

        # 收集所有维度（用每个文件的有效 scoring 维度，取并集）
        all_dims: set = set()
        for f in files:
            scoring_specs, _, _ = self._effective_specs(f)
            for dim, spec in scoring_specs.items():
                if isinstance(spec, dict) and spec.get("enabled", True):
                    all_dims.add(dim)

        for dim in all_dims:
            # 取该维度的 unknown_policy（不同文件槽位可能不同，取第一个非 ignore 为准；默认 ignore）
            policy = "ignore"
            has_unknown = False
            for f in files:
                scoring_specs, _, _ = self._effective_specs(f)
                spec = scoring_specs.get(dim, {})
                if isinstance(spec, dict):
                    p = spec.get("unknown_policy", "ignore")
                    if p == "zero":
                        policy = "zero"
                val = self._dim_value(f.meta, dim)
                if self._is_unknown(dim, val):
                    has_unknown = True

            policy_map[dim] = policy
            if has_unknown and policy == "ignore":
                disabled.add(dim)

        return disabled, policy_map

    def compare_and_decide(self, files: List[MediaFile]) -> Tuple[MediaFile, List[MediaFile], float, bool]:
        """在同组多个版本中比对评分，决定保留版（Winner）与淘汰版（Losers）。

        返回: (胜出者, 淘汰列表, 最大分差, 是否达到洗版门槛)
        """
        if not files:
            raise ValueError("No files to compare")
        if len(files) == 1:
            self.calculate_score(files[0])
            return files[0], [], 0.0, False

        # 1. 计算组级禁用维度（unknown_policy=ignore 且任一侧未知）
        disabled_dims, _ = self._disabled_dims_for_group(files)

        # 2. 逐文件打分（应用禁用维度）
        for f in files:
            score, details = self._score_file(f, disabled_dims)
            f.score = score
            f.score_details = details

        # 3. 排序：得分高者优先；得分相同优先真实大文件；再相同优先 mtime 更新者
        #    「保留哪个、删哪个」始终按分数决定：分数最高的保留，其余淘汰。
        #    平分同分时（用户 m03287 定案）：strm 之间 http 分享链接保留、本地路径删除——
        #    本地路径只是网盘索引，分享链接才是可长期使用的正本。
        def _link_rank(x):
            lk = (getattr(x, "link_kind", "") or "").lower()
            if not x.is_strm:
                return 2  # 真实媒体 > 分享链接 > 本地路径
            return 1 if lk == "http" else 0
        sorted_files = sorted(
            files,
            key=lambda x: (
                x.score,
                _link_rank(x),
                1 if not x.is_strm else 0,
                x.mtime
            ),
            reverse=True
        )

        winner = sorted_files[0]
        losers = sorted_files[1:]

        # 4. 分差 = 胜者 - 最高次席（仅用于展示/记录，不参与洗版判定）
        runner_up = losers[0]
        score_diff = round(winner.score - runner_up.score, 2)

        # 5. 洗版判定：本地 strm 查重场景下，保留重复的 strm 本身没有意义，
        #    分差门槛（min_score_diff）也无意义——凡同组聚合出重复即判洗版，
        #    删除时按分数保留最高分、删除其余。prefer_real_media 同理不再生效。
        is_wash_out = True

        return winner, losers, score_diff, is_wash_out
