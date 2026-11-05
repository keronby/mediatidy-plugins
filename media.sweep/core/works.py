# -*- coding: utf-8 -*-
"""作品层识别。

实测结构（2026-10-29，/115open/新方案）：
    新方案/rEmux/作品名 (2018) {tmdb-535170}/Season 1/E01.mkv
顶层 `rEmux` 是"分组目录"，不是作品；作品层在它下面。

识别规则：
  一旦某目录被识别为作品层，其整个子树都归属该作品，不再往下找作品。
  判断顺序（对每个目录）：
    1. 目录名含 {tmdb-N} / {tvdb-N} / {imdb-ttN}   → 作品层
    2. 目录名匹配 "名字 (年份)"                     → 作品层
    3. 该目录下直接有 Season N / Specials 子目录     → 作品层
    4. 该目录下直接有视频文件                        → 作品层
    5. 都不是 → 是"分组目录"，继续下钻
  超过 max_depth 仍未识别 → 记为未识别，不动。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

RE_TAG = re.compile(r"\{[^}]*\b(?:tmdb|tvdb|imdb)[-_]?[\w\-]*\}", re.I)
RE_YEAR = re.compile(r"[\(\[]\s*(19|20)\d{2}\s*(?:[-–~]\s*(?:19|20)\d{2}\s*)?[\)\]]")
RE_SEASON = re.compile(r"^(?:season\s*\d+|specials?|s\d{1,2}|第\s*\d+\s*季)$", re.I)


def looks_like_work_name(name: str) -> bool:
    """按目录名判断（规则 1、2）。"""
    if not name:
        return False
    return bool(RE_TAG.search(name) or RE_YEAR.search(name))


def is_season_dir(name: str) -> bool:
    return bool(RE_SEASON.match((name or "").strip()))


# 发布目录名特征：不含年份、但带分辨率/编码/音轨等发布标签。
# 例如 The.Cleaning.Lady.S01.1080p.NF.WEB-DL.DDP.5.1.H.264-CHDWEB。
# 这种目录「直接含视频」但绝不是作品根，作品根在它上面一层。
RE_RELEASE = re.compile(
    r"(?i)(?:^|[ ._\-])(?:\d{3,4}p|4k|uhd|web[ ._\-]?dl|webrip|bluray|blu[ ._\-]?ray|"
    r"remux|hdtv|dvdrip|bdrip|x26[45]|h[ ._\-]?26[45]|hevc|avc|xvid|divx|"
    r"ddp|dd[ ._\-]?[257]|aac|ac3|eac3|dts|flac|atmos|truehd|"
    r"hdr|sdr|dv|dolby[ ._\-]?vision|10bit|8bit)(?:$|[ ._\-])"
)
# 季集标记：S01E01、S01、E01、第1季 等
RE_EPISODE_TAG = re.compile(r"(?i)(?:^|[ ._\-])S\d{1,3}(?:E\d{1,4})?(?:$|[ ._\-])")


def is_release_dir(name: str) -> bool:
    """目录名像「发布/打包目录」而不是作品名。"""
    name = (name or "").strip()
    if not name:
        return False
    return bool(RE_RELEASE.search(name) or RE_EPISODE_TAG.search(name))


def clean_title(name: str) -> str:
    t = RE_TAG.sub("", name or "")
    t = RE_YEAR.sub("", t)
    return re.sub(r"\s+", " ", t).strip(" -_.")


class WorkBuilder:
    """两遍式：先 feed 所有条目，再 works()。

    feed 顺序无关：内部先记录结构，最后统一解析归属。
    """

    def __init__(self, source_root: str, max_depth: int = 4) -> None:
        self.source_root = (source_root or "/").rstrip("/") or "/"
        self.max_depth = max_depth

        self._files: Dict[str, Any] = {}          # path -> entry (文件)
        self._dirs: Set[str] = set()              # 所有目录路径
        self._children: Dict[str, List[str]] = {}  # dir -> 直接子项名
        self._stats = {"files": 0, "dirs": 0, "videos": 0, "sidecars": 0, "other": 0}
        self.unrecognized: List[str] = []

    # ------------------------------------------------------------------ #
    def feed(self, entry: Any) -> None:
        path = (entry.path or "").rstrip("/")
        if not path or path == self.source_root:
            return
        parent = path.rsplit("/", 1)[0] or "/"
        self._children.setdefault(parent, []).append(entry.name)
        self._children.setdefault(path, [])
        if entry.is_dir:
            self._dirs.add(path)
            self._stats["dirs"] += 1
            return
        self._files[path] = entry
        self._stats["files"] += 1
        if getattr(entry, "is_video", False):
            self._stats["videos"] += 1
        elif getattr(entry, "is_sidecar", False):
            self._stats["sidecars"] += 1
        else:
            self._stats["other"] += 1

    # ------------------------------------------------------------------ #
    def _depth(self, path: str) -> int:
        base = self.source_root
        if path == base:
            return 0
        if not path.startswith(base + "/"):
            return -1
        return path[len(base) + 1:].count("/") + 1

    def _direct_files(self, path: str) -> List[Any]:
        out = []
        for e in self._files.values():
            if e.path.rsplit("/", 1)[0] == path:
                out.append(e)
        return out

    def _is_work_dir(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        if looks_like_work_name(name):          # 规则 1、2
            return True
        kids = self._children.get(path, [])
        for k in kids:                          # 规则 3：含 Season N 子目录
            if is_season_dir(k):
                return True
        # 规则 3.5：含「发布目录」子目录（1080p/WEB-DL/S01 之类）→ 本层是作品层，
        # 内层那个发布目录不是。典型：作品名/The.Cleaning.Lady.S01.1080p.WEB-DL.../
        for k in kids:
            if is_release_dir(k):
                return True
        if is_release_dir(name):
            # 自己就是发布目录（1080p / S01 / WEB-DL …）→ 不可能是作品根，
            # 让 _resolve_work_root 继续向上找真正的作品层。
            return False
        for f in self._direct_files(path):      # 规则 4：直接含视频
            if getattr(f, "is_video", False):
                return True
        return False

    def _resolve_work_root(self, path: str) -> Optional[str]:
        """从 path（文件或目录）向上找作品层目录。

        特殊处理：`作品/Season 1/E01.mkv` 里的 `Season 1` 目录本身
        可能因"直接含视频"而被判为作品层，但它其实是父作品的季目录。
        因此向上收集所有候选，取**最外层**那个（仍在源根之内）。
        """
        cur = path.rsplit("/", 1)[0] if path in self._files else path
        base = self.source_root
        found: Optional[str] = None
        guard = 0
        while cur and cur != base and cur.startswith(base + "/") and guard < 16:
            guard += 1
            if self._is_work_dir(cur):
                found = cur          # 继续向上，取最外层的作品层
            cur = cur.rsplit("/", 1)[0]
        return found

    # ------------------------------------------------------------------ #
    def works(self) -> List[Dict[str, Any]]:
        """解析所有文件归属，产出作品列表。"""
        groups: Dict[str, Dict[str, Any]] = {}
        for path, f in self._files.items():
            work = self._resolve_work_root(path)
            if work is None:
                d = self._depth(path)
                if d > self.max_depth:
                    if d and path not in self.unrecognized:
                        self.unrecognized.append(path)
                    continue
                # 源根下的散落文件 → 单文件自成一体
                parent = path.rsplit("/", 1)[0]
                if parent == self.source_root and getattr(f, "is_video", False):
                    work = path
                else:
                    if path not in self.unrecognized:
                        self.unrecognized.append(path)
                    continue
            g = groups.get(work)
            if g is None:
                g = {"path": work, "videos": [], "sidecars": [], "is_loose": False}
                groups[work] = g
            if getattr(f, "is_video", False):
                g["videos"].append(f)
            else:
                g["sidecars"].append(f)

        # 没有任何文件的作品目录（空作品）也要进报告，标 no-video 不动
        for path in self._dirs:
            if path in groups:
                continue
            if not self._is_work_dir(path):
                continue
            if any(c.startswith(path + "/") for c in groups):
                continue
            groups[path] = {"path": path, "videos": [], "sidecars": [], "is_loose": False}

        # 同一个作品可能因不同文件解析出不同的作品根（例如作品根自身目录被
        # 判为作品、其 Season 子目录也被判为作品）。凡是路径位于另一个作品
        # 路径之下的，一律合并进最外层那个作品 —— 否则同一个剧会被拆成两条
        # 记录，季目录还可能被单独判成「全部命中」而整包删除。
        def _outermost(path: str) -> str:
            cur = path
            best = path
            while cur and cur != self.source_root and cur.startswith(self.source_root + "/"):
                if cur in groups:
                    best = cur
                cur = cur.rsplit("/", 1)[0]
            return best

        merged: Dict[str, Dict[str, Any]] = {}
        for path, g in groups.items():
            root = _outermost(path)
            tgt = merged.get(root)
            if tgt is None:
                tgt = {"path": root, "videos": [], "sidecars": [], "is_loose": False}
                merged[root] = tgt
            tgt["videos"].extend(g["videos"])
            tgt["sidecars"].extend(g["sidecars"])
            tgt["is_loose"] = tgt["is_loose"] or (root in self._files)
        groups = merged

        out: List[Dict[str, Any]] = []
        for path, g in groups.items():
            is_loose = g["path"] in self._files
            name = g["path"].rsplit("/", 1)[-1]
            out.append({
                "path": path,
                "name": name,
                "title": clean_title(name),
                "videos": sorted(g["videos"], key=lambda x: x.path),
                "sidecars": sorted(g["sidecars"], key=lambda x: x.path),
                "is_loose": is_loose,
            })
        out.sort(key=lambda x: x["path"])
        return out

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)
