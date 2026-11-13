# -*- coding: utf-8 -*-
"""影视作品安全删除模块：按分类删除。http 删本地 /strm，cloudnas 映射删网盘（MT宿主授权删除）。"""

import os
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("series_core.deleter")

# 绝对禁止删除的受保护关键系统/库根目录
PROTECTED_PREFIXES = {
    "/", "/root", "/vol1", "/volume1", "/mnt", "/app", "/app/data",
    "/strm", "/CloudNAS", "/media", "/data", "/volume1/strm"
}

# 必须包含的影视目录特征词（防止误删非影视目录）
MEDIA_DIR_KEYWORDS = ["strm", "tmdb", "电影", "电视剧", "动漫", "share", "media"]


def is_safe_to_delete(path: str) -> Tuple[bool, str]:
    """安全校验：防止误删根目录或顶级分类目录。"""
    if not path:
        return False, "路径为空"

    clean_p = os.path.abspath(path).replace("\\", "/").rstrip("/")
    if len(clean_p) < 15:
        return False, f"路径过短 ({clean_p})，存在安全风险，拒绝删除"

    # 1. 绝对不能是系统保护目录
    for p_pref in PROTECTED_PREFIXES:
        if clean_p.lower() == p_pref.rstrip("/").lower():
            return False, f"禁止删除受保护的根/分类目录: {clean_p}"

    # 2. 深度校验：必须至少有 3 级深度（例如 /strm/媒体库/电影/华语电影/作品名）
    parts = [p for p in clean_p.split("/") if p]
    if len(parts) < 3:
        return False, f"目录层级过浅 ({len(parts)}层)，拒绝删除: {clean_p}"

    # 3. 必须包含影视特征
    if not any(k in clean_p.lower() for k in MEDIA_DIR_KEYWORDS):
        return False, f"路径不包含影视库合法标识，拒绝删除: {clean_p}"

    return True, ""


def resolve_cloudnas_mount_path(
    local_p: str,
    local_prefix: str = "",
    remote_prefix: str = ""
) -> Optional[str]:
    """将本地 STRM 路径映射为 CloudDrive 在宿主/容器中的真实挂载路径。"""
    clean_p = local_p.replace("\\", "/")
    rel_sub = ""

    if local_prefix and clean_p.startswith(local_prefix):
        rel_sub = clean_p[len(local_prefix):].lstrip("/")
    elif "/strm/" in clean_p:
        idx = clean_p.find("/strm/")
        rel_sub = clean_p[idx + len("/strm/"):].lstrip("/")

    # CloudDrive 候选挂载根路径（涵盖宿主与 Docker 容器环境）
    candidates = [
        "/CloudNAS/CloudDrive",
        "/CloudNAS",
        "/CloudNAS",
        "/cloudnas",
    ]

    for c_root in candidates:
        if rel_sub:
            target = os.path.join(c_root, rel_sub)
        else:
            target = c_root
        if os.path.exists(target):
            return target

    return None


def delete_media_entry(
    local_path: str,
    link_type: str = "http",
    local_prefix: str = "",
    remote_prefix: str = ""
) -> Dict[str, Any]:
    """
    根据链接类型安全删除影视作品目录：
    - http (分享链接): 直接在本地文件系统删除 STRM 目录；
    - cloudnas (云盘链接): 映射至网盘真实挂载路径删除网盘文件，并清理本地 STRM 目录。
    """
    raise RuntimeError("原生删除入口已停用；请通过授权 SDK 删除并等待宿主任务确认")


# ══════════════════════════════════════════════════════════════════════
# 官方 SDK 删除：files.stat 取得 fileRef → files.delete(fileRef)
# http 删除本地 STRM；cloudnas 按显式配置映射云盘目录。
# ══════════════════════════════════════════════════════════════════════

def _sdk_call(fn, **kwargs):
    r = fn(**kwargs)
    return r() if callable(r) else r


