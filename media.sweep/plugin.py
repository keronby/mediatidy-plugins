# -*- coding: utf-8 -*-
"""media.sha1-dedup · sha1 比对删除插件

作用：对云盘某个未识别目录，用本地 sha1 归约库比对；
      视频全部命中（= 已整理过）的作品整包删除，
      未命中的作品按原始结构搬到另一个目录。

契约见 docs/设计与交互契约.md
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from typing import Any, Dict, List, Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from mt_plugin import Context, Plugin as PluginBase, action, query, ui

from ai import AiIdentifyMixin, validate_prompt

from core import sources
from core import mtfs
from core.judge import judge_works, mixed_works, summarize
from core.library import Sha1Library
from core.runner import execute_plan
from core.settings import apply_settings, load_settings
from core.works import WorkBuilder

KV_CFG = "config"
KV_META = "meta"
KV_BATCH = "batch"          # 当前批次的冻结快照（扫描时刻的文件清单）
BATCH_SCHEMA = 1

LIB_NAME = "sha1lib.sqlite3"

# 用户口径（2026-10-30）：目录 / 方案这些值只在插件页面里选和存，
# 「授权和设置」界面不再放这些目录设置。所以 DEFAULT_CONFIG 只留安全默认，
# 真实值优先取页面保存的 settings（storage KV 键 "settings"），其次清单配置。
DEFAULT_CONFIG: Dict[str, Any] = {
    "mtDbPath": "/app/data/mediatidy.db",
    "historyPath": "",
    "strmRoots": "/strm/shareStrm,/strm/云盘影视",
    "cloudRoots": "",
    "cloudDepth": 12,
    "scanMinutes": 20,                 # 单次扫描时间上限（分钟），到点停下留待下次续跑
    "srcRoot": "",
    "maxDelete": 200,
    "maxDepth": 4,
    "cleanEmpty": True,
    "autoExecute": False,
    "profileId": "",
    "aiReview": True,
}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def kv_get(ctx: Context, key: str) -> Any:
    try:
        val = ctx.mt.storage.get(key=key)
        if isinstance(val, dict) and "value" in val:
            val = val["value"]
        if isinstance(val, str) and val[:1] in "[{":
            try:
                val = json.loads(val)
            except Exception:
                pass
        return val
    except Exception:
        return None


def kv_put(ctx: Context, key: str, value: Any) -> None:
    try:
        ctx.mt.storage.set(key=key, value=value)
    except Exception as exc:
        try:
            ctx.mt.log(level="error", message="写入存储 [%s] 失败：%s" % (key, exc))
        except Exception:
            pass


def _manifest_config(ctx: Context) -> Dict[str, Any]:
    """读取宿主注入的清单配置（用户可在插件设置页直接填 CD2 Token）。"""
    for attr in ("config", "plugin_config", "settings"):
        try:
            val = getattr(ctx, attr, None)
        except Exception:
            val = None
        if isinstance(val, dict) and val:
            return val
    return {}


def _cfg(ctx: Context) -> Dict[str, Any]:
    """合并三层配置，后者覆盖前者。

    1. DEFAULT_CONFIG  —— 安全默认值
    2. 清单配置        —— 宿主设置页（用户要求这里不再放目录设置，通常为空）
    3. 页面保存的设置  —— 插件页面里「保存设置」写进 storage KV 的值，优先级最高

    之所以让页面保存的设置压过清单配置：用户口径是「目录和整理方案的选取和保存」
    都在插件页面里做，如果清单里的旧值一直压着，页面上改了也不生效。
    """
    c = dict(DEFAULT_CONFIG)
    for k, v in _manifest_config(ctx).items():
        if v is not None and v != "":
            c[k] = v
    # load_settings 只调一次：它内部要走 storage KV，而 init 的预算只有 10 秒，
    # 之前这里连调两次，实测让 init 卡到 9 秒多直接超时（插件进 status:error，
    # 之后所有调用都 403，看起来像「权限没给」）。
    saved = load_settings(ctx)
    for k, v in saved.items():
        if v is not None and v != "":
            c[k] = v
    # 老版本把整包配置存进了 "config" 键，兼容读一次（settings 优先）
    if not saved:
        stored = kv_get(ctx, KV_CFG)
        if isinstance(stored, dict):
            for k, v in stored.items():
                if v is not None and v != "":
                    c.setdefault(k, v)
    return c


def resolve_profile_id(ctx: Context, value: Any) -> str:
    """把「方案 ID」或「方案名」解析成 MT 整理方案 id。

    用户更容易记住「阿里秒传」这样的名字，所以两种都接受；
    纯数字直接当 ID 用，其余当作名字，调用 MT 的 organize.read 查真实 ID。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return raw
    try:
        result = ctx.mt.organize.profiles()
    except Exception as exc:  # 没权限/宿主异常时给出可读日志，不静默失败
        _log(ctx, "读取整理方案失败：%s" % exc, "error")
        return ""
    # 返回形状在不同宿主版本里可能是 list / {items:[...]} / {profiles:[...]}
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        items = result.get("items") or result.get("profiles") or []
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() == raw:
            return str(item.get("id") or "")
    _log(ctx, "整理方案列表为空：宿主只返回已授权给本插件的方案。"
              "请在插件授权里勾选要用的方案，或直接把配置填成方案 ID（如 954）。", "error")
    return ""


def _as_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "")
    parts = []
    for chunk in s.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _as_int(v: Any, default: int) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _log(ctx: Context, msg: str, level: str = "info") -> None:
    try:
        ctx.mt.log(level=level, message=msg)
    except Exception:
        pass


# 归约库的「固定存放位置」。
#
# 为什么不只放 ctx.data_dir：MT 每次卸载/重装插件都会换一个 instanceId，
# ctx.data_dir 跟着换（plugins/data/<instanceId>/），旧库就成孤儿 —— 开发期
# 反复重装会把 80 万条的库丢好几次（真机踩过，一次 130 秒重建）。
# 所以库默认落到容器里一个**固定路径**，跨实例、跨版本存活；
# 若该位置不可写（宿主收紧权限等），自动回退到 ctx.data_dir，功能不受影响。
LIB_PERSIST_PATHS = (
    "/app/data/plugins/data/.media.sweep/sha1lib.sqlite3",
    "/app/data/media.sweep/sha1lib.sqlite3",
)


