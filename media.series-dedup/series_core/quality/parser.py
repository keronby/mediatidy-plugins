#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STRM & 媒体去重洗版伴随文件清理工具 - 文件名与路径元数据解析器"""

import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from .models import MediaMeta

# 常见视频后缀
VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".rmvb", ".iso",
    ".wmv", ".mov", ".flv", ".webm", ".rm", ".mpg", ".mpeg", ".vob"
}
STRM_EXT = ".strm"


class MediaParser:
    """媒体文件名与路径元数据解析器，支持 MT 命名规范与智能正则回退"""

    def __init__(self, custom_regex: Optional[Dict[str, str]] = None):
        self.custom_regex = custom_regex or {}
        self._compile_patterns()

    def _compile_patterns(self):
        # 季集匹配正则
        self.re_season_dir = re.compile(r"Season\s*(\d+)", re.IGNORECASE)
        self.re_s_e = re.compile(r"[Ss](\d+)[\. \-_]*[Ee](\d+)(?:[\-_][Ee]?(\d+))?", re.IGNORECASE)
        self.re_ep_fallback = re.compile(r"(?:EP|E|第)\s*(\d{1,4})(?:[\-_]?(?:EP|E|集)?(\d{1,4}))?\s*(?:集)?", re.IGNORECASE)

        # 电影年份与 TMDB 匹配
        self.re_folder_tmdb = re.compile(
            r"^(?P<title>.+?)[ \.\[\(]+(?P<year>(?:19|20)\d{2})[ \]\)]*(?:[\[\{](?:tmdbid-|tmdb-)?(?P<tmdbid>\d+)[\]\}])?",
            re.IGNORECASE
        )

        # 分辨率匹配
        self.re_resolution = re.compile(r"\b(4320p|8K|2160p|4K|UHD|1080p|1080i|FHD|720p|HD|576p|480p|360p)\b", re.IGNORECASE)

        # 来源匹配
        self.re_source = re.compile(
            r"\b(Remux\+UHD BluRay|Remux\+BluRay|Remux|UHD[\.\-_ ]BluRay|Blu[\.\-_]?Ray|WEB[\.\-_]?DL|WEBRip|WEB|HDTV|DVDRip|DVD|CAM)\b",
            re.IGNORECASE
        )

        # 视频编码
        self.re_video_codec = re.compile(r"\b(AV1|H\.?265|x265|HEVC|H\.?264|x264|AVC|VP9|MPEG-2|Xvid|DivX)\b", re.IGNORECASE)

        # HDR 规格
        self.re_hdr = re.compile(
            r"\b(Dolby[ \.\-_]?Vision[ \.\-_]?P7|Dolby[ \.\-_]?Vision[ \.\-_]?P5|Dolby[ \.\-_]?Vision[ \.\-_]?P8|Dolby[ \.\-_]?Vision|DV|HDR10\+|HDR10|HLG|HDR|SDR)\b",
            re.IGNORECASE
        )

        # 音频编码（兼容无分隔符写法：DTSHD-MA、TrueHDAtmos、DDP5.1 等）
        self.re_audio_codec = re.compile(
            r"\b(TrueHD[ \.\-_]?(?:Dolby[ \.\-_]?)?Atmos[ \.\-_]?7\.1|TrueHD[ \.\-_]?Atmos|TrueHD|DTS:X|DTS[\-_]?HD[ \.\-_]?MA[ \.\-_]?7\.1|DTS[\-_]?HD[ \.\-_]?MA[ \.\-_]?5\.1|DTS[\-_]?HD[ \.\-_]?MA|DTS[\-_]?HD|DDP[ \.\-_]?5\.1|Dolby[ \.\-_]?Digital[ \.\-_]?Plus|E[\-_]?AC[\-_]?3|DTS|Dolby[ \.\-_]?Digital|AC3|AAC|FLAC|MP3)\b",
            re.IGNORECASE
        )

        # 码率：形如 747.2Mbps / 18.0Mbps / 2500kbps / 12Mbps
        self.re_bitrate = re.compile(r"\b(\d+(?:\.\d+)?)\s*(Mbps|kbps|Kbps|mbps|Gbps)\b")

        # 版本/剪辑变体标记（这些是合法变体，不应互相洗版）：
        #   -C / -剪辑 / -删减 / 完整版 / 导演剪辑版 / Extended / Director's Cut / Uncut / Uncensored 等
        # 使用非捕获分组 + 后向断言确保捕获到干净的标签
        self.re_edition = re.compile(
            r"(?<=[\.\-_ ])"
            r"(?P<edition>"
            r"C|剪辑|删减|阉割|完整版|未删减|导演剪辑版|加长版|剧场版|导演剪辑|加长|"
            r"Uncut|Uncensored|Extended|Remastered|修复版|"
            r"Director[^\.\-_]*?Cut|Alternative[.\-_ ]?Ending"
            r")"
            r"(?=$|[\.\-_ ])",
            re.IGNORECASE
        )

    def is_media_file(self, path: Path) -> Tuple[bool, bool]:
        """判断是否为媒体文件。返回: (是否是媒体文件, 是否为strm)"""
        ext = path.suffix.lower()
        if ext == STRM_EXT:
            return True, True
        if ext in VIDEO_EXTS:
            return True, False
        return False, False

    def parse(self, path: Path) -> MediaMeta:
        """从路径和文件名中完整解析 MediaMeta"""
        meta = MediaMeta()
        stem = path.stem
        filename = path.name
        parent_dir = path.parent.name
        grand_dir = path.parent.parent.name if path.parent.parent else ""

        # 1. 优先从路径结构推断是剧集还是电影
        season_num = None
        s_match = self.re_season_dir.search(parent_dir)
        if s_match:
            # 父级目录是 Season XX，说明一定是剧集
            meta.media_type = "tv"
            season_num = int(s_match.group(1))
            series_dir = grand_dir
        else:
            series_dir = parent_dir

        # 2. 尝试从剧集/电影所在目录名解析 Title, Year, TMDB ID
        folder_match = self.re_folder_tmdb.search(series_dir)
        if folder_match:
            meta.title = folder_match.group("title").strip()
            if folder_match.group("year"):
                meta.year = int(folder_match.group("year"))
            if folder_match.group("tmdbid"):
                meta.tmdb_id = folder_match.group("tmdbid")

        # 3. 检查文件名中是否有 SxxExx
        se_match = self.re_s_e.search(filename)
        if se_match:
            meta.media_type = "tv"
            meta.season = int(se_match.group(1))
            meta.episode = int(se_match.group(2))
            if se_match.group(3):
                meta.episode_end = int(se_match.group(3))
        elif season_num is not None:
            # 目录标明了是季，但文件名没有 SxxExx，尝试匹配 EPxx
            ep_match = self.re_ep_fallback.search(filename)
            if ep_match:
                meta.media_type = "tv"
                meta.season = season_num
                meta.episode = int(ep_match.group(1))
                if ep_match.group(2):
                    meta.episode_end = int(ep_match.group(2))
        else:
            # 检查是否有单集记号比如 E01
            ep_match = self.re_ep_fallback.search(filename)
            if ep_match and ("anime" in str(path).lower() or "tv" in str(path).lower() or "剧" in str(path)):
                meta.media_type = "tv"
                meta.season = 1
                meta.episode = int(ep_match.group(1))

        # 4. 如果尚未识别出 Title，从文件名中回退提取
        if not meta.title:
            # 如果文件名有 SxxExx，取 SxxExx 之前的部分作为标题
            if se_match:
                prefix = filename[:se_match.start()].strip(" .-_")
                meta.title = prefix or series_dir
            else:
                meta.title = series_dir

        # 5. 提取质量与规格字段（用于后续 MT 规则打分）
        # 分辨率
        res_m = self.re_resolution.search(filename)
        if res_m:
            r = res_m.group(1).lower()
            if r in ["4k", "uhd"]:
                meta.resolution = "2160p"
            elif r in ["8k"]:
                meta.resolution = "4320p"
            elif r in ["fhd"]:
                meta.resolution = "1080p"
            elif r in ["hd"]:
                meta.resolution = "720p"
            else:
                meta.resolution = r
        else:
            meta.resolution = "unknown"

        # 来源 (Source)
        src_m = self.re_source.search(filename)
        if src_m:
            s_raw = src_m.group(1)
            s_norm = re.sub(r"[\.\-_ ]+", "", s_raw).lower()
            if "remux" in s_norm:
                meta.source = "Remux"
            elif "uhdbluray" in s_norm or "uhdbd" in s_norm:
                meta.source = "UHD BluRay"
            elif "bluray" in s_norm or s_norm == "bd":
                meta.source = "BluRay"
            elif "webdl" in s_norm:
                meta.source = "WEB-DL"
            elif "webrip" in s_norm:
                meta.source = "WEBRip"
            elif s_norm == "web":
                meta.source = "WEB"
            else:
                meta.source = s_raw

        # 视频编码 (Video Codec)
        vc_m = self.re_video_codec.search(filename)
        if vc_m:
            vc = vc_m.group(1).upper()
            if vc in ["X265", "HEVC", "H265"]:
                meta.video_codec = "H.265"
            elif vc in ["X264", "AVC", "H264"]:
                meta.video_codec = "H.264"
            else:
                meta.video_codec = vc

        # 码率 (Bitrate)：文件名中形如 747.2Mbps / 18.0Mbps / 2500kbps
        br_m = self.re_bitrate.search(filename)
        if br_m:
            val = float(br_m.group(1))
            unit = br_m.group(2).lower()
            if unit.startswith("m"):
                meta.bitrate_kbps = int(val * 1000)
            elif unit.startswith("g"):
                meta.bitrate_kbps = int(val * 1000 * 1000)
            else:
                meta.bitrate_kbps = int(val)

        # HDR
        hdr_m = self.re_hdr.search(filename)
        if hdr_m:
            h = hdr_m.group(1).upper()
            if "P7" in h:
                meta.hdr = "Dolby Vision P7"
            elif "P5" in h:
                meta.hdr = "Dolby Vision P5"
            elif "P8" in h:
                meta.hdr = "Dolby Vision P8"
            elif "VISION" in h or h == "DV":
                meta.hdr = "Dolby Vision"
            elif "HDR10+" in h:
                meta.hdr = "HDR10+"
            elif "HDR10" in h:
                meta.hdr = "HDR10"
            elif "HLG" in h:
                meta.hdr = "HLG"
            elif "HDR" in h:
                meta.hdr = "HDR"
            else:
                meta.hdr = "SDR"
        else:
            meta.hdr = "SDR"

        # 音频编码 (Audio Codec) —— 归一化为规范写法便于打分匹配
        ac_m = self.re_audio_codec.search(filename)
        if ac_m:
            raw = ac_m.group(1)
            r = raw.upper().replace(" ", "").replace(".", "").replace("-", "").replace("_", "")
            # 直接映射到规范名称
            if "DTSHDMA" in r:
                meta.audio_codec = "DTS-HD MA" + (" 7.1" if "7.1" in raw else " 5.1" if "5.1" in raw else "")
            elif "DTSHD" in r:
                meta.audio_codec = "DTS-HD"
            elif r == "DTSX" or "DTS:X" in r:
                meta.audio_codec = "DTS:X"
            elif r == "DTS":
                meta.audio_codec = "DTS"
            elif "TRUEHDATMOS" in r:
                meta.audio_codec = "TrueHD Atmos"
            elif r == "TRUEHD":
                meta.audio_codec = "TrueHD"
            elif "DDP" in r or "EAC3" in r:
                meta.audio_codec = "DDP5.1" if "5.1" in raw else "DDP"
            elif "DOLBYDIGITAL" in r or r == "AC3":
                meta.audio_codec = "Dolby Digital"
            elif r == "AAC":
                meta.audio_codec = "AAC"
            elif r == "FLAC":
                meta.audio_codec = "FLAC"
            elif r == "MP3":
                meta.audio_codec = "MP3"
            else:
                meta.audio_codec = raw

        # 版本/剪辑变体标记（用于区分 -C 剪辑版、加长版等合法变体，避免互相洗版）
        ed_m = self.re_edition.search(filename)
        if ed_m:
            meta.edition = ed_m.group("edition").strip()

        return meta
