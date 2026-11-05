# -*- coding: utf-8 -*-
"""云盘文件操作层：完全走 MediaTidy 原生 files.* API，不依赖 grpcio。

为什么要换掉 CD2 gRPC：
  grpcio/protobuf 的 wheel 体积大，MT 安装插件时需要在依赖阶段联网拉取；
  本机网络下官方 PyPI 的 grpcio 索引页拉不动，安装会永久卡在「准备依赖」。
  改用 MT 自己的 files.* 后插件零第三方依赖，安装秒级完成。

与 CD2 版的语义差异：
  - CD2 的 delete(paths) 能递归删目录；MT 的 files.delete 需要 fileRef + recursive
  - CD2 的 move(paths, dest_dir) 是"移动到目标目录"；MT 的 files.move 用 fileRef + destination
  - MT 的写操作都是任务式（返回 ref/runId），需要 tasks.wait 或 continuation 续接
    本模块在同步 handler 里用 tasks.wait 阻塞等待；等待前必须先把状态落盘（契约 §2.4）

关键约束（宿主实测）：
  - files.list / files.stat / files.delete / files.move 都是**单次限 500 次调用/运行**
  - tasks.wait 之后必须立刻 return {"waiting": True}，否则 FLOW_WAITING
  - files.roots() 返回项没有 path 字段，需要从授权的 roots 里按 name 反查
"""
from __future__ import annotations

import posixpath
import time
import weakref
from typing import Any, Dict, Iterator, List, Optional, Tuple


# 与旧 CD2 实现保持一致的扩展名集合。判定口径见契约 §2.1：
# 只有视频参与命中率计算，附属文件随作品整体动作。
VIDEO_EXTS = {
    ".mkv", ".mp4", ".ts", ".m2ts", ".iso", ".avi", ".rmvb", ".rm", ".mov",
    ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".m4v", ".divx", ".vob", ".3gp",
}
SIDECAR_EXTS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
    ".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt", ".smi",
    ".mp3", ".flac", ".aac", ".m4a", ".wav", ".ape", ".ogg",
    ".xml", ".json", ".txt", ".log", ".db", ".ini", ".md5", ".sfv",
}


class MtFsError(Exception):
    """云盘操作失败的统一异常。"""


# --------------------------------------------------------------------------- #
# 根目录与路径解析
# --------------------------------------------------------------------------- #
# 一次运行内缓存文件根：files.* 每次运行有调用配额（500 次），
# 而 resolve_root / _root_name / list_dir 都会用到根列表 —— 每列一个目录就调一次
# files.roots 会在扫大目录时把配额打满，报「本次运行的能力调用配额已满」（真机踩过）。
# 用 WeakKeyDictionary：ctx 生命周期结束（运行结束）缓存自动失效，
# 不会因为 id() 被回收复用而串到下一次运行。
_ROOTS_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def list_roots(ctx: Any) -> List[Dict[str, Any]]:
    """列出已授权的文件根。返回 [{id, name, read, write, delete, storageType}]。

    同一次运行（同一个 ctx 对象）只真正调用一次 files.roots。
    """
    try:
        cached = _ROOTS_CACHE.get(ctx)
    except TypeError:
        cached = None
    if cached is not None:
        return cached
    try:
        res = ctx.mt.files.roots()
    except Exception as exc:
        raise MtFsError("读取文件根失败：%s" % exc) from exc
    roots = res if isinstance(res, list) else (res.get("items") or [])
    roots = [r for r in roots if isinstance(r, dict)]
    # 只缓存非空结果：空列表更可能是临时故障，不该被记住
    if roots:
        try:
            _ROOTS_CACHE[ctx] = roots
        except TypeError:
            pass
    return roots


