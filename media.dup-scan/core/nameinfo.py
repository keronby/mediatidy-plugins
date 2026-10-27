# -*- coding: utf-8 -*-
"""从 strm 文件名解析出用于评分的规格信息。

设计原则：
1. **解析不到就是 None，绝不猜。** 猜错的代价是删掉更好的那一版。
2. 只认 MT 自己产出的文件名形态（`作品名.年份.规格...-组名.strm`），
   因为库里的 strm 都是 MT 整理后落盘的，不是原始下载名。
3. 剧集额外解析季集号，用于「同集数」配对；连集包（S01E01-E08）
   单独标出来，交给上层决定是否跳过。

MT 实际文件名样例：
  电影：一树梨花压海棠.1997.1080p.SDR.8-bit.24fps.41.1Mbps.H264.PCM.strm
  剧集：101次抢婚.2023.S01E01.2160p.SDR.10-bit.60fps.25.2Mbps.H265.AAC.strm
  带 Source：活色生香.1997.1080p.BluRay Remux.SDR.8-bit.24fps.31.7Mbps.H264.DTSHD-MA 2.0.strm
  连集：宋飞正传.1989.S04E23-E24.2160p.WEB-DL.HDR10.10-bit.24fps.16.9Mbps.H265.DDP 5.1.strm
"""
from __future__ import annotations

import posixpath
import re
import urllib.parse
from typing import Any, Dict, Optional

VALID_MEDIA_EXTS = {"iso", "bdmv", "mkv", "mp4", "ts", "m2ts", "avi", "rmvb", "wmv", "flv", "mov"}


def extract_real_ext(content: str) -> str:
    """从 strm 文件的首行内容提取真实视频或镜像的扩展名（如 iso / mkv / mp4）。

    支持：
      1. 本地/挂载路径：/CloudNAS/.../movie.iso
      2. 携带参数的 URL：http://.../play?fileName=xxx.mkv&...
      3. 纯 URL 路径：http://.../movies/movie.iso?token=...
    解析不到有效媒体扩展名时返回 "strm"。
    """
    if not content:
        return "strm"
    first_line = content.strip().splitlines()[0].strip()
    if not first_line:
        return "strm"

    # 1. 尝试从 URL 查询参数解析
    if "?" in first_line:
        try:
            parsed = urllib.parse.urlparse(first_line)
            qs = urllib.parse.parse_qs(parsed.query)
            for k in ("fileName", "filename", "file", "name"):
                if k in qs and qs[k]:
                    val = urllib.parse.unquote(qs[k][0])
                    ext = posixpath.splitext(val)[1].lstrip(".").lower()
                    if ext in VALID_MEDIA_EXTS:
                        return ext
        except Exception:
            pass

    # 2. 尝试从路径解析（去 query 与引号）
    try:
        path_part = urllib.parse.urlparse(first_line).path if first_line.startswith(("http://", "https://")) else first_line
        path_part = path_part.strip("\"'")
        ext = posixpath.splitext(path_part)[1].lstrip(".").lower()
        if ext in VALID_MEDIA_EXTS:
            return ext
    except Exception:
        pass

    return "strm"

# ---------------------------------------------------------------- 季集号

# S01E01 / S1E1；连集 S01E01-E08 / S01E01E08 / S01E01-08
_RE_EP = re.compile(
    r'[Ss](\d{1,2})\s*[Ee](\d{1,3})'
    r'(?:\s*[-–~]\s*(?:[Ee])?(\d{1,3}))?'
)
# 1x02
_RE_EP_ALT = re.compile(r'(?<![0-9])(\d{1,2})x(\d{2,3})(?![0-9])')
# 第01集
_RE_EP_CN = re.compile(r'第\s*(\d{1,3})\s*集')
# Season 2 / 第2季。
# 注意：绝不能匹配标题里的裸"S7"之类，必须是 "Season 7" / "第7季"
# 或独立的 "S7"（前后都有分隔符）。乐高大电影2 这类标题数字不能被当季号。
_RE_SEASON = re.compile(
    r'(?:\bSeason[\s._-]*(\d{1,2})\b|\bS(\d{1,2})(?=[.\s_-]|$)'
    r'|第\s*(\d{1,2})\s*季)',
    re.I,
)