def sdk_delete_dir(
    sdk: Any,
    local_path: str,
    link_type: str = "http",
    strm_local_prefix: str = "",
    strm_cloud_prefix: str = "",
    known_roots: Any = None,
    config: Any = None,
) -> Dict[str, Any]:
    """按分类删除作品目录（用户设计 m00681）。返回宿主任务引用；不存在或授权失败均保留异常。

    - http（分享链接分类）：只删本地 STRM 作品目录
      `/strm/云盘影视/电影/***`，经 SDK files.stat→files.delete。
    - cloudnas（本地链接分类）：本地 STRM 目录地址
      `/strm/云盘影视/电影/***` 按 `strm_local_prefix → strm_cloud_prefix`
      映射为网盘目录 `/小小玻珠/云盘影视/电影/***` 后删除网盘真实文件；
      本入口只提交映射源目录的删除任务，不保证本地索引同步清理；
      故只删映射后的网盘目录一次即可（不再二次删本地，避免悬空提示）。
      映射缺失/未命中时拒绝删除并提示配置，不静默转本地删除，防止误删云盘真实文件。
    """
    norm_local = (strm_local_prefix or "").strip().rstrip("/")
    norm_cloud = (strm_cloud_prefix or "").strip().rstrip("/")

    if link_type == "cloudnas":
        target = to_single_mapped_path(local_path, norm_local, norm_cloud)
        # to_mt_path(link_type="cloudnas") 即"作品目录按前缀映射"
        # （STRM 内容绝不参与地址构造，见 submitter.py m00619 设计）。
        try:
            from .submitter import to_mt_path
            target = to_mt_path(
                local_path, link_type="cloudnas",
                strm_local_prefix=norm_local, strm_cloud_prefix=norm_cloud,
            )
        except Exception as e:
            raise ValueError(f"网盘路径映射失败: {e}") from e
        if not norm_cloud or target == (local_path or "").rstrip("/"):
            raise ValueError("cloudnas 删除需要明确配置作品目录映射，当前未生效，拒绝删除")
        return _sdk_delete_path(sdk, target, note="网盘（MT授权删除）", known_roots=known_roots, config=config)

    return _sdk_delete_path(sdk, local_path, note="本地 STRM 目录", known_roots=known_roots, config=config)


def to_single_mapped_path(local_path: str, norm_local: str, norm_cloud: str) -> str:
    p = (local_path or "").strip().rstrip("/")
    if norm_local and norm_cloud and (p == norm_local or p.startswith(norm_local + "/")):
        return norm_cloud + p[len(norm_local):]
    return p


def _sdk_delete_path(sdk: Any, path: str, note: str = "", known_roots: Any = None, config: Any = None) -> Dict[str, Any]:
    """只返回宿主任务，不把 stat 错误或入队解释为删除完成。"""
    safe, msg = is_safe_to_delete(path)
    if not safe:
        raise ValueError(f"安全检查未通过: {msg}")
    from .submitter import get_roots
    roots = get_roots(sdk, known_roots=known_roots, config=config)
    p = (path or "").rstrip("/")
    root = next((r for r in roots if r.get("path") and
                 (p == r["path"].rstrip("/") or p.startswith(r["path"].rstrip("/") + "/"))), None)
    if not root:
        raise ValueError("作品目录没有明确匹配的授权根，拒绝删除")
    rel = p[len(root["path"].rstrip("/")):].strip("/")
    if not rel:
        raise ValueError("禁止删除授权根目录")
    # 包括 NOT_FOUND 在内的错误均保留给调用者，不能当成已成功删除。
    st = sdk.files.stat(root_id=root["id"], path=rel)
    if not isinstance(st, dict) or not st.get("ref"):
        raise ValueError("files.stat 未返回有效 ref，拒绝删除")
    import hashlib
    idem = "delete:" + hashlib.sha256(str(st["ref"]).encode()).hexdigest()
    task = sdk.files.delete(file_ref=st["ref"], recursive=True, idempotency_key=idem)
    if not isinstance(task, dict) or not task.get("ref"):
        raise RuntimeError("删除提交结果未知：宿主未返回 task ref，禁止自动重试")
    return task