def resolve_root(ctx: Any, path: str) -> Tuple[str, str]:
    """把绝对路径拆成 (rootId, 根内相对路径)。

    MT 的 files.* 需要 rootId + 根内相对路径，用户填的却是 `/115open/新方案`
    这样的完整路径。这里按根名做最长前缀匹配。
    """
    p = (path or "").strip()
    if not p:
        raise MtFsError("路径为空")
    if not p.startswith("/"):
        p = "/" + p

    roots = list_roots(ctx)
    # 根名形如 "115open" / "strm"，匹配 "/115open/..." 的第一段
    best: Optional[Dict[str, Any]] = None
    best_len = -1
    for r in roots:
        name = str(r.get("name") or "").strip().strip("/")
        if not name:
            continue
        if p == "/" + name:
            rel = ""
            n = len(name)
        elif p.startswith("/" + name + "/"):
            rel = p[len(name) + 2:]
            n = len(name)
        else:
            continue
        if n > best_len:
            best, best_len = r, n
    if best is None:
        names = ", ".join(str(r.get("name")) for r in roots) or "(无)"
        raise MtFsError("路径 %s 不在任何已授权的文件根下；可用根：%s。"
                        "请在插件设置里为此目录的根授权读写权限。" % (p, names))
    root_id = str(best.get("id") or "")
    if not root_id:
        raise MtFsError("文件根 %s 缺少 id" % best.get("name"))
    return root_id, rel


def _entry_path(root_name: str, rel: str) -> str:
    rel = rel.strip("/")
    return "/%s" % root_name if not rel else "/%s/%s" % (root_name, rel)


def _norm(items: Any) -> List[Dict[str, Any]]:
    if isinstance(items, dict):
        items = items.get("items") or []
    return [x for x in (items or []) if isinstance(x, dict)]


# --------------------------------------------------------------------------- #
# 读：列目录 / 递归遍历
# --------------------------------------------------------------------------- #
def list_dir(ctx: Any, path: str) -> List[Dict[str, Any]]:
    """列一层。自动翻页（单页上限 100）。返回条目带绝对 path。"""
    root_id, rel = resolve_root(ctx, path)
    root_name = _root_name(ctx, root_id)
    out: List[Dict[str, Any]] = []
    for page in range(1, 201):
        try:
            res = ctx.mt.files.list(rootId=root_id, path=rel, page=page)
        except Exception as exc:
            raise MtFsError("列目录失败 %s：%s" % (path, exc)) from exc
        items = _norm(res.get("items") if isinstance(res, dict) else res)
        for it in items:
            it = dict(it)
            it["path"] = _entry_path(root_name, str(it.get("path") or ""))
            it["_rel"] = str(it.get("path") or "")
            out.append(it)
        more = bool(res.get("hasMore")) if isinstance(res, dict) else False
        if not more or not items:
            break
    return out


def _root_name(ctx: Any, root_id: str) -> str:
    for r in list_roots(ctx):
        if str(r.get("id") or "") == root_id:
            return str(r.get("name") or "").strip().strip("/")
    return ""


class Entry:
    """统一的目录项视图，字段与旧的 Cd2File 对齐，便于 works.py / judge.py 复用。"""

    __slots__ = ("path", "name", "is_dir", "size", "sha1", "ref", "root_id", "modified_at")

    def __init__(self, item: Dict[str, Any], root_id: str = ""):
        self.path = str(item.get("path") or "")
        self.name = str(item.get("name") or posixpath.basename(self.path))
        self.is_dir = bool(item.get("isDir"))
        self.size = int(item.get("size") or 0)
        # MT 只在 cloud 类型的根、且是对文件 stat/list 时才会填充 sha1（实测）
        self.sha1 = str(item.get("sha1") or "").strip().lower()
        self.ref = str(item.get("ref") or "")
        self.root_id = root_id or str(item.get("rootId") or "")
        self.modified_at = str(item.get("modifiedAt") or "")

    @property
    def ext(self) -> str:
        _, dot, tail = self.name.rpartition(".")
        return ("." + tail.lower()) if dot else ""

    @property
    def is_video(self) -> bool:
        return (not self.is_dir) and self.ext in VIDEO_EXTS

    @property
    def is_sidecar(self) -> bool:
        return (not self.is_dir) and self.ext in SIDECAR_EXTS

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<Entry %s%s>" % (self.path, " dir" if self.is_dir else "")


# walk 过程中遇到的失败目录：{id(ctx): [(path, error), ...]}
_WALK_ERRORS: Dict[int, List[Tuple[str, str]]] = {}


def walk_errors(ctx: Any, reset: bool = False) -> List[Tuple[str, str]]:
    """取出本次运行里 walk 失败的目录列表。reset=True 时取完清空。"""
    if reset:
        return _WALK_ERRORS.pop(id(ctx), [])
    return list(_WALK_ERRORS.get(id(ctx), []))