def parse_episode(name: str) -> Dict[str, Any]:
    """解析季集号。

    返回 {season, episode, episodeEnd, isMulti}：
      - 解析不到 → season/episode 都为 None
      - 连集 S01E01-E08 → episode=1, episodeEnd=8, isMulti=True
    """
    out: Dict[str, Any] = {"season": None, "episode": None,
                           "episodeEnd": None, "isMulti": False}
    m = _RE_EP.search(name)
    if m:
        out["season"] = int(m.group(1))
        out["episode"] = int(m.group(2))
        if m.group(3):
            end = int(m.group(3))
            # S01E01-08 这种写法，end 是集号；S01E05-1 这种 end 明显不是集号
            if end > out["episode"]:
                out["episodeEnd"] = end
                out["isMulti"] = True
        return out
    m = _RE_EP_ALT.search(name)
    if m:
        out["season"] = int(m.group(1))
        out["episode"] = int(m.group(2))
        return out
    m = _RE_EP_CN.search(name)
    if m:
        out["episode"] = int(m.group(1))
    m = _RE_SEASON.search(name)
    if m:
        got = m.group(1) or m.group(2) or m.group(3)
        if got:
            out["season"] = int(got)
    return out


# ---------------------------------------------------------------- 规格维度

# 分辨率
_RE_RES = re.compile(r'(?<![0-9])(4320p|2160p|1080p|1080i|720p|576p|480p|4K|8K)(?![0-9])', re.I)

# 视频编码
_CODEC_MAP = [
    (re.compile(r'(?<![A-Za-z0-9])(?:H[\.\s]?265|HEVC|x265)(?![A-Za-z0-9])', re.I), "H.265"),
    (re.compile(r'(?<![A-Za-z0-9])(?:H[\.\s]?264|AVC|x264)(?![A-Za-z0-9])', re.I), "H.264"),
    (re.compile(r'(?<![A-Za-z0-9])AV1(?![A-Za-z0-9])', re.I), "AV1"),
    (re.compile(r'(?<![A-Za-z0-9])VP9(?![A-Za-z0-9])', re.I), "VP9"),
    (re.compile(r'(?<![A-Za-z0-9])VC-?1(?![A-Za-z0-9])', re.I), "VC-1"),
    (re.compile(r'(?<![A-Za-z0-9])MPEG-?2(?![A-Za-z0-9])', re.I), "MPEG-2"),
    (re.compile(r'(?<![A-Za-z0-9])Xvid(?![A-Za-z0-9])', re.I), "Xvid"),
    (re.compile(r'(?<![A-Za-z0-9])DivX(?![A-Za-z0-9])', re.I), "DivX"),
]

# HDR：长名优先，避免 "Dolby Vision" 吃掉 "Dolby Vision P7"
_HDR_ORDER = [
    "Dolby Vision P7", "Dolby Vision P5", "Dolby Vision P8",
    "Dolby Vision", "DoVi", "DV",
    "HDR10+", "HDR10", "HDRVivid", "HLG", "HDR", "SDR",
]


def parse_hdr(name: str) -> Optional[str]:
    """解析 HDR 类型。返回 MT 规则里用的规范名（如 'Dolby Vision P7'）。"""
    low = name.lower()
    # DV 单独出现时容易误伤（如标题里的 DV），只在有分隔符时认
    for tag in _HDR_ORDER:
        t = tag.lower()
        if t == "dv":
            if re.search(r'(?:^|[\.\s_\-\[])dv(?=[\.\s_\-\]]|$)', low):
                return "Dolby Vision"
            continue
        if t == "dovi":
            if re.search(r'(?:^|[\.\s_\-])dovi(?=[\.\s_\-]|$)', low):
                return "Dolby Vision"
            continue
        if t in low:
            return tag
    return None


