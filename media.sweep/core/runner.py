# -*- coding: utf-8 -*-
"""执行器：按判定结果删除命中作品，并清理源目录内的空目录。

流程口径（用户确认）：
  · 只删除，不搬迁。比对命中 → 删；未命中 → 留在原处交给后续 AI 识别。
  · 空目录只清到「源目录之内」：源目录下的子目录可以被逐级清理，
    但源目录本身以及它的任何上级目录都不会被删。
  · 若源目录内 100% 命中，其下所有子目录清空后，源目录保留（变成空目录）。

安全边界：
  1. dry_run=True 时只出报告，绝不调用任何写接口
  2. 删除走 files.delete，递归删作品根
  3. 单次上限由调用方传入（默认 200 个作品）
  4. 幂等：同一 job 已处理过的 key 不再重复执行（job_item 有唯一约束）
"""
from __future__ import annotations

import posixpath
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import mtfs
from core.judge import VERDICT_DELETE, VERDICT_KEEP


def _join(a: str, b: str) -> str:
    return posixpath.join(a.rstrip("/") or "/", b.lstrip("/"))


def execute_plan(ctx: Any, works: List[Dict[str, Any]],
                 src_root: str,
                 max_delete: int = 200,
                 dry_run: bool = True,
                 clean_empty: bool = True,
                 on_progress: Optional[Callable[[int, int, str], None]] = None,
                 ) -> Dict[str, Any]:
    """执行删除（不搬迁）。

    返回：
      {"dry_run":bool, "deleted":[...], "kept":[...],
       "errors":[...], "cleaned_dirs":[...]}
    """
    result: Dict[str, Any] = {
        "dry_run": dry_run, "deleted": [], "kept": [], "skipped": [],
        "errors": [], "cleaned_dirs": [],
    }

    todo_del = [w for w in works if w.get("verdict") == VERDICT_DELETE][:max_delete]
    todo_keep = [w for w in works if w.get("verdict") == VERDICT_KEEP]
    total = len(todo_del)
    done = 0

    # 未命中的作品原样留在源目录，交给后续 AI 识别；删除阶段不碰它们。
    for w in todo_keep:
        result["kept"].append({"path": w["path"], "videos": len(w["videos"]),
                               "sidecars": len(w["sidecars"])})
    # 这些是「必须保住」的目录：删空目录时绝不能往上越过它们。
    result["keep_paths"] = [str(w["path"]).rstrip("/") for w in todo_keep]

    # ---------------- 删除：只传作品根，CD2 递归删整棵 ----------------
    if todo_del:
        if dry_run:
            for w in todo_del:
                result["deleted"].append({"path": w["path"], "dry": True,
                                          "videos": len(w["videos"])})
                done += 1
        else:
            # 逐个删除作品根（目录递归删）。逐个而不是批量，便于精确定位失败项，
            # 也避免一次 API 调用失败牵连整批。
            for w in todo_del:
                r = mtfs.delete_path(ctx, w["path"], recursive=True)
                if r.get("success"):
                    result["deleted"].append({"path": w["path"],
                                              "videos": len(w["videos"])})
                else:
                    result["errors"].append("删除失败 %s：%s"
                                            % (w["path"], r.get("error") or "未知原因"))
                done += 1
                if on_progress:
                    on_progress(done, total, "已删除 %d 个作品" % len(result["deleted"]))
        if on_progress:
            on_progress(done, total, "已删除 %d 个作品" % len(result["deleted"]))

    # ---------------- 清理源目录内因删除而变空的父目录 ----------------
    if clean_empty and not dry_run and result["deleted"]:
        result["cleaned_dirs"] = clean_empty_parents(
            ctx, [d["path"] for d in result["deleted"]], src_root,
            keep_paths=result["keep_paths"])

    return result


def clean_empty_parents(ctx: Any, deleted_paths: List[str],
                        src_root: str,
                        keep_paths: Optional[List[str]] = None) -> List[str]:
    """删除后自下而上清理变空的父目录。

    两条硬边界：
      1. 绝不越过 src_root —— 源目录本身及其上级永不删除；
      2. 绝不越过 keep_paths —— 里面是要交给 AI 识别的作品，即使此刻为空也不动。
    比对删除跑完后，能清的空目录一次清干净，不留残壳。
    """
    src_root = src_root.rstrip("/")
    keeps = [k.rstrip("/") for k in (keep_paths or []) if k]
    cleaned: List[str] = []
    seen = set()
    for p in deleted_paths:
        cur = (p.rsplit("/", 1)[0] or "/")
        if any(cur == k for k in keeps):
            continue
        while cur and cur.startswith(src_root + "/") and cur not in seen \
                and not any(cur == k or cur.startswith(k + "/") for k in keeps):
            seen.add(cur)
            try:
                children = mtfs.list_dir(ctx, cur)
            except mtfs.MtFsError:
                break
            if children:
                break
            parent = cur.rsplit("/", 1)[0] or "/"
            if not mtfs.delete_path(ctx, cur, recursive=True).get("success"):
                break
            cleaned.append(cur)
            cur = parent
    return cleaned
