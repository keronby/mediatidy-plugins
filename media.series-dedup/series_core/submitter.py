# -*- coding: utf-8 -*-
"""按链接属性分流提交 MT 整理任务（支持调试模式 Dry-Run）。"""

import logging
import os
import re
from typing import Dict, List, Any, Optional, Tuple

from .scanner import detect_media_type

logger = logging.getLogger("media-series-dedup")


def to_mt_path(
    local_path: str,
    link_type: str = "http",
    profile_id: Any = None,
    dest_dir: str = "",
    cloudnas_path: str = "",  # 仅分类残留，不参与地址构造（m00619）
    strm_local_prefix: str = "",
    strm_cloud_prefix: str = ""
) -> str:
    """转换为 MT 容器可识别的路径。
    核心原则：
    1. 严格剥离季度子目录（Season xx / Specials / SP / 第x季），确保传递给 MT 整理方案的源目录为作品根目录；
    2. 云盘判定唯一依据：link_type（scanner 读 STRM 内容后的 http/cloudnas 分类）；
    3. 云盘任务下只映射作品目录 local_path：剥掉 strm_local_prefix（如 /strm/云盘影视）
       后拼上 strm_cloud_prefix（如 /小小玻珠/云盘影视）。STRM 文件内容
       （cloudnas_path，如 /CloudNAS/.../115open/云盘影视/...）只用于判定
       link_type 是 http 还是 cloudnas，绝不作为提交地址。
    4. 分享直链（http 模式）且目标为本地时，保留本地 STRM 根目录。
    """
    clean_p = (local_path or "").strip()
    norm_local = (strm_local_prefix or "").strip().rstrip("/")
    norm_cloud = (strm_cloud_prefix or "").strip().rstrip("/")

    # 剥离季目录辅助函数：循环剥离任意层级尾部季目录（v1.1.10）
    # 覆盖 Season 1 / Season.01 / Season_1 / S01 / Specials / SP / 第1季 / 特别篇，
    # 以及嵌套 Season 1/Specials 等；且季目录后若还跟文件会被上层先取 dirname。
    _SEASON_TAIL = re.compile(
        r"/(?:Season[\s._-]*\d+|Specials?|SP\d*|S\d{1,3}|OVA\d*|OAD\d*|"
        r"Featurettes|CD[\s._-]*\d+|第\s*[0-9一二三四五六七八九十]+\s*[季部]|特别篇\d*)$",
        re.IGNORECASE,
    )
    def _strip_season(path_str: str) -> str:
        s = (path_str or "").rstrip("/")
        while True:
            nxt = _SEASON_TAIL.sub("", s)
            if nxt == s or not nxt:
                return s
            s = nxt.rstrip("/")

    # 是否针对云盘真实文件进行整理
    # m00619：唯一云盘判定 = scanner 读 STRM 后的分类 link_type。
    # 不用 dest_dir 是否在云盘、不用 clean_p 是否含 /115open 等路径猜测，
    # 否则 http 作品会因 dest 是云盘目录被误映射。
    is_cloud_target = (link_type != "http")

    if is_cloud_target:
        # 用户设计（m00619）：STRM 内容只用于分类 http/cloudnas，不作地址。
        # 提交永远用作品目录地址 local_path（如 /strm/云盘影视/电影/***），
        # 本地分类时把该目录按 strm_local_prefix->strm_cloud_prefix 映射到云盘。
        target_raw = clean_p

        # 若 target_raw 是具体文件路径（以常见后缀结尾），先取其所在目录
        if re.search(r"\.(?:strm|mkv|mp4|avi|iso|ts|mov)$", target_raw, re.IGNORECASE):
            target_raw = os.path.dirname(target_raw)

        target_raw = _strip_season(target_raw)

        if not norm_local or not norm_cloud:
            raise ValueError("云盘操作需要明确配置本地与云盘前缀")
        if target_raw == norm_local or target_raw.startswith(norm_local + "/"):
            return norm_cloud + target_raw[len(norm_local):]
        raise ValueError("作品目录未命中配置的本地前缀，拒绝猜测云盘路径")

        return target_raw

    # 分享直链（本地 STRM 整理任务）：同样只提交作品根，剥季目录
    if re.search(r"\.(?:strm|mkv|mp4|avi|iso|ts|mov)$", clean_p, re.IGNORECASE):
        clean_p = os.path.dirname(clean_p)
    return _strip_season(clean_p)