# 音频：编码 + 声道
_RE_BITRATE = re.compile(r'(?<![0-9])(\d+(?:\.\d+)?)\s*Mbps', re.I)

_AUDIO_CODEC_PATTERNS = [
    ("TrueHD-Atmos", re.compile(r'TrueHD[\s._-]*Atmos|Atmos[\s._-]*TrueHD', re.I)),
    ("DTS-HD MA", re.compile(r'DTS[\s._-]*HD[\s._-]*MA|DTSHD[\s._-]*MA', re.I)),
    ("DTS-HD HRA", re.compile(r'DTS[\s._-]*HD[\s._-]*HRA', re.I)),
    ("DTS:X", re.compile(r'DTS[\s._-]*X(?![A-Za-z])', re.I)),
    ("TrueHD", re.compile(r'TrueHD', re.I)),
    ("DTS", re.compile(r'(?<![A-Za-z])DTS(?![A-Za-z])', re.I)),
    ("E-AC-3", re.compile(r'EAC3|E-AC-3|DDP', re.I)),
    ("AC-3", re.compile(r'(?<![A-Za-z])AC3(?![A-Za-z])|AC-3|(?<![A-Za-z])DD(?![A-Za-z])', re.I)),
    ("FLAC", re.compile(r'(?<![A-Za-z])FLAC(?![A-Za-z])', re.I)),
    ("LPCM", re.compile(r'LPCM', re.I)),
    ("PCM", re.compile(r'(?<![A-Za-z])PCM(?![A-Za-z])', re.I)),
    ("AAC", re.compile(r'(?<![A-Za-z])AAC(?![A-Za-z])', re.I)),
    ("Opus", re.compile(r'(?<![A-Za-z])Opus(?![A-Za-z])', re.I)),
    ("MP3", re.compile(r'(?<![A-Za-z])MP3(?![A-Za-z])', re.I)),
]

# 声道：必须紧跟在音频编码后面，否则 "2.9Mbps" 的 2 会被当成 2 声道
_RE_CHANNELS = re.compile(
    r'(?:TrueHD(?:[\s._-]*Atmos)?'
    r'|DTS(?:HD)?(?:[\s._-]?MA)?(?:[\s._-]*X)?'
    r'|DDP|DD\+|EAC3|E-AC-3|AC3|AC-3|AAC|FLAC|LPCM|PCM|Opus|MP3)'
    r'(?:[\s._-]*Atmos)?'
    r'[\s._-]*([1-9](?:\.[01])?)(?![0-9])',
    re.I,
)


def parse_audio(name: str) -> Dict[str, Any]:
    """解析音频编码与声道。"""
    codec = None
    for tag, pat in _AUDIO_CODEC_PATTERNS:
        if pat.search(name):
            codec = tag
            break
    ch = None
    m = _RE_CHANNELS.search(name)
    if m:
        ch = m.group(1)
    return {"audioCodec": codec, "audioChannels": ch}


# Source（来源）—— 优先级从具体到宽泛
_SOURCE_PATTERNS = [
    ("BluRay Remux", re.compile(r'Blu[\s._-]?Ray[\s._-]*Remux|BluRay[\s._-]*Remux|BDRemux|REMUX', re.I)),
    ("UHD BluRay Remux", re.compile(r'UHD[\s._-]*Blu[\s._-]?Ray[\s._-]*Remux', re.I)),
    ("WEB-DL", re.compile(r'WEB[\s._-]?DL|WEBDL', re.I)),
    ("WEBRip", re.compile(r'WEB[\s._-]?Rip', re.I)),
    ("BluRay", re.compile(r'Blu[\s._-]?Ray|BDRip|Bluray', re.I)),
    ("HDTV", re.compile(r'HDTV', re.I)),
    ("DVDRip", re.compile(r'DVD[\s._-]?Rip|DVDRip', re.I)),
    ("Remux", re.compile(r'(?<![A-Za-z])Remux(?![A-Za-z])', re.I)),
]


