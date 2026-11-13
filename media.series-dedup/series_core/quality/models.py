#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STRM & 媒体去重洗版伴随文件清理工具 - 数据模型定义"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Set


@dataclass
class MediaMeta:
    """从文件名与路径中解析出的元数据"""
    title: str = ""
    year: Optional[int] = None
    tmdb_id: Optional[str] = None
    media_type: str = "movie"  # "movie" 或 "tv"
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_end: Optional[int] = None  # 跨集支持，如 E01-E02

    # 规格字段（用于 MT 规则打分）
    resolution: str = ""        # 2160p, 1080p, 720p 等
    source: str = ""            # Remux, UHD BluRay, BluRay, WEB-DL 等
    video_codec: str = ""       # H.265, H.264, AV1, VP9 等
    audio_codec: str = ""       # TrueHD, DTS-HD MA, DDP, AAC 等
    hdr: str = ""               # Dolby Vision, HDR10+, HDR, SDR 等
    bitrate_kbps: Optional[int] = None
    release_group: str = ""     # 压制/发布组
    edition: str = ""           # Extended, Director's Cut 等
    target_format: str = "standard"  # 目标媒体格式分类: "iso" 或 "standard"（用于电影 ISO 独立分类）
    target_ext: str = ""        # 有效扩展名（strm 内容指向文件的后缀，如 iso/mkv/mp4；真实文件取自身后缀）
    languages: List[str] = field(default_factory=list)


@dataclass
class MediaFile:
    """媒体文件对象（真实视频文件或 .strm 文件）"""
    path: Path
    is_strm: bool
    size_bytes: int
    mtime: float
    meta: MediaMeta
    score: float = 0.0
    score_details: Dict[str, Any] = field(default_factory=dict)
    companions: List[Path] = field(default_factory=list)  # 该文件专属关联的伴随文件
    link_kind: str = "real"  # strm 内容分组（用户 m03178）：http=分享链接 / local=本地链接 / real=真实媒体

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()


@dataclass
class DuplicateGroup:
    """重复/洗版组（同一部电影或同一集电视剧下的多个竞争版本）"""
    group_key: str              # 唯一键，如 "Movie:阿凡达 (2009)" 或 "TV:曼达洛人:S01E01"
    media_type: str             # "movie" 或 "tv"
    title: str
    season: Optional[int] = None
    episode: Optional[int] = None
    directory: Path = field(default_factory=Path)
    files: List[MediaFile] = field(default_factory=list)
    winner: Optional[MediaFile] = None
    losers: List[MediaFile] = field(default_factory=list)
    score_diff: float = 0.0
    is_wash_out: bool = False   # 是否达到洗版分差门槛触发清理


@dataclass
class OrphanCompanion:
    """孤儿伴随文件（没有任何视频关联的残留 .nfo, -mediainfo.json, 字幕等）"""
    path: Path
    category: str               # "subtitle", "nfo", "mediainfo_json", "thumb", "unknown"
    reason: str