def submit_tasks(
    evaluated_groups: List[Dict[str, Any]],
    dry_run: bool = True,
    client: Optional[Any] = None,
    sdk: Any = None,
    stop_check_fn=None,
    log_callback=None,
    known_roots: Any = None,
    config: Any = None,
    **kwargs
) -> Dict[str, Any]:
    """批量提交整理任务：逐作品调用 submit_single_task（SDK fileRef 契约）。

    官方契约 organize.manual.run 只接受 files.list 签发的 fileRef，
    因此批量路径与单项路径共用同一条 SDK 路径，不再走 HTTP sourceDir/destDir。
    若未提供 sdk，则按错误返回，绝不回退猜密码的 MTClient。
    """
    if "debug_mode" in kwargs:
        dry_run = bool(kwargs["debug_mode"])
    if "mt_client" in kwargs and kwargs["mt_client"] is not None:
        sdk = kwargs["mt_client"]
    if sdk is None and client is not None and hasattr(client, "organize"):
        sdk = client

    submitted = 0
    simulated = 0
    skipped = 0
    errors = 0
    details = []

    for g in evaluated_groups:
        if stop_check_fn and stop_check_fn():
            break
        tid = g["tmdb_id"]
        mtype = g.get("media_type", "tv")
        for d in g.get("dirs", []):
            if stop_check_fn and stop_check_fn():
                break
            if d.get("disabled") or d.get("link_type") == "mixed":
                skipped += 1
                msg = f"跳过 [TMDB {tid}] {d.get('dir_name')}: {d.get('disabled_reason') or '混合链接'}"
                if log_callback:
                    log_callback(msg)
                continue
            if not d.get("need_organize"):
                skipped += 1
                continue
            pid = int(d.get("assigned_profile_id") or d.get("suggested_profile_id") or 0)
            dst = d.get("assigned_dest_dir") or d.get("suggested_dest_dir") or ""
            if dry_run:
                simulated += 1
                log_msg = f"【调试模式-模拟投递】[方案 {pid}] -> [目标 {dst}] {d.get('dir_path')} ({d.get('diff_reason')})"
                logger.info(log_msg)
                if log_callback:
                    log_callback(log_msg)
                details.append({"status": "simulated", "profileId": pid, "destDir": dst,
                                "dir_path": d.get("dir_path"), "reason": d.get("diff_reason")})
                continue
            if sdk is None:
                errors += 1
                details.append({"status": "error", "dir_path": d.get("dir_path"),
                                "error": "未提供宿主 SDK（ctx.mt），拒绝回退 HTTP 猜密码客户端"})
                continue
            r = submit_single_task(
                local_path=d["dir_path"],
                profile_id=str(pid),
                dest_dir=dst,
                tmdb_id=tid,
                link_type=d.get("link_type", "http"),
                strm_source=d.get("sample_line", "") or d.get("strm_source", ""),
                cloudnas_path=d.get("cloudnas_path", ""),
                strm_local_prefix=kwargs.get("strm_local_prefix", ""),
                strm_cloud_prefix=kwargs.get("strm_cloud_prefix", ""),
                media_type=mtype,
                dry_run=False,
                sdk=sdk,
                known_roots=known_roots if known_roots is not None else kwargs.get("known_roots"),
                config=config if config is not None else kwargs.get("config"),
            )
            if r.get("status") in ("submitted", "completed"):
                submitted += 1
            else:
                errors += 1
            if log_callback:
                log_callback(r.get("message") or r.get("error") or str(r.get("status")))
            details.append(r)

    return {
        "dry_run": dry_run,
        "submitted": submitted,
        "simulated": simulated,
        "skipped": skipped,
        "errors": errors,
        "details": details[:100],
    }