def parse_source(name: str) -> Optional[str]:
    low = name
    # 先查更长的组合，避免 "UHD BluRay Remux" 被 "BluRay Remux" 抢先
    for tag, pat in sorted(_SOURCE_PATTERNS, key=lambda x: -len(x[0])):
        if pat.search(low):
            return tag
    return None


# 色深 / 帧率 / 扩展名
_RE_DEPTH = re.compile(r'(?<![0-9])(8|10|12)[\s._-]?bit(?![A-Za-z])', re.I)
_RE_FPS = re.compile(r'(?<![0-9])(\d{2,3}(?:\.\d+)?)\s*fps(?![A-Za-z])', re.I)
_RE_EXT = re.compile(r'\.(strm|mkv|mp4|ts|m2ts|avi|iso|rmvb|wmv|flv|mov)$', re.I)


def parse_video(name: str) -> Dict[str, Any]:
    res = None
    m = _RE_RES.search(name)
    if m:
        v = m.group(1)
        v = {"4K": "2160p", "8K": "4320p"}.get(v.upper(), v)
        res = v[0].upper() + v[1:].lower() if v.lower() != v else v
        # 统一成 MT 的写法：2160p / 1080p / 1080i / 720p
        low = v.lower()
        if low in ("2160p", "4320p", "1080p", "1080i", "720p", "576p", "480p"):
            res = low

    codec = None
    for pat, tag in _CODEC_MAP:
        if pat.search(name):
            codec = tag
            break

    depth = None
    m = _RE_DEPTH.search(name)
    if m:
        depth = "%s-bit" % m.group(1)

    fps = None
    m = _RE_FPS.search(name)
    if m:
        # 90000fps 这类是 HEVC 的 level 上限被写进文件名，不是真实帧率
        fps = _sane(m.group(1), 1.0, 240.0)

    return {"resolution": res, "videoCodec": codec,
            "colorDepth": depth, "frameRate": fps}


# 合理性上下限：超出范围一律视为脏数据返回 None（宁可当作未知，也不拿脏值去算分）。
# 实测踩到过 `156440.4Mbps`（把别的数字误当码率）与 `90000fps`（HEVC level 上限写进了文件名）。
_BR_RANGE = (0.05, 500.0)      # Mbps
_FPS_RANGE = (1.0, 240.0)


def _sane(v, lo, hi):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def parse_bitrate(name: str) -> Optional[float]:
    """解析码率（Mbps）。超出合理范围返回 None。"""
    m = _RE_BITRATE.search(name)
    if not m:
        return None
    try:
        return _sane(float(m.group(1)), *_BR_RANGE)
    except ValueError:
        return None


def parse_ext(name: str) -> Optional[str]:
    m = _RE_EXT.search(name)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------- 汇总

def parse(name: str, real_ext: Optional[str] = None) -> Dict[str, Any]:
    """把一个 strm 文件名解析成规格字典。

    键名与 MT 质量规则的 scoring 维度对齐（驼峰），便于直接喂给评分。
    如果提供了 real_ext（从 strm 内容提取的实际文件后缀如 iso/mkv），优先采用。
    解析不到的维度一律 None —— 绝不猜。
    """
    info: Dict[str, Any] = {"name": name}
    info.update(parse_video(name))
    info.update(parse_audio(name))
    info["hdr"] = parse_hdr(name)
    info["source"] = parse_source(name)
    info["bitrate"] = parse_bitrate(name)
    ext = (real_ext or parse_ext(name) or "").lower()
    info["fileExtension"] = ext or None
    info.update(parse_episode(name))
    return info