def _lib_path(ctx: Context) -> str:
    """挑一个可写的位置放归约库；优先固定路径，失败回退实例 data_dir。"""
    for candidate in LIB_PERSIST_PATHS:
        folder = os.path.dirname(candidate)
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".writetest")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return candidate
        except Exception:
            continue
    data_dir = getattr(ctx, "data_dir", None) or os.path.join(CURRENT_DIR, ".data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        pass
    return os.path.join(data_dir, LIB_NAME)


def _lib(ctx: Context) -> Sha1Library:
    return Sha1Library(_lib_path(ctx))


def migrate_library(ctx: Context) -> None:
    """把老位置的库迁到固定路径（只做一次，幂等）。

    触发场景：从「库在 plugins/data/<instanceId>/」的老版本升级上来。
    搬的是库文件本体以及 -wal/-shm，避免 WAL 里的事务丢失。
    """
    target = LIB_PERSIST_PATHS[0]
    if os.path.exists(target):
        return
    data_dir = getattr(ctx, "data_dir", None)
    if not data_dir:
        return
    src = os.path.join(data_dir, LIB_NAME)
    if not os.path.exists(src):
        return
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(src + suffix):
                shutil.copy2(src + suffix, target + suffix)
        try:
            ctx.mt.log(level="info", message="归约库已从实例目录迁移到固定路径：%s" % target)
        except Exception:
            pass
    except Exception as exc:
        try:
            ctx.mt.log(level="error", message="归约库迁移失败（继续用实例目录）：%s" % exc)
        except Exception:
            pass




# --------------------------------------------------------------------------- #
# 插件主体
# --------------------------------------------------------------------------- #
class Plugin(AiIdentifyMixin, PluginBase):
    """整理清道夫：sha1 归约库 → 比对删除 → 剩余交 AI 识别。

    三个分区（用户 2026-10-30 口径）：
      Tab1「AI 识别」    —— 套用原石界面，另加「手动 sha1 匹配」
      Tab2「比对处理」  —— sha1 比对 + AI 后续动作 + 定时流程 + 目录/方案选取保存
      Tab3「sha1 数据库」—— 库状态、建库/同步、库来源目录设定
    """

    @staticmethod
    def library_path(ctx: Context) -> str:
        """归约库的绝对路径（固定路径优先）。供 AI 识别分区的 sha1 过滤复用。"""
        return _lib_path(ctx)

    def validate_config(self, config: dict, ctx: Context) -> Optional[Dict[str, Any]]:
        # 目录 / 方案这些值改在插件页面里选和存（见 core/settings.py），
        # 所以清单配置校验只检查提示词，不再强制要求 srcRoot。
        return validate_prompt(config or {})

    def on_init(self, ctx: Context) -> None:
        # init 只有 10 秒预算（官方指南 §11），这里任何一步超时都会让插件进入
        # status:error，之后所有调用都被 403 —— 排查起来像「权限没给」。
        # 所以把每一步的耗时都打出来，超时能直接看出卡在哪。
        t0 = time.time()
        def lap(name: str) -> None:
            _log(ctx, "init %s %.2fs" % (name, time.time() - t0))
        try:
            cfg = _cfg(ctx)
            lap("配置")
            # KV_CFG 只是老版本的兼容副本，init 里没必要每次都写
            # （storage.set 也可能慢，而 init 只有 10 秒预算）。
            # 页面保存时 save_settings 已经写过，这里只在完全没有时补一份。
            try:
                if not kv_get(ctx, KV_CFG):
                    kv_put(ctx, KV_CFG, cfg)
                    lap("写配置")
            except Exception:
                pass
            meta = kv_get(ctx, KV_META)
            if not isinstance(meta, dict) or not meta:
                kv_put(ctx, KV_META, {"state": "idle", "updatedAt": "%s｜就绪" % _now()})
            # 首次切到固定路径时，把当前实例目录里已有的库搬过去，
            # 免得升级一次就丢一次历史数据。
            migrate_library(ctx)
            lap("迁移库")
            # 建库（幂等）
            lib = _lib(ctx)
            lap("打开库")
            try:
                if not lib.get_meta("created_at"):
                    lib.set_meta("created_at", _now())
            finally:
                lib.close()
            lap("完成")
        except Exception:
            pass

    def on_stop(self, ctx: Context) -> None:
        # 不能在这里禁用定时：on_stop 在每次禁用/重启/升级时都会触发，
        # 写 enabled=False 会把用户在 MT 页面手动启用的定时又关掉。
        # 定时是否启用由用户在 MT 页面控制，这里什么都不做。
        pass

    # ------------------------------------------------------------------ #
    # 页面
    # ------------------------------------------------------------------ #
    def get_page(self, ctx: Context):
        """三个宽 Tab（用户 2026-10-30 口径）。

        Tab1「AI 识别」     —— 原石界面原样搬过来（选择片源/整理设置/本次识别/识别结果），
                              另加「手动 sha1 匹配」：拿本批快照去库里比对，命中即说明
                              是 MT 整理过的，直接列出来并可选做一次识别核对。
        Tab2「比对处理」     —— sha1 比对 → 删除 → 剩余交 AI 的完整流水线，
                              加上定时流程、目录与整理方案的选取与保存。
        Tab3「sha1 数据库」  —— 库状态 / 建库同步 / 库来源目录设定（三个来源目录挪到这里）。
        默认打开 Tab1。
        """
        from ai.page import recognition_sections

        def col(label: str, field: str) -> Dict[str, Any]:
            return {"label": label, "cells": [ui.Text(text=ui.ref("row." + field))]}

        running = ui.expr("includes", ["collecting", "comparing", "executing", "queued", "running"],
                          ui.ref("run.status"))

        lib_table = ui.Table(
            columns=[col("来源", "name"), col("条数", "rows"), col("最近刷新", "updated")],
            source=ui.ref("query.overview.data.sources"),
            pageSize=20,
        )

        report_table = ui.Table(
            columns=[col("作品", "work"), col("判定", "verdict"),
                     col("视频命中", "hits"), col("目标", "dest")],
            source=ui.ref("query.overview.data.works"),
            pageSize=50,
        )

        # 原石界面（AI 识别）的区块，整块搬进 Tab1
        ai = recognition_sections()
        ai_sections = ai["sections"]

        def has_set(key: str):
            return ui.expr("gt", ui.expr("length", ui.expr("coalesce", ui.ref("query.overview.data.sets." + key), "")), 0)

        # ---------------------------------------------------------------- #
        # Tab1 · AI 识别（原石界面 + 手动 sha1 匹配）
        # ---------------------------------------------------------------- #
        zone_ai = ui.Tab(
            *ai_sections,
            ui.Section(
                ui.Text(text="这一页是**手动处理**的入口：勾好文件、选好方案，直接开始识别。"
                             "下面的 sha1 开关决定这次手动处理要不要先过一遍归约库。",
                        tone="muted", size="sm"),
                ui.Divider(),
                ui.Checkbox(label="先用 sha1 归约库过滤（命中＝已整理过，本次不送 AI 识别）",
                            bind="state.aiSha1Gate"),
                ui.Text(text="开关打开时：先用库里的 sha1 判断这批文件，命中的直接标为「已整理过」并跳过，"
                             "只把未命中的交给 AI；开关关闭时：全部都送 AI 识别。"
                             "两种都不会删文件 —— 删除只发生在「比对处理」那一页。",
                        tone="muted", size="sm"),
                ui.Text(text=ui.ref("query.overview.data.sha1GateLine"), tone="muted", size="sm"),
                ui.Stack(
                    ui.Button(label="🔎 只看 sha1 比对结果（不调 AI）", variant="outline",
                              onClick=[{"type": "invoke", "actionId": "dry-run"}]),
                    # 两个按钮分别固定开关值：宿主会把 input 里的键原样校验，
                    # 塞 ui.ref(...) 有时求值成整个 state（含 aiSha1Gate 这类未声明键），
                    # 宿主就报「配置包含未知字段或超限内容」。写死布尔最稳。
                    ui.Button(label="🤖 开 sha1 过滤，开始手动识别", variant="ghost",
                              onClick=[{"type": "invoke", "actionId": "recognize",
                                        "input": {"manual": True, "useSha1": True}}]),
                    ui.Button(label="🚫 关 sha1 过滤，全部送 AI", variant="outline",
                              onClick=[{"type": "invoke", "actionId": "recognize",
                                        "input": {"manual": True, "useSha1": False}}]),
                    direction="row", gap="sm",
                ),
                title="🔗 sha1 过滤开关 · 手动处理",
            ),
            label="🤖  AI 识别",
            id="tab-ai",
        )

        # ---------------------------------------------------------------- #
        # Tab2 · 比对处理（流水线 + 定时 + 目录/方案保存）
        # ---------------------------------------------------------------- #
        zone_flow = ui.Tab(
            ui.Section(
                ui.Alert(text="⏳ 正在后台处理，按钮已锁定…", tone="warning", visible=running),
                ui.Text(text="第 1 步 · 扫描源目录并冻结本批快照；第 2 步 · 按视频 sha1 与归约库比对，"
                             "命中即视为已整理过、整包删除；第 3 步 · 剩下的原地保留，交给 AI 识别。"),
                ui.Text(text="扫描完成后这一批就固定了，之后新进云盘的文件不会被拉进本批，"
                             "也不会插队到 AI 识别。空目录只清到源目录之内，源目录本身不会被删。"),
                ui.Text(text=ui.ref("query.overview.data.srcRoot")),
                ui.Stack(
                    ui.Button(label="▶️ 一键跑完（扫描→比对删→AI 识别）",
                              onClick=[{"type": "invoke", "actionId": "cleanup-all"}], variant="default"),
                    ui.Button(label="⚠️ 一键跑完并真删（需确认）",
                              onClick=[{"type": "invoke", "actionId": "cleanup-all", "input": {"confirm": "YES"}}],
                              variant="destructive"),
                    direction="row", gap="sm",
                ),
                ui.Divider(),
                ui.Stack(
                    ui.Button(label="🔍 只预演比对（不动文件，会冻结快照）",
                              onClick=[{"type": "invoke", "actionId": "dry-run"}], variant="ghost"),
                    ui.Button(label="🗑️ 只执行删除（不跑 AI）",
                              onClick=[{"type": "invoke", "actionId": "execute"}], variant="ghost"),
                    direction="row", gap="sm",
                ),
                title="sha1 比对 → 删除",
            ),
            ui.Section(
                ui.Alert(text="处理完成后要不要自动接着做 AI 识别，由下面的开关决定。"
                              "「一键跑完」默认先预演；真删必须点带 ⚠️ 的按钮或传 confirm=YES。",
                        tone="muted"),
                ui.Metric(label="本批视频命中率", value=ui.ref("query.overview.data.hitRate")),
                ui.Text(text=ui.ref("query.overview.data.verdictLine")),
                ui.Text(text=ui.ref("query.overview.data.mixedLine")),
                report_table,
                title="AI 后续动作与结果",
            ),
            ui.Section(
                ui.Text(text="定时由 MT 插件触发器负责，Cron 五段顺序为「分 时 日 月 周」，时区跟随服务端。"
                             "新建后默认暂停，需要到 MT 的插件页面手动启用。",
                        tone="muted", size="sm"),
                ui.Text(text=ui.ref("query.overview.data.autoLine")),
                ui.Stack(
                    ui.Input(label="定时 Cron（分 时 日 月 周）", bind="state.cronExpr",
                             placeholder="0 4 * * *"),
                    ui.Checkbox(label="定时任务里直接真删（不勾＝只预演）", bind="state.autoExecute"),
                    ui.Stack(
                        ui.Button(label="💾 保存定时设置",
                                  onClick=[{"type": "invoke", "actionId": "save-flow-settings",
                                            "input": {"cronExpr": ui.ref("state.cronExpr"),
                                                      "autoExecute": ui.ref("state.autoExecute")}}]),
                        ui.Button(label="↻ 载入已保存设置", variant="ghost",
                                  onClick=[{"type": "invoke", "actionId": "load-settings",
                                            "input": {"scope": "flow"}}]),
                        direction="row", gap="sm",
                    ),
                    gap="sm",
                ),
                title="⏰ 自动化定时流程",
            ),
            ui.Section(
                ui.Text(text="这些是这一条流程线要用的目录和整理方案。填好后点保存，"
                             "插件会记住（即便重启、重装插件也还在）。",
                        tone="muted", size="sm"),
                ui.Stack(
                    ui.Input(label="① 待处理源目录（云盘绝对路径）", bind="state.srcRoot",
                             placeholder="/小小玻珠/预处理/未识别"),
                    ui.ProfilePicker(label="② AI 识别后整理到哪个方案", bind="state.profileId"),
                    ui.Checkbox(label="③ AI 整理前让我确认", bind="state.aiReview"),
                    ui.Stack(
                        ui.Button(label="💾 保存目录与方案",
                                  onClick=[{"type": "invoke", "actionId": "save-flow-settings",
                                            "input": {"srcRoot": ui.ref("state.srcRoot"),
                                                      "profileId": ui.expr("if", ui.ref("state.profileId"),
                                                                           ui.ref("state.profileId"), 0),
                                                      "aiReview": ui.ref("state.aiReview")}}]),
                        ui.Button(label="↻ 载入已保存设置", variant="ghost",
                                  onClick=[{"type": "invoke", "actionId": "load-settings",
                                            "input": {"scope": "flow"}}]),
                        direction="row", gap="sm",
                    ),
                    gap="sm",
                ),
                ui.Text(text=ui.ref("query.overview.data.flowCfgLine"), tone="muted", size="sm"),
                ui.Details(
                    ui.Text(text="只在需要时改：作品层探测最大层数、单次最多删多少个作品、删完是否清空目录。",
                            tone="muted", size="sm"),
                    ui.Stack(
                        ui.Input(label="作品层探测最大层数", bind="state.maxDepth", type="number", placeholder="4"),
                        ui.Input(label="单次最多删除作品数", bind="state.maxDelete", type="number", placeholder="200"),
                        ui.Checkbox(label="删除后清理变空的父目录（不越过源目录）", bind="state.cleanEmpty"),
                        ui.Button(label="💾 保存高级参数", variant="outline",
                                  onClick=[{"type": "invoke", "actionId": "save-flow-settings",
                                            "input": {"maxDepth": ui.ref("state.maxDepth"),
                                                      "maxDelete": ui.ref("state.maxDelete"),
                                                      "cleanEmpty": ui.ref("state.cleanEmpty")}}]),
                        gap="sm",
                    ),
                    label="高级参数 · 可选",
                ),
                title="📁 目录与整理方案",
            ),
            label="🔍  比对处理",
            id="tab-flow",
        )

        # ---------------------------------------------------------------- #
        # Tab3 · sha1 数据库
        # ---------------------------------------------------------------- #
        zone_db = ui.Tab(
            ui.Section(
                ui.Alert(text="⏳ 正在后台同步库，按钮已锁定…", tone="warning", visible=running),
                ui.Stack(
                    ui.Metric(label="库内 sha1 总数", value=ui.ref("query.overview.data.total")),
                    ui.Metric(label="库文件大小（MB）", value=ui.ref("query.overview.data.dbSizeMb")),
                    direction="row", gap="md",
                ),
                ui.Text(text=ui.ref("query.overview.data.updatedAt")),
                ui.Divider(),
                lib_table,
                ui.Stack(
                    ui.Button(label="① 只读检测 MT 缓存", onClick=[{"type": "invoke", "actionId": "probe-mt"}],
                              variant="outline"),
                    ui.Button(label="② 增量同步（日常用这个）",
                              onClick=[{"type": "invoke", "actionId": "refresh-lib"}], variant="default"),
                    ui.Button(label="全量重建（含 STRM，约 3 分钟）",
                              onClick=[{"type": "invoke", "actionId": "refresh-lib", "input": {"mode": "full"}}],
                              variant="ghost"),
                    ui.Button(label="☁️ 从云盘媒体库提取 sha1",
                              onClick=[{"type": "invoke", "actionId": "refresh-lib",
                                        "input": {"includeCloud": True}}],
                              variant="outline"),
                    direction="row", gap="sm",
                ),
                ui.Text(text="日常只需要「增量同步」：MT 的 FF 缓存锁死 40 万行，满了就替换最旧的，"
                             "所以每跑一次就把新增的补进来，长期攒到自己的库里。",
                        tone="muted", size="sm"),
                title="📚 归约库",
            ),
            ui.Section(
                ui.Text(text="库的来源就这三个。目录填好后点保存即可，以后同步都用这里存的值。",
                        tone="muted", size="sm"),
                ui.Stack(
                    ui.Input(label="MT 数据库路径（只读，通常在容器里）", bind="state.mtDbPath",
                             placeholder="/app/data/mediatidy.db"),
                    ui.Input(label="STRM 扫描根目录（多个用英文逗号分隔）", bind="state.strmRoots",
                             placeholder="/strm/shareStrm,/strm/云盘影视"),
                    ui.Input(label="历史 sha1 文件（txt/csv，一次性导入，可留空）", bind="state.historyPath"),
                    ui.Input(label="云盘媒体库提取目录（MT 里的云盘绝对路径，多个用英文逗号分隔）",
                             bind="state.cloudRoots", placeholder="/小小玻珠/云盘影视"),
                    ui.Text(text="云盘提取走 MT 原生文件接口（零依赖，无 CD2 / grpcio）。"
                                 "大库分多次点击续跑：每次到 scanMinutes 上限即停，已入库不丢。",
                            tone="muted", size="sm"),
                    ui.Stack(
                        ui.Button(label="💾 保存库来源目录",
                                  onClick=[{"type": "invoke", "actionId": "save-lib-settings",
                                            "input": {"mtDbPath": ui.ref("state.mtDbPath"),
                                                      "strmRoots": ui.ref("state.strmRoots"),
                                                      "historyPath": ui.ref("state.historyPath"),
                                                      "cloudRoots": ui.ref("state.cloudRoots"),
                                                      }}]),
                        ui.Button(label="↻ 载入已保存设置", variant="ghost",
                                  onClick=[{"type": "invoke", "actionId": "load-settings",
                                            "input": {"scope": "lib"}}]),
                        direction="row", gap="sm",
                    ),
                    gap="sm",
                ),
                ui.Text(text="说明：共享型 strm 文件的下载地址里自带 sha1 参数，读文本就能拿到，"
                             "不需要任何云盘接口；MT 数据库里的 FF 缓存则是长期增量主力。",
                        tone="muted", size="sm"),
                title="🗂 库来源目录",
            ),
            label="📚  sha1 数据库",
            id="tab-db",
        )

        # 默认打开第一个 Tab（用户要求：进插件工作区就是「AI 识别」）
        state: Dict[str, Any] = {"activeTab": "tab-ai"}
        state.update(ai["state"] or {})
        state.pop("promptOpen", None)
        # Tab2 / Tab3 的输入框回显：把已保存的值放进 state，
        # 这样用户再进来看到的就是上次保存的内容（bind 只在 state 上有真语义）。
        # 只放**纯值**：这里塞 ui.ref(...) 的话宿主不会求值，输入框里会原样显示
        # {"$ref":"query.overview.data.cfg.xxx"}（真机踩过）。同一次渲染里拿不到
        # overview 的返回值，所以初值留空，由「载入已保存设置」按钮把保存值回填。
        for _k in ("srcRoot", "cronExpr", "mtDbPath", "strmRoots", "historyPath",
                   "cloudRoots"):
            state.setdefault(_k, "")
        # profileId 的字段类型是 number，宿主不接受 ""/null（会报「配置字段类型无效」）。
        # 用 0 表示「还没选方案」，插件内部把 0 当成未设置。
        state.setdefault("profileId", 0)
        for _k in ("aiReview", "autoExecute", "cleanEmpty"):
            state.setdefault(_k, False)
        for _k in ("maxDepth", "maxDelete"):
            state.setdefault(_k, "")
        # 手动识别默认先过一遍 sha1 归约库：命中 = MT 已整理过，不必再送 AI。
        # 默认开是有意的 —— 开着不花额外成本（本地查表），
        # 关着会把大量已整理文件送去 AI，白花配额。
        state.setdefault("aiSha1Gate", True)

        return ui.page(
            ui.Section(
                ui.Stack(
                    ui.Text(text="整理清道夫", size="xl"),
                    ui.Text(text="MT 整理之后的清扫环节：sha1 归约库比对 → 命中即删 → 未命中交 AI 识别",
                            tone="muted", size="sm"),
                    ui.Stack(
                        ui.Badge(text=ui.expr("concat", "库 ", ui.ref("query.overview.data.total"), " 条"),
                                 tone="primary"),
                        ui.Badge(text=ui.expr("concat", "本批命中率 ", ui.ref("query.overview.data.hitRate")),
                                 tone="success"),
                        ui.Badge(text=ui.ref("query.overview.data.updatedAt"), tone="muted"),
                        direction="row", gap="sm", align="center",
                    ),
                    gap="sm",
                ),
                ui.Divider(),
                ui.Tabs(zone_ai, zone_flow, zone_db, bind="state.activeTab"),
            ),
            state=state,
            queries=[{"id": "overview", "name": "overview"}] + list(ai["queries"] or []),
        )

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    @query("overview")
    def overview(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _cfg(ctx)
        meta = kv_get(ctx, KV_META) or {}
        lib = _lib(ctx)
        try:
            total = lib.total()
            by_src = lib.count_by_src()
            states = lib.all_states()
            size_mb = round(lib.size_bytes() / 1024 / 1024, 1)
            job = lib.latest_job()
            works_rows: List[Dict[str, Any]] = []
            stats: Dict[str, Any] = {}
            if job:
                stats = job.get("stats") or {}
                for it in lib.job_items(job["id"], limit=50):
                    works_rows.append({
                        "work": (it.get("key") or it.get("src_path") or "").rsplit("/", 1)[-1],
                        "verdict": {"delete": "🗑️ 删除", "keep": "📦 保留待识别",
                                    "no-video": "⏸️ 无视频", "unrecognized": "❔ 未识别"}
                                   .get(it["verdict"], it["verdict"]),
                        "hits": it["note"] or ("%d" % (1 if it["hit"] else 0)),
                        "dest": it["dest_path"] or "-",
                    })
        finally:
            lib.close()

        src_rows = []
        for name, bit in (("MT FF 缓存", 1), ("历史导入", 2), ("STRM 扫描", 4), ("云盘媒体库", 16)):
            key = {1: "mt_cache", 2: "history", 4: "strm", 16: "cloud"}[bit]
            st = states.get(key) or {}
            up = st.get("updated_at")
            src_rows.append({
                "name": name,
                "rows": by_src.get(name, 0),
                "updated": time.strftime("%m-%d %H:%M", time.localtime(up)) if up else "未刷新",
            })

        mixed = stats.get("mixed") or []
        return {
            "total": total,
            "dbSizeMb": size_mb,
            "sources": src_rows,
            "srcRoot": "扫描源目录：%s（命中即删，未命中原地保留交给 AI 识别）" % (cfg.get("srcRoot") or "未设置"),
            "cfg": {k: cfg.get(k) for k in (
                "srcRoot", "profileId", "aiReview", "cronExpr", "autoExecute",
                "maxDepth", "maxDelete", "cleanEmpty", "mtDbPath", "strmRoots", "historyPath",
                "cloudRoots", "cloudDepth", "scanMinutes")},
            "sets": {k: ("" if v in (None, "") else str(v)) for k, v in load_settings(ctx).items()},
            "flowCfgLine": "当前流程配置：源目录 %s ｜ 整理方案 %s ｜ 删除前确认 %s" % (
                cfg.get("srcRoot") or "未设置",
                cfg.get("profileId") or "未设置（只识别不整理）",
                "开" if cfg.get("aiReview") else "关"),
            "sha1GateLine": "归约库现有 %s 条，可作为手动识别的过滤依据%s" % (
                total, "" if total else "（库是空的，开关打开也不会过滤，请先到「sha1 数据库」建库）"),
            "autoLine": "定时：%s ｜ 定时里真删：%s" % (
                cfg.get("cronExpr") or "未设置（默认 0 4 * * *）",
                "开" if cfg.get("autoExecute") else "关（只预演）"),
            "cfgLine": "MT库 %s ｜ 单次最多删 %s 个作品 ｜ 自动执行 %s" % (
                cfg.get("mtDbPath"), cfg.get("maxDelete"),
                "开" if cfg.get("autoExecute") else "关"),
            "hitRate": ("%s%%" % stats.get("hit_rate")) if stats else "-",
            "verdictLine": ("本次：删除 %s 个作品 / 保留待识别 %s 个 / 无视频 %s / 未识别 %s ｜ 视频命中 %s，未命中 %s，缺sha1 %s"
                            % (stats.get("delete_works", 0), stats.get("keep_works", 0),
                               stats.get("no_video_works", 0), stats.get("unrecognized", 0),
                               stats.get("hit_videos", 0), stats.get("miss_videos", 0),
                               stats.get("no_sha1_videos", 0))) if stats else "尚未运行",
            "mixedLine": ("⚠️ 混合作品（部分命中，整包保留待 AI 识别）%d 个：%s"
                          % (len(mixed), "；".join(mixed[:5]))) if mixed else "",
            "works": works_rows,
            "updatedAt": "%s｜%s" % (meta.get("updatedAt", "-"), meta.get("state", "idle")),
        }

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    @action("save-flow-settings")
    def save_flow_settings(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """保存「② 比对处理」里的目录、整理方案、定时与高级参数。

        页面上的 Input/ProfilePicker 用的是 bind="state.*"，state 只在当前页面会话内有效；
        要真正记住必须由这个 action 写进插件存储（storage KV 键 settings）。
        """
        return apply_settings(self, input or {}, ctx, keys=(
            "srcRoot", "profileId", "aiReview", "cronExpr", "autoExecute",
            "maxDepth", "maxDelete", "cleanEmpty"))

    @action("load-settings")
    def load_settings_action(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """把已保存的设置回填到页面输入框。

        为什么需要它：页面渲染那一刻拿不到 query 的返回值（不是 Promise），
        所以 state 里只能放**纯值**初值；若塞 ui.ref(...)，输入框里会原样显示
        {"$ref":"query.overview.data.cfg.cronExpr"}（真机踩过）。
        于是改成：初值留空 + 一个按钮主动把 storage 里的值读出来回填。
        `state` 在宿主那边是可写绑定，用它回填输入框。
        """
        saved = load_settings(ctx)
        scope = str((input or {}).get("scope") or "").strip()
        flow_keys = ("srcRoot", "profileId", "aiReview", "cronExpr",
                     "autoExecute", "maxDepth", "maxDelete", "cleanEmpty")
        lib_keys = ("mtDbPath", "strmRoots", "historyPath", "cloudRoots")
        keys = lib_keys if scope == "lib" else flow_keys

        cfg = _cfg(ctx)
        state: Dict[str, Any] = {}
        for k in keys:
            val = saved.get(k, cfg.get(k))
            state[k] = "" if val is None else val

        filled = [k for k in keys if state.get(k) not in ("", None)]
        return {"waiting": False, "ok": True,
                "message": "已载入 %d 项设置%s" % (
                    len(filled), "" if filled else "（都还是空的，先填好再保存）"),
                "settings": state}
    @action("save-lib-settings")
    def save_lib_settings(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """保存「③ sha1 数据库」的库来源目录（MT 库路径 / STRM 根 / 历史文件）。"""
        return apply_settings(self, input or {}, ctx, keys=(
            "mtDbPath", "strmRoots", "historyPath", "cloudRoots",
            "cloudDepth", "scanMinutes"))

    @action("sha1-match-recognize")
    def sha1_match_recognize(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """手动 sha1 匹配：把本批快照的全部文件拿来比对，再让 AI 核对一遍身份。

        用途是人工复核 —— 「sha1 命中了」只说明库里见过这个哈希，可能想再确认它属于哪部作品。
        所以这里取快照里的**全部**文件（不是只取未命中的 keep 作品），并且故意不带整理方案：
        只识别、不提交整理，避免在复核阶段误动媒体库。
        """
        cfg = _cfg(ctx)
        src = str(cfg.get("srcRoot") or "").strip()
        if not src:
            return {"waiting": False, "ok": False,
                    "message": "请先到「② 比对处理」里选好待处理源目录并保存"}
        return self.recognize({"profileId": "", "review": False,
                               "instructions": "", "filesFromBatch": True,
                               "batchAll": True}, ctx)

    @action("cloud-probe")
    def cloud_probe(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """对单个路径做裸调 files.list，把原始返回和错误码原样打出来。

        用来区分「MT 侧列不出来」和「插件拼路径/翻页出错」：
        这里不走 mtfs 的任何封装，直接调 ctx.mt.files.list。
        """
        path = str((input or {}).get("path") or "").strip()
        if not path:
            return {"waiting": False, "ok": False, "message": "请传 path"}
        out: List[Dict[str, Any]] = []

        # 1) 裸调 files.stat
        try:
            root_id, rel = mtfs.resolve_root(ctx, path)
            out.append("resolve_root → rootId=%s rel=%r" % (root_id, rel))
        except Exception as exc:
            return {"waiting": False, "ok": False,
                    "message": "resolve_root 失败：%s" % str(exc)[:300]}

        try:
            st = ctx.mt.files.stat(rootId=root_id, path=rel)
            out.append("files.stat → %s" % json.dumps(st, ensure_ascii=False)[:400])
        except Exception as exc:
            out.append("files.stat 失败 → %s: %s" % (type(exc).__name__, str(exc)[:300]))

        # 2) 裸调 files.list 第 1 页
        try:
            res = ctx.mt.files.list(rootId=root_id, path=rel, page=1)
            items = res.get("items") if isinstance(res, dict) else res
            out.append("files.list → %d 项 hasMore=%s" % (
                len(items or []), (res or {}).get("hasMore") if isinstance(res, dict) else "?"))
            for it in (items or [])[:5]:
                out.append("   · %s%s" % (it.get("name"), "/" if it.get("isDir") else ""))
        except Exception as exc:
            out.append("files.list 失败 → %s: %s" % (type(exc).__name__, str(exc)[:300]))

        # 3) 同一路径的父目录，做对照
        parent = path.rsplit("/", 1)[0]
        try:
            pr, prel = mtfs.resolve_root(ctx, parent)
            res = ctx.mt.files.list(rootId=pr, path=prel, page=1)
            items = res.get("items") if isinstance(res, dict) else res
            out.append("对照：父目录 %s → %d 项" % (parent, len(items or [])))
        except Exception as exc:
            out.append("对照：父目录 %s 失败 → %s" % (parent, str(exc)[:200]))

        # 4) 配额探测：连续调 files.list 同一个目录，看第几次开始报错
        n = _as_int((input or {}).get("quotaTest"), 0)
        if n > 0:
            hit = 0
            err = ""
            for i in range(n):
                try:
                    ctx.mt.files.list(rootId=root_id, path=rel, page=1)
                    hit = i + 1
                except Exception as exc:
                    err = "%s: %s" % (i + 1, str(exc)[:120])
                    break
            out.append("配额探测：连续成功 %d 次%s" % (hit, ("；第 %s 次失败" % err) if err else "；n=%d 内未失败" % n))

        return {"waiting": False, "ok": True, "message": "\n".join(out)}

    @action("cloud-diag")
    def cloud_diag(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """诊断云盘媒体库提取：逐层展开，把每个失败目录和它的真实错误列出来。

        背景：`mtfs.walk` 遇到列目录失败会静默跳过，导致「某一层整体打不开」
        在结果里只体现成「目录数比预期少」，看不出原因。这个动作专门用来定位。
        """
        cfg = _cfg(ctx)
        roots = _as_list(input.get("roots")) or _as_list(cfg.get("cloudRoots"))
        if not roots:
            return {"waiting": False, "ok": False,
                    "message": "请先在第 3 个 Tab 填「云盘媒体库提取目录」，或给本动作传 roots"}
        depth = _as_int(input.get("cloudDepth"), _as_int(cfg.get("cloudDepth"), 12))
        mtfs.walk_errors(ctx, reset=True)

        lines: List[str] = []
        detail: List[Dict[str, Any]] = []
        for root in roots:
            root = (root or "").strip()
            if not root:
                continue
            # 先单独验证根能不能列
            try:
                top = mtfs.list_dir(ctx, root)
                lines.append("%s：首层 %d 项" % (root, len(top)))
            except Exception as exc:
                lines.append("%s：首层就失败 —— %s" % (root, str(exc)[:200]))
                detail.append({"path": root, "depth": 0, "error": str(exc)[:400]})
                continue

            dirs = files = 0
            per_level: Dict[int, int] = {}
            deadline = time.time() + 600
            stack = [(root, 0)]
            while stack:
                cur, d = stack.pop()
                if time.time() > deadline:
                    lines.append("（诊断超时，已停在 %s）" % cur)
                    break
                try:
                    kids = mtfs.list_dir(ctx, cur)
                except Exception as exc:
                    detail.append({"path": cur, "depth": d, "error": str(exc)[:400]})
                    continue
                for it in kids:
                    if it.get("isDir"):
                        dirs += 1
                        per_level[d + 1] = per_level.get(d + 1, 0) + 1
                        if d + 1 < depth:
                            stack.append((str(it.get("path") or ""), d + 1))
                    else:
                        files += 1

            lines.append("%s：目录 %d / 文件 %d；每层目录数 %s"
                         % (root, dirs, files,
                            " ".join("L%d=%d" % (k, per_level[k]) for k in sorted(per_level))))

        errs = mtfs.walk_errors(ctx, reset=True)
        for path, err in errs[:20]:
            detail.append({"path": path, "depth": -1, "error": err})

        ok = not detail
        lines.append("失败目录 %d 个%s" % (len(detail), "" if detail else "（无）"))
        return {"waiting": False, "ok": ok,
                "message": "\n".join(lines),
                "detail": detail[:60]}

    @action("probe-mt")
    def probe_mt(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """环境自检：MT 库能否读、文件根是否授权、源目录是否可达。"""
        cfg = _cfg(ctx)
        lines: List[str] = []

        info = sources.probe_mt_db(cfg.get("mtDbPath") or sources.DEFAULT_MT_DB)
        if info["ok"]:
            lines.append("✅ MT 库可读：%s，FF 缓存 %d 行" % (info["path"], info["rows"]))
        else:
            lines.append("❌ 读不到 MT 库：%s" % info["error"])

        # TMDB 通道自检：tmdb.detail 报「操作未完成」时，从这里区分是
        # 「插件通道坏了」还是「MT 后端的对应业务状态不对」。
        try:
            detail = ctx.mt.tmdb.detail(id=88273, media_type="movie")
            lines.append("✅ TMDB 通道可用：电影 88273 → %s（%s）"
                         % (detail.get("title") or "?", detail.get("year") or "?"))
        except Exception as exc:
            lines.append("❌ TMDB 通道失败：%s" % exc)
        try:
            profiles = ctx.mt.organize.profiles()
            names = "、".join(str(x.get("name")) for x in profiles if isinstance(x, dict)) or "（空，需在授权里勾选方案）"
            lines.append("✅ 整理方案 %d 个：%s" % (len(profiles or []), names))
        except Exception as exc:
            lines.append("❌ 读取整理方案失败：%s" % exc)

        try:
            roots = mtfs.list_roots(ctx)
            names = "、".join(str(r.get("name")) for r in roots) or "（无）"
            lines.append("✅ 文件根 %d 个：%s" % (len(roots), names))
        except mtfs.MtFsError as exc:
            roots = []
            lines.append("❌ 读取文件根失败：%s" % exc)

        for label, path in (("源目录", cfg.get("srcRoot")),):
            path = str(path or "").strip()
            if not path:
                lines.append("⚠️ %s未配置" % label)
                continue
            e = mtfs.stat(ctx, path)
            if e is None:
                lines.append("⚠️ %s不可达：%s（需在插件设置里授权该目录所在根）" % (label, path))
            elif not e.is_dir:
                lines.append("❌ %s不是目录：%s" % (label, path))
            else:
                lines.append("✅ %s可达：%s" % (label, path))

        # 源目录内容抽样，便于排查「扫到 0 个作品」
        src = str(cfg.get("srcRoot") or "").strip()
        sample: List[Dict[str, Any]] = []
        if src:
            try:
                for it in mtfs.list_dir(ctx, src)[:20]:
                    sample.append({"name": it.get("name"), "isDir": bool(it.get("isDir")),
                                   "sha1": (it.get("sha1") or "")[:8]})
                lines.append("源目录首层 %d 项" % len(sample))
            except mtfs.MtFsError as exc:
                lines.append("⚠️ 列源目录失败：%s" % exc)

        ok = info["ok"] and bool(roots)
        msg = "\n".join(lines)
        _log(ctx, "环境自检：" + msg.replace("\n", " ｜ "))
        return {"waiting": False, "ok": ok, "message": msg,
                "info": {"mt": info, "roots": len(roots), "sample": sample}}

    @action("refresh-lib")
    def refresh_lib(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """刷新 sha1 归约库。

        日常（默认）只做 MT FF 缓存增量同步：MT 每整理完一个作品就往
        organize_file_probe_cache 写一行，用 rowid 游标取新增行即可，
        通常几十~几百行、毫秒级完成。

        STRM 扫描与历史导入是一次性建库动作，
        默认跳过；要补存量时显式传 includeStrm / includeHistory，
        或传 mode="full" 全量重来一遍。
        """
        cfg = _cfg(ctx)
        inp = input or {}
        mode = str(inp.get("mode") or "incremental").strip().lower()
        full = mode == "full"
        # 一次性动作：默认不做；full 模式强制做
        want_hist = full or _as_bool(inp.get("includeHistory"), False)
        want_strm = full or _as_bool(inp.get("includeStrm"), False)
        want_cloud = full or _as_bool(inp.get("includeCloud"), False)

        kv_put(ctx, KV_META, {"state": "collecting", "updatedAt": "%s｜刷新库中" % _now()})
        lib = _lib(ctx)
        msgs: List[str] = []
        t0 = time.time()
        try:
            # ---- 1) MT FF 缓存：长期增量主力 ----
            added, msg = sources.collect_mt_cache(
                lib, cfg.get("mtDbPath"), incremental=not full)
            msgs.append(msg)
            _log(ctx, msg)

            # ---- 2) 历史导入：一次性 ----
            hist = (cfg.get("historyPath") or "").strip()
            if hist and want_hist:
                _, msg = sources.collect_history(lib, hist)
                msgs.append(msg)
                _log(ctx, msg)
            elif hist:
                msgs.append("历史导入：已完成过，本次跳过（勾选「同时补历史库」可重跑）")

            # ---- 3) STRM 扫描：一次性 ----
            roots = _as_list(cfg.get("strmRoots"))
            strm_done = lib.get_meta(sources.META_STRM_DONE, "") == "1"
            if not roots:
                if want_strm:
                    msgs.append("STRM 扫描：未配置 strmRoots，跳过")
            elif want_strm or not strm_done:
                # STRM 扫描只负责「分享型 strm」（URL 里自带 sha1= 参数），
                # 纯读本地文本，不碰任何云盘接口。
                # 非 http 型的 CD2 路径 strm 不再从本地反查 —— 网盘那部分
                # 统一走下面的 CD2 gRPC 整树遍历（更快、能做断点续跑）。
                _, msg = sources.collect_strm(
                    lib, roots,
                    deadline=time.time() + _as_int(cfg.get("scanMinutes"), 20) * 60)
                msgs.append(msg)
                _log(ctx, msg)
            else:
                msgs.append("STRM 扫描：已完成过（一次性动作），本次跳过"
                            "（勾选「同时补 STRM」可重跑）")

            # ---- 4) 云盘媒体库提取：直接遍历云盘已整理目录取 sha1 ----
            # 不受 MT FF 缓存 40 万行滚动淘汰影响，补的是被淘汰掉的存量。
            cloud_roots = _as_list(cfg.get("cloudRoots"))
            if not cloud_roots:
                if want_cloud:
                    msgs.append("云盘媒体库：未设置要提取的云盘目录，跳过")
            elif want_cloud or not all(sources.cloud_done(lib, r) for r in cloud_roots):
                # 云盘提取是**一次性扩库**动作（用户口径：strm 只在开始扩库时用，
                # 日常靠 MT FF 缓存增量）。所以：
                #   · 显式要求（勾选「同时补云盘」）→ 一定重跑
                #   · 否则只有还存在「没完整提取过」的目录时才跑
                # 跑完会按目录写 cloud_scan_done 标记，下次就不再自动跑了。
                # 深度必须够深：云盘影视是 分类/子类/作品/Season/文件，作品层已在第 4 层，
                # 复用 maxDepth（作品层探测用，默认 4）会把 Season 里的文件全漏掉。
                _, msg = sources.collect_cloud(
                    lib, ctx,
                    cloud_roots,
                    max_depth=_as_int(cfg.get("cloudDepth"), 12),
                    deadline=time.time() + _as_int(cfg.get("scanMinutes"), 20) * 60)
                msgs.append(msg)
                _log(ctx, msg)
            else:
                msgs.append("云盘媒体库：已完成过一次扩库，本次跳过"
                            "（勾选「同时补云盘」可重跑）")

            total = lib.total()
            by_src = lib.count_by_src()
            states = lib.all_states()
        finally:
            lib.close()
            kv_put(ctx, KV_META, {"state": "idle", "updatedAt": "%s｜刷新完成" % _now()})

        cursor = (states.get("mt_cache") or {}).get("last_value") or "0"
        mt_rows = (states.get("mt_cache") or {}).get("rows") or 0
        summary = ("归约库现有 %d 条（MT %d / 历史 %d / STRM %d / 云盘 %d）；"
                   "MT 游标 rowid %s（表内 %d 行），耗时 %.1fs" % (
                       total, by_src.get("MT FF 缓存", 0), by_src.get("历史导入", 0),
                       by_src.get("STRM 扫描", 0), by_src.get("云盘媒体库", 0),
                       cursor, mt_rows, time.time() - t0))
        return {"waiting": False, "ok": True, "message": summary, "detail": msgs,
                "mode": "full" if full else "incremental"}

    # ------------------------------------------------------------------ #
    def _compare(self, ctx: Context, cfg: Dict[str, Any], dry_run: bool,
                 snapshot: bool = False) -> Dict[str, Any]:
        src_root = (cfg.get("srcRoot") or "").strip()
        if not src_root:
            return {"waiting": False, "ok": False, "message": "未配置源目录"}

        kv_put(ctx, KV_META, {"state": "comparing", "updatedAt": "%s｜正在比对" % _now()})
        lib = _lib(ctx)
        try:
            # 1) 遍历（需要时同时冻结本批快照）
            deadline = time.time() + 1200
            builder = WorkBuilder(src_root, max_depth=_as_int(cfg.get("maxDepth"), 4))
            n_files = 0
            frozen: List[Dict[str, Any]] = []
            try:
                for entry in mtfs.walk(ctx, src_root, max_depth=12, deadline=deadline):
                    builder.feed(entry)
                    n_files += 1
                    if snapshot and not entry.is_dir:
                        frozen.append({"path": entry.path, "name": entry.name,
                                       "ref": entry.ref or "", "sha1": entry.sha1 or "",
                                       "size": entry.size or 0,
                                       "rootId": entry.root_id or ""})
                    if n_files % 200 == 0:
                        try:
                            ctx.mt.runtime.progress(completed=n_files, message="已扫描 %d 个条目" % n_files)
                        except Exception:
                            pass
            except mtfs.MtFsError as exc:
                kv_put(ctx, KV_META, {"state": "idle", "updatedAt": "%s｜扫描失败" % _now()})
                return {"waiting": False, "ok": False, "message": "扫描云盘失败：%s" % exc}

            # 2) 归并作品 + 判定
            works = builder.works()
            works = judge_works(works, lib)
            stats = summarize(works)
            stats["mixed"] = [w["path"] for w in mixed_works(works)]
            stats["scanned"] = n_files

            # 3) 落库报告
            job_id = lib.new_job("dry-run" if dry_run else "execute", src_root, "")
            items = []
            for w in works:
                note = "%d/%d" % (w.get("hit_count", 0), w.get("total", 0))
                items.append((
                    w["path"], (w["videos"][0].sha1 if w["videos"] else ""),
                    w["verdict"], 1 if w["verdict"] == "delete" else 0,
                    "", note,
                ))
            lib.add_items(job_id, items)

            # 3.5) 冻结本批快照：后续 AI 识别只认这份清单，
            #      扫描结束后新进云盘的文件不会插队进本批。
            batch_kept: List[str] = []
            if snapshot:
                for w in works:
                    if w.get("verdict") == "keep":
                        batch_kept.append(w["path"].rstrip("/"))
                kv_put(ctx, KV_BATCH, {
                    "schema": BATCH_SCHEMA,
                    "createdAt": time.time(),
                    "srcRoot": src_root,
                    "scanned": n_files,
                    "files": frozen,
                    "keptWorks": batch_kept,
                    "stats": {k: stats.get(k) for k in
                              ("works", "delete_works", "keep_works", "hit_videos",
                               "miss_videos", "hit_rate")},
                })

            # 4) 执行（dry_run 时只出计划）
            res = execute_plan(
                ctx, works, src_root,
                max_delete=_as_int(cfg.get("maxDelete"), 200),
                dry_run=dry_run,
                clean_empty=_as_bool(cfg.get("cleanEmpty"), True),
            )
            stats["deleted"] = len(res["deleted"])
            stats["kept"] = len(res["kept"])
            stats["errors"] = len(res["errors"])
            stats["cleaned"] = len(res["cleaned_dirs"])
            lib.update_job(job_id, status="done", stats=stats)

            line = "%s：扫描 %d 条 → %d 个作品；删除 %d，保留待识别 %d，无视频 %d，未识别 %d，失败 %d" % (
                "预演完成" if dry_run else "执行完成", n_files, len(works),
                len(res["deleted"]), len(res["kept"]),
                stats.get("no_video_works", 0), stats.get("unrecognized", 0), len(res["errors"]))
            kv_put(ctx, KV_META, {"state": "idle", "updatedAt": "%s｜%s" % (_now(), line)})
            return {"waiting": False, "ok": True, "message": line,
                    "stats": stats, "errors": res["errors"][:20]}
        except mtfs.MtFsError as exc:
            kv_put(ctx, KV_META, {"state": "idle", "updatedAt": "%s｜失败" % _now()})
            return {"waiting": False, "ok": False, "message": "云盘操作失败：%s" % exc}
        finally:
            lib.close()

    @action("dry-run")
    def dry_run(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """只预演：不删不搬，但照样冻结本批快照。

        冻结的理由是页面上那句承诺 ——「预演也会冻结快照，不动任何文件」。
        AI 识别只认快照，所以先预演再识别是一条正规用法：
        用户可以先看比对结果，再决定要不要把那批未命中的文件交出去识别。
        """
        return self._compare(ctx, _cfg(ctx), dry_run=True, snapshot=True)

    @action("execute")
    def execute(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        cfg = _cfg(ctx)
        confirm = str(input.get("confirm") or "").strip().upper()
        if confirm not in ("YES", "Y", "确认", "1"):
            return {"waiting": False, "ok": False,
                    "message": "执行需要二次确认：请先运行预演，确认结果后在调用参数里传 confirm=YES"}
        return self._compare(ctx, cfg, dry_run=False)

    @action("cleanup-all")
    def cleanup_all(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """一键清理：扫一遍 → 冻结本批快照 → sha1 比对删除 → 剩下的交 AI 识别。

        整条流程只认扫描那一刻的快照，扫描之后新进云盘的文件不会插队。
        execute=false 时只预演不删（默认），真删需传 confirm=YES。
        """
        cfg = _cfg(ctx)
        _log(ctx, "cleanup-all 配置：srcRoot=%r profileId=%r aiReview=%r"
             % (cfg.get("srcRoot"), cfg.get("profileId"), cfg.get("aiReview")))
        profile_id = resolve_profile_id(ctx, cfg.get("profileId"))
        if not profile_id:
            return {"waiting": False, "ok": False,
                    "message": "请在插件「配置」里填写 AI 识别后要交的 MT 整理方案（方案名或方案 ID，"
                               "如「阿里秒传」或 954），否则比对删除之后无法交给 AI 整理。"}
        confirm = str(input.get("confirm") or "").strip().upper()
        execute = confirm in ("YES", "Y", "确认", "1")

        # 第 1、2 步：扫描 + 冻结快照 + sha1 比对删除（同一次遍历，快照即本批边界）
        res = self._compare(ctx, cfg, dry_run=not execute, snapshot=True)
        if not res.get("ok"):
            return res
        kept = int((res.get("stats") or {}).get("kept") or 0)
        if kept == 0:
            return {"waiting": False, "ok": True,
                    "message": res.get("message", "") + "｜本批全部命中已删除，无需 AI 识别",
                    "stats": res.get("stats")}

        # 第 3 步：把剩余的交 AI 识别（只读快照，不重新扫描云盘）
        return self.recognize({"profileId": profile_id,
                               "review": _as_bool(cfg.get("aiReview"), False),
                               "instructions": "",
                               "filesFromBatch": True}, ctx)

    @action("run-job")
    def run_job(self, input: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """定时入口：只在配置了自动执行时才真删，否则只跑预演。"""
        cfg = _cfg(ctx)
        # 定时触发时只看配置，避免触发器固化一次性的执行开关
        auto = _as_bool(cfg.get("autoExecute"), False)
        return self._compare(ctx, cfg, dry_run=not auto)
