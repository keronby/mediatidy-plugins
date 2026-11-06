# -*- coding: utf-8 -*-
"""按链接属性分流提交 MT 整理任务（支持调试模式 Dry-Run）。"""

import logging
from typing import Dict, List, Any, Optional

from core.tmdb import MTClient

logger = logging.getLogger("media-series-dedup")


def to_mt_path(local_path: str, link_type: str = "http", cloudnas_path: str = "") -> str:
    """转换为 MT 容器可识别的路径。"""
    clean_p = (local_path or "").strip()

    if link_type == "cloudnas":
        if cloudnas_path:
            return cloudnas_path

        # 智能剥离或映射 CD2 挂载路径
        for prefix in [
            "/vol1/1000/CloudNAS/CloudDrive",
            "/CloudNAS/CloudDrive",
            "/vol1/1000/CloudNAS",
            "/CloudNAS",
            "/cloudnas",
        ]:
            if clean_p.startswith(prefix):
                sub = clean_p[len(prefix):]
                return sub if sub.startswith("/") else "/" + sub

        strm_sub = ""
        if clean_p.startswith("/vol1/1000/strm"):
            strm_sub = clean_p[len("/vol1/1000/strm"):]
        elif clean_p.startswith("/strm"):
            strm_sub = clean_p[len("/strm"):]

        if strm_sub:
            if not strm_sub.startswith("/"):
                strm_sub = "/" + strm_sub
            return strm_sub

        return clean_p

    else:
        # 本地整理：剥离 /vol1/1000
        if clean_p.startswith("/vol1/1000/strm"):
            return clean_p[len("/vol1/1000"):]
        elif clean_p.startswith("/vol1/1000/"):
            return clean_p[len("/vol1/1000"):]
        return clean_p


def submit_tasks(
    evaluated_groups: List[Dict[str, Any]],
    dry_run: bool = True,
    client: Optional[MTClient] = None,
    stop_check_fn=None,
    log_callback=None
) -> Dict[str, Any]:
    """批量提交或模拟提交整理任务。"""
    client = client or MTClient()
    submitted = 0
    simulated = 0
    skipped = 0
    details = []

    for g in evaluated_groups:
        if stop_check_fn and stop_check_fn():
            break

        tid = g["tmdb_id"]
        mtype = g.get("media_type", "tv")

        for d in g.get("dirs", []):
            if stop_check_fn and stop_check_fn():
                break

            # 1. 跳过禁用或混合链接
            if d.get("disabled") or d.get("link_type") == "mixed":
                skipped += 1
                msg = f"跳过 [TMDB {tid}] {d['dir_name']}: {d.get('disabled_reason') or '混合链接'}"
                if log_callback:
                    log_callback(msg)
                continue

            # 2. 跳过无需重整的一致目录
            if not d.get("need_organize"):
                skipped += 1
                continue

            # 3. 准备投递参数
            pid = int(d["suggested_profile_id"])
            dst = d["suggested_dest_dir"]
            src = to_mt_path(d["dir_path"], link_type=d["link_type"], cloudnas_path=d.get("cloudnas_path", ""))

            payload = {
                "profileId": pid,
                "tmdbId": tid,
                "mediaType": mtype,
                "sourceDir": src,
                "destDir": dst,
            }

            if dry_run:
                simulated += 1
                log_msg = f"【调试模式-模拟投递】[方案 {pid}] -> [目标 {dst}] 来源: {src} ({d.get('diff_reason')})"
                logger.info(log_msg)
                if log_callback:
                    log_callback(log_msg)
                details.append({
                    "status": "simulated",
                    "payload": payload,
                    "reason": d.get("diff_reason")
                })
            else:
                try:
                    res = client.request("/api/organize/manual", payload=payload, method="POST")
                    submitted += 1
                    log_msg = f"【实机投递成功】[方案 {pid}] 来源: {src} 受理ID: {res.get('data', {}).get('id')}"
                    logger.info(log_msg)
                    if log_callback:
                        log_callback(log_msg)
                    details.append({
                        "status": "submitted",
                        "payload": payload,
                        "response": res
                    })
                except Exception as e:
                    log_msg = f"【实机投递失败】[方案 {pid}] 来源: {src} 错误: {e}"
                    logger.error(log_msg)
                    if log_callback:
                        log_callback(log_msg)
                    details.append({
                        "status": "error",
                        "payload": payload,
                        "error": str(e)
                    })

    return {
        "dry_run": dry_run,
        "submitted": submitted,
        "simulated": simulated,
        "skipped": skipped,
        "details": details[:100],  # 截断避免超出 storage 限制
    }