def walk(ctx: Any, root_path: str, max_depth: int = 8,
         on_progress=None, deadline: Optional[float] = None) -> Iterator[Entry]:
    """深度优先遍历，产出 Entry（含目录本身）。

    与旧的 CD2 walk 保持同样的调用形态，方便 works.WorkBuilder 直接消费。
    """
    root_path = (root_path or "").rstrip("/") or "/"
    stack: List[Tuple[str, int]] = [(root_path, 0)]
    count = 0
    while stack:
        cur, depth = stack.pop()
        if deadline is not None and time.time() > deadline:
            return
        try:
            children = list_dir(ctx, cur)
        except MtFsError as exc:
            # 静默跳过会让「某一层整体展开失败」完全不可见（真机踩过：
            # 云盘影视只遍历到 295 个目录，却没有任何报错）。把失败目录记下来，
            # 由调用方通过 walk_errors() 取走。
            _WALK_ERRORS.setdefault(id(ctx), []).append((cur, str(exc)[:200]))
            continue
        for it in children:
            e = Entry(it, str(it.get("rootId") or ""))
            count += 1
            yield e
            if e.is_dir and depth < max_depth:
                stack.append((e.path, depth + 1))
        if on_progress:
            on_progress(count)


def stat(ctx: Any, path: str) -> Optional[Entry]:
    """stat 单个路径，不存在返回 None。"""
    try:
        root_id, rel = resolve_root(ctx, path)
    except MtFsError:
        return None
    try:
        res = ctx.mt.files.stat(rootId=root_id, path=rel)
    except Exception:
        return None
    if not isinstance(res, dict):
        return None
    item = dict(res)
    item["path"] = path
    return Entry(item, root_id)


def find(ctx: Any, parent_path: str, name: str) -> Optional[Entry]:
    """在 parent 下按名字精确找，找不到返回 None。"""
    for it in list_dir(ctx, parent_path):
        if str(it.get("name") or "") == name:
            return Entry(it, str(it.get("rootId") or ""))
    return None


def ref_of(ctx: Any, path: str) -> str:
    """取路径对应的 fileRef；拿不到就抛错（写操作必须先有 ref）。"""
    e = stat(ctx, path)
    if e is None:
        raise MtFsError("路径不存在：%s" % path)
    if not e.ref:
        raise MtFsError("拿不到 %s 的 fileRef" % path)
    return e.ref


# --------------------------------------------------------------------------- #
# 写：删除 / 移动 / 新建目录 / 重命名
# --------------------------------------------------------------------------- #
def _dest(parent_path: str, ctx: Any) -> Dict[str, str]:
    """把绝对目录路径转成写接口要的 destination 对象。"""
    root_id, rel = resolve_root(ctx, parent_path)
    return {"rootId": root_id, "path": rel}


_IDEMPOTENCY_SEQ = {"n": 0}


def _idem(prefix: str) -> str:
    """写操作的幂等键：前缀 + 递增序号，保证同一次运行内不重复。"""
    _IDEMPOTENCY_SEQ["n"] += 1
    return "%s-%d-%d" % (prefix, int(time.time() * 1000), _IDEMPOTENCY_SEQ["n"])


def _wait_task(ctx: Any, res: Any, label: str) -> Dict[str, Any]:
    """写操作统一等待：任务式接口返回 ref，用 tasks.wait(refs=[...]) 等完成。"""
    if not isinstance(res, dict):
        return {"success": True}
    status = str(res.get("status") or "").lower()
    if status in ("done", "success", "completed"):
        return {"success": True, "result": res.get("result")}
    if status in ("failed", "error"):
        return {"success": False, "error": str(res.get("error") or ("%s 失败" % label))}
    ref = str(res.get("ref") or "").strip()
    if not ref:
        if status in ("queued", "running"):
            return {"success": False, "error": "%s 未返回任务 ref，无法确认结果" % label}
        return {"success": True, "result": res.get("result")}
    try:
        w = ctx.mt.tasks.wait(refs=[ref])
    except Exception as exc:
        return {"success": False, "error": "%s 等待失败：%s" % (label, exc)}
    if not isinstance(w, dict):
        return {"success": True}
    st = str(w.get("status") or "").lower()
    ok = st in ("done", "success", "completed")
    out = {"success": ok, "result": w.get("result")}
    if not ok:
        out["error"] = str(w.get("error") or w.get("message") or ("%s 状态 %s" % (label, st)))
    return out