def check_organize_finished(local_path: str, tmdb_id: int, timeout: float = 0.0) -> Tuple[bool, str]:
    """整理任务完成状态由宿主任务系统管理，插件不再轮询本地 FS/SQLite。

    保留签名兼容旧调用方；立即返回"已入队"。旧实现（os.path.exists +
    sqlite3 直读 organize_records）已按官方契约移除。
    """
    return False, "已入队，宿主后台处理中"


def submit_single_task(
    local_path: str,
    profile_id: str,
    dest_dir: str,
    tmdb_id: Any,
    link_type: str = "http",
    strm_source: str = "",
    cloudnas_path: str = "",  # 仅分类残留，不参与地址构造（m00619）
    strm_local_prefix: str = "",
    strm_cloud_prefix: str = "",
    media_type: str = "tv",
    dry_run: bool = False,
    client: Any = None,
    sdk: Any = None,
    known_roots: Any = None,
    config: Any = None,
) -> Dict[str, Any]:
    """单项提交重整任务：严格区分分享链接与本地链接，与 STRM 内容地址精准对应。

    优先走官方 SDK（ctx.mt.organize.manual.run），无需任何登录密码；
    仅在 sdk 未提供时才回退到 HTTP 客户端。
    """
    # 核心映射：根据链接形态与目标方案确定源目录与方案
    if not profile_id or not str(profile_id).strip() or str(profile_id) == "0":
        return {
            "status": "error",
            "error": "未设置整理方案，请在工作台【二、整理方案与路径配置】中选择并保存方案后再提交",
            "message": "未设置整理方案"
        }

    pid = int(profile_id)
    dst = dest_dir if dest_dir is not None else ""

    # m00619：只看分类结果 link_type（来自 scanner 读 STRM 内容判定）。
    # cloudnas_path/strm 内容绝不参与地址构造；strm_*_prefix 只用于映射作品目录。
    is_cloud = (link_type != "http")

    if is_cloud:
        src = to_mt_path(
            local_path,
            link_type="cloudnas",
            profile_id=pid,
            dest_dir=dst,
            cloudnas_path=cloudnas_path,
            strm_local_prefix=strm_local_prefix,
            strm_cloud_prefix=strm_cloud_prefix
        )
        channel_desc = f"☁️ 本地/云盘链接 [方案 {pid}] -> {dst or '默认目标'} (源目录: {src})"
    else:
        # 分享直链：对应本地 STRM 目录
        src = to_mt_path(local_path, link_type="http", profile_id=pid, dest_dir=dst)
        channel_desc = f"🌐 分享直链 [方案 {pid}] -> {dst or '默认目标'} (本地STRM目录: {src})"

    # 严格校验媒体类型：优先使用明确指定的 media_type，若为默认则从路径智能推导 (movie/tv)
    real_media_type = (media_type or "").strip().lower()
    if not real_media_type or real_media_type not in ["movie", "tv"]:
        real_media_type = detect_media_type(local_path)
    elif real_media_type == "tv" and detect_media_type(local_path) == "movie":
        # 路径明确为电影目录（如 /电影/ 或 /华语电影/），强制纠正为 movie，绝不误当作电视剧提交！
        real_media_type = "movie"

    payload = {
        "profileId": pid,
        "tmdbId": int(tmdb_id) if str(tmdb_id).isdigit() else 0,
        "mediaType": real_media_type,
        "sourceDir": src,
        "destDir": dst,
    }

    if dry_run:
        return {
            "status": "simulated",
            "payload": payload,
            "link_type": link_type,
            "strm_source": strm_source,
            "message": f"【调试模式-单项模拟投递】{channel_desc}"
        }

    try:
        if sdk is not None:
            # ── 官方契约：manual.run 只认 files.list 签发的 fileRef ──
            roots = get_roots(sdk, known_roots=known_roots, config=config)
            _manual = sdk.organize.manual

            # 存储类型必须匹配（宿主硬校验：文件与整理方案的存储类型不同）：
            # - cloud 方案（如 1148 媒体入库 storageType=cloud, readStrm=False）：
            #   只能吃云盘真实视频文件（.mkv/.mp4/...），.strm 会被拒。
            #   正确源 = 映射后的云盘路径 src（如 /小小玻珠/云盘影视/...），
            #   且需插件对该云盘根有 files.read 授权（grants roots 含 /小小玻珠）。
            # - local 方案（如 1359 strm整理 storageType=local, readStrm=True）：
            #   只能吃本地 .strm，源 = 本地 STRM 路径。
            # 因此云盘任务优先用云盘映射路径 + video_only；无视频时才
            # 回退本地 .strm（调用方应改用 local 方案，否则宿主仍会拒）。
            work_dir = src
            refs, reason = [], ""
            if is_cloud and match_root(src, roots):
                refs, reason = collect_strm_refs(sdk, roots, src, video_only=True)
                if refs:
                    work_dir = src
            elif not is_cloud and match_root(src, roots):
                # http 分享直链：源 = 本地 STRM 目录，只收 .strm
                refs, reason = collect_strm_refs(sdk, roots, src)
                if refs:
                    work_dir = src
            # m00619 设计边界：云盘任务绝不回退本地 .strm（cloud 方案 readStrm=False
            # 会硬拒 .strm）；本地 .strm 只能走 local 方案（1359 strm整理）。
            # 云盘目录无视频 -> 直接报错，提示改用 local 方案或补云盘文件。
            if not refs and is_cloud:
                return {
                    "status": "error",
                    "payload": payload,
                    "link_type": link_type,
                    "strm_source": strm_source,
                    "work_dir": work_dir,
                    "error": (reason or "cloud 方案下未找到可提交的云盘视频文件") +
                             "（cloud 方案不吃 .strm；如需整理本地 .strm，请改用本地方案 strm整理 提交）",
                    "message": f"【提交失败】{channel_desc}：云盘目录下无可提交视频（cloud 方案不吃 .strm），请改用本地方案或补云盘真实文件",
                }
            if not refs:
                return {
                    "status": "error",
                    "payload": payload,
                    "link_type": link_type,
                    "strm_source": strm_source,
                    "work_dir": work_dir,
                    "error": reason,
                    "message": f"【提交失败】{channel_desc}：{reason}",
                }

            ok_files, failed = [], []
            base = {
                "profileId": pid,
                "tmdbId": int(tmdb_id) if str(tmdb_id).isdigit() else 0,
                "mediaType": real_media_type,
            }
            for it in refs:
                one = dict(base)
                one["fileRef"] = it.get("ref")
                one["idempotencyKey"] = "%s:%s" % (base["tmdbId"], it.get("ref"))
                if real_media_type == "tv":
                    m2 = re.search(r"[Ss](\d{1,4})[Ee](\d{1,5})", str(it.get("name") or ""))
                    if m2:
                        one["season"] = int(m2.group(1))
                        one["episode"] = int(m2.group(2))
                try:
                    _sdk_call(_manual.run, **one)
                    ok_files.append(it.get("name"))
                except Exception as fe:
                    failed.append("%s: %s" % (it.get("name"), fe))

            if not ok_files:
                return {
                    "status": "error",
                    "payload": payload,
                    "link_type": link_type,
                    "strm_source": strm_source,
                    "work_dir": work_dir,
                    "error": "; ".join(failed),
                    "message": f"【提交失败】{channel_desc}：{'; '.join(failed)}",
                }

            finished, fin_detail = check_organize_finished(
                local_path, base["tmdbId"], timeout=0.0
            )
            return {
                "status": "completed" if finished else "submitted",
                "is_finished": finished,
                "finish_detail": fin_detail,
                "submitted_files": len(ok_files),
                "total_files": len(refs),
                "failed_files": failed,
                "work_dir": work_dir,
                "payload": payload,
                "link_type": link_type,
                "strm_source": strm_source,
                "response": {"code": 0, "data": {}},
                "message": (
                    f"【提交成功】{channel_desc}｜{len(refs)} 个文件，成功 {len(ok_files)} 个"
                    + (f"，失败 {len(failed)} 个" if failed else "")
                    + ("，已完成整理" if finished else "，已入队处理中")
                ),
            }

        # 不再回退 HTTP 猜密码客户端：organize.manual.run 必须走 SDK fileRef 契约
        return {
            "status": "error",
            "payload": payload,
            "link_type": link_type,
            "strm_source": strm_source,
            "error": "未提供宿主 SDK（ctx.mt），无法签发 fileRef；请在插件内通过 ctx.mt 调用，不再支持 HTTP 回退",
            "message": f"【提交失败】{channel_desc}：未提供宿主 SDK，拒绝 HTTP 回退",
        }
    except Exception as e:
        logger.error(f"【实机单项投递失败】{channel_desc}: {e}")
        return {
            "status": "error",
            "payload": payload,
            "link_type": link_type,
            "strm_source": strm_source,
            "error": str(e),
            "message": f"【实机单项投递失败】{channel_desc}: {e}"
        }

