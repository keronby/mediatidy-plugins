# -*- coding: utf-8 -*-
"""设置项的读写：把界面上填的目录 / 方案 / 开关真正存下来。

为什么需要这一层：
    UI v2 里 `Input(bind="config.xxx")`、`FilePicker`、`ProfilePicker` 的 `bind`
    只是**样式绑定**，宿主不会因为用户在框里打字就落库；`bind="state.xxx"`
    才有真实语义，但它只活在当前页面会话里。
    所以「目录和整理方案的选取和保存」必须靠一个显式 action 把值写进插件存储，
    页面再通过 query 把存下来的值回显到 `state`。

存储位置（双写）：
    1. 插件 storage KV，键 "settings" —— 页面会话内的主通道
    2. 容器里一个**固定路径**的 JSON 文件 —— 跨「卸载→重装」存活
       MT 每次卸载重装都会换 instanceId，storage KV 与新实例绑定，
       设置就会跟着丢（真机踩过）。目录 / 方案这些是用户的劳动成果，
       不该因为升级插件重新填一遍，所以额外落一份文件。

优先级（由调用方 _cfg 决定）：清单配置 < 存储设置。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional

KV_SETTINGS = "settings"

# 与归约库放在同一个固定目录下，一次迁移、长期有效。
SETTINGS_PATHS = (
    "/app/data/plugins/data/.media.sweep/settings.json",
    "/app/data/media.sweep/settings.json",
)

# 需要整型/布尔归一化的键
_INT_KEYS = {"maxDelete", "maxDepth", "cloudDepth", "scanMinutes"}
_BOOL_KEYS = {"cleanEmpty", "autoExecute", "aiReview", "review"}
_INT_DEFAULTS = {"maxDelete": 200, "maxDepth": 4, "cloudDepth": 12, "scanMinutes": 20}


def _settings_file() -> Optional[str]:
    """挑一个可写的位置放设置文件；都不可写时返回 None（退回纯 KV）。"""
    for candidate in SETTINGS_PATHS:
        folder = os.path.dirname(candidate)
        try:
            os.makedirs(folder, exist_ok=True)
            return candidate
        except Exception:
            continue
    return None


def _read_file() -> Dict[str, Any]:
    path = _settings_file()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_file(data: Dict[str, Any]) -> None:
    path = _settings_file()
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def load_settings(ctx) -> Dict[str, Any]:
    """读已经保存过的设置。

    合并顺序：文件（跨重装的那份）→ 存储 KV（当前实例）。
    这样老版本只存在 KV 里的设置不会丢，新写的两边都有。

    注意 storage.get 返回的是 {key, found, value, ...} 包装，
    必须看 found 且取 value —— 直接把它当设置内容会得到一堆元数据键。
    """
    out: Dict[str, Any] = {}
    out.update(_read_file())
    try:
        res = ctx.mt.storage.get(key=KV_SETTINGS)
    except Exception:
        return out
    if not isinstance(res, dict) or not res.get("found"):
        return out
    val = res.get("value")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return out
    if isinstance(val, dict):
        out.update(val)
    return out


def save_settings(ctx, patch: Dict[str, Any]) -> Dict[str, Any]:
    """把 patch 合并进已保存的设置并写回，返回合并后的全量设置。"""
    merged = load_settings(ctx)
    merged.update({k: v for k, v in patch.items() if v is not None})
    # 先落文件（跨重装），再写 KV（当前会话）；任一成功都算保存成功。
    _write_file(merged)
    try:
        ctx.mt.storage.set(key=KV_SETTINGS, value=merged)
    except Exception as exc:
        try:
            ctx.mt.log(level="error", message="写入插件存储失败（已落文件）：%s" % exc)
        except Exception:
            pass
    return merged


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "是", "开")


def _as_int(value: Any, default: int) -> int:
    try:
        n = int(str(value).strip())
        return n if n > 0 else default
    except Exception:
        return default


def apply_settings(plugin, payload: Dict[str, Any], ctx, keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """从一个 action 的 input 里挑出设置键，归一化后保存，并回显给界面。

    payload 里出现的键才会被改；没出现的保持原值（不会把别的 Tab 的设置清空）。
    """
    wanted = set(keys) if keys else None
    patch: Dict[str, Any] = {}

    for key, raw in payload.items():
        if wanted is not None and key not in wanted:
            continue
        if raw is None:
            continue
        if key in _INT_KEYS:
            patch[key] = _as_int(raw, _INT_DEFAULTS.get(key, 0))
        elif key in _BOOL_KEYS:
            patch[key] = _as_bool(raw, False)
        elif key in ("profileId", "prompt"):
            text = str(raw).strip()
            # 提示词留空 = 用默认；方案留空 = 只识别不整理。两者都不写空值覆盖。
            # profileId 的页面控件是 number 类型（宿主不接受 ""/null），
            # 所以「还没选方案」会以 0 传进来 —— 同样当作未设置，不覆盖已存的方案。
            if key == "profileId" and text in ("0", "None", "null"):
                continue
            if text:
                patch[key] = text
        else:
            patch[key] = str(raw).strip()

    merged = save_settings(ctx, patch)
    saved = sorted(patch.keys())
    try:
        ctx.mt.log(level="info", message="已保存设置：%s" % ("、".join(saved) if saved else "（无变化）"))
    except Exception:
        pass
    return {"waiting": False, "ok": True,
            "message": "已保存：%s" % ("、".join(saved) if saved else "没有需要保存的项"),
            "settings": merged}