def delete_path(ctx: Any, path: str, recursive: bool = True) -> Dict[str, Any]:
    """删除一个路径（目录需 recursive=True）。

    幂等：路径本来就不存在时视为「已删除」，返回 success=True。
    插件重复执行、或上一轮已删掉同一路径时，不该报成失败。
    """
    try:
        ref = ref_of(ctx, path)
    except MtFsError as exc:
        if "不存在" in str(exc):
            return {"success": True, "alreadyGone": True}
        return {"success": False, "error": str(exc)}
    try:
        res = ctx.mt.files.delete(fileRef=ref, recursive=recursive,
                                  idempotencyKey=_idem("del"))
    except Exception as exc:
        return {"success": False, "error": "删除 %s 失败：%s" % (path, exc)}
    return _wait_task(ctx, res, "删除 %s" % path)


def move_path(ctx: Any, path: str, dest_dir: str, name: str = "") -> Dict[str, Any]:
    """把 path 移到 dest_dir 下。name 为空则沿用原名。"""
    try:
        ref = ref_of(ctx, path)
    except MtFsError as exc:
        return {"success": False, "error": str(exc)}
    kwargs: Dict[str, Any] = {"fileRef": ref, "destination": _dest(dest_dir, ctx),
                              "idempotencyKey": _idem("mov")}
    if name:
        kwargs["name"] = name
    try:
        res = ctx.mt.files.move(**kwargs)
    except Exception as exc:
        return {"success": False, "error": "移动 %s 失败：%s" % (path, exc)}
    return _wait_task(ctx, res, "移动 %s" % path)


def mkdir(ctx: Any, parent: str, name: str) -> Dict[str, Any]:
    """在 parent 下新建目录 name（已存在视为成功）。"""
    try:
        dest = _dest(parent, ctx)
    except MtFsError as exc:
        return {"success": False, "error": str(exc)}
    try:
        res = ctx.mt.files.mkdir(destination=dest, name=name,
                                 idempotencyKey=_idem("mkdir"))
    except Exception as exc:
        if "exist" in str(exc).lower():
            return {"success": True, "existed": True}
        return {"success": False, "error": "新建目录 %s/%s 失败：%s" % (parent, name, exc)}
    out = _wait_task(ctx, res, "新建目录 %s/%s" % (parent, name))
    if not out.get("success") and "exist" in str(out.get("error") or "").lower():
        out["success"] = True
        out["existed"] = True
    return out


def ensure_dir(ctx: Any, path: str) -> bool:
    """逐级确保目录存在，返回是否可用。"""
    path = (path or "").strip().rstrip("/")
    if not path or path == "/":
        return True
    e = stat(ctx, path)
    if e is not None and e.is_dir:
        return True
    parent = path.rsplit("/", 1)[0] or "/"
    name = path.rsplit("/", 1)[-1]
    if not parent or parent == path:
        return False
    if parent != "/" and not ensure_dir(ctx, parent):
        return False
    return bool(mkdir(ctx, parent, name).get("success"))


def rename_path(ctx: Any, path: str, new_name: str) -> Dict[str, Any]:
    """原地改名。"""
    try:
        ref = ref_of(ctx, path)
    except MtFsError as exc:
        return {"success": False, "error": str(exc)}
    parent = path.rsplit("/", 1)[0] or "/"
    try:
        res = ctx.mt.files.rename(fileRef=ref, destination=_dest(parent, ctx),
                                  name=new_name, idempotencyKey=_idem("ren"))
    except Exception as exc:
        return {"success": False, "error": "改名 %s 失败：%s" % (path, exc)}
    return _wait_task(ctx, res, "改名 %s" % path)


def read_text(ctx: Any, path: str, limit: int = 8192) -> str:
    """读小文本文件（strm 用）。"""
    try:
        ref = ref_of(ctx, path)
    except MtFsError:
        return ""
    try:
        res = ctx.mt.files.readText(ref=ref)
    except Exception:
        return ""
    if isinstance(res, dict):
        return str(res.get("text") or "")[:limit]
    return str(res or "")[:limit]