# ══════════════════════════════════════════════════════════════════════
# 官方契约支持：organize.manual.run 必须传 files.list 签发的 fileRef
# ══════════════════════════════════════════════════════════════════════

def _sdk_call(fn, **kwargs):
    """兼容 SDK 同步返回与被包装成可调用对象两种形态。"""
    r = fn(**kwargs)
    return r() if callable(r) else r


def get_roots(sdk: Any, known_roots: Any = None, config: Any = None) -> List[Dict[str, Any]]:
    """Filter explicit adapter bindings against current host authorization."""
    from .roots import authorized_bindings
    if not isinstance(known_roots, dict):
        raise ValueError("缺少显式授权根绑定，禁止从名称或路径推导")
    bindings = [{"rootId": rid, "mountPath": path} for rid, path in known_roots.items()]
    return authorized_bindings(sdk, bindings)


def match_root(path: str, roots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """返回覆盖该路径的最长匹配授权根。"""
    p = (path or "").rstrip("/")
    if not p:
        return None
    for r in roots:
        rp = str(r.get("path") or "").rstrip("/")
        if rp and (p == rp or p.startswith(rp + "/")):
            return r
    return None


def list_dir(sdk: Any, root_id: str, rel: str, max_pages: int = 8) -> List[Dict[str, Any]]:
    """files.list 分页列出目录内容。"""
    out: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        try:
            res = _sdk_call(sdk.files.list, rootId=root_id, path=rel, page=page)
        except Exception as e:
            logger.warning("files.list 失败 (%s:%s p%d): %s", root_id, rel, page, e)
            break
        if not isinstance(res, dict):
            break
        items = res.get("items") or []
        if not items:
            break
        out.extend(items)
        if not res.get("hasMore"):
            break
    return out


def collect_strm_refs(sdk: Any, roots: List[Dict[str, Any]], work_dir: str, video_only: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """递归收集作品目录下 .strm 的真实引用对象。返回 (refs, 诊断信息)。

    video_only=True 时同时收集视频文件（.mkv/.mp4/...）：云盘真实文件
    必须走 cloud 方案（文件与整理方案的存储类型不同），而 .strm 必须走
    local 方案（strm整理 readStrm=True）。调用方按方案存储类型决定
    传 video_only 还是只取 .strm，绝不混用——混用必被宿主拒绝。
    """
    root = match_root(work_dir, roots)
    if not root:
        return [], "作品目录不在任何授权根内：%s（已授权根：%s）" % (
            work_dir, ", ".join(str(r.get("path")) for r in roots) or "无"
        )

    root_path = str(root.get("path") or "").rstrip("/")
    rel = (work_dir or "").rstrip("/")[len(root_path):].strip("/") or "."

    refs: List[Dict[str, Any]] = []
    queue = [rel]
    seen = set()
    while queue and len(seen) < 60:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        for it in list_dir(sdk, str(root["id"]), cur):
            name = str(it.get("name") or "")
            if it.get("isDir"):
                sub = it.get("path") or ""
                if sub and sub not in seen:
                    queue.append(sub)
                continue
            low = name.lower()
            is_strm = low.endswith(".strm")
            is_video = low.endswith((".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".wmv", ".flv", ".mov", ".webm", ".rmvb", ".mpg", ".mpeg", ".iso"))
            if (is_strm or (video_only and is_video)) and it.get("ref"):
                item = dict(it)
                item["_rootId"] = root.get("id")
                refs.append(item)

    if not refs:
        return [], "作品目录下未找到 .strm 文件：%s" % work_dir
    return refs, ""
