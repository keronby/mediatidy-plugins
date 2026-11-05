# -*- coding: utf-8 -*-
"""本地重装 + 启用 + 自检（media.sweep 整理清道夫）。

用法：
    python3 tools/reinstall.py            # 重装并启用
    python3 tools/reinstall.py --no-enable
    python3 tools/reinstall.py --keep     # 不卸载，直接覆盖上传安装（更快）

要点（踩坑得来，别改）：
  1. install 的 grants 只传权限布尔 map，其它形状会被静默忽略
  2. 启用时 PUT 必须同时传 enabled + config + grants，否则配置/授权被清空
  3. grants.roots 是本插件的授权目录列表（根名或绝对路径）
     —— 本版走 MT 原生 files.* API，必须真的授权云盘根，否则 resolve_root 找不到根
"""
import json
import sys
import time

import requests

MT = "http://127.0.0.1:2019/api"
PID = "media.sweep"
ZIP = "dist/media.sweep-1.1.0.zip"

# 只留清单里仍然声明的键。目录 / 方案 / 定时这些已经挪到插件页面里选和保存
# （用户口径：授权和设置界面不再放这些目录设置），所以这里不再下发。
CFG = {
    "prompt": "",
}

# 授权根：必须是完整的 root 对象（宿主校验 id/read/write/delete/storageType）
# 云盘根名为「小小玻珠」
ROOTS = [
    {
        "read": True, "write": True, "delete": True,
        "id": "root-242c8625-6284-468a-bce5-0ce210ea1834",
        "name": "小小玻珠", "path": "/小小玻珠", "storageType": "cloud",
    },
    {
        "read": True, "write": False, "delete": False,
        "id": "root-b4626435-631a-4982-a6ba-8083d745a821",
        "name": "strm", "path": "/strm", "storageType": "local",
    },
]


def login():
    s = requests.Session()
    s.headers["Authorization"] = "Bearer " + s.post(
        MT + "/auth/login",
        json={"username": "admin", "password": "fuck0000"},
        timeout=30,
    ).json()["data"]["token"]
    return s


def install(s, drop=True):
    P = MT + "/plugins/" + PID
    if drop:
        s.delete(P, timeout=60)
        time.sleep(3)

    with open(ZIP, "rb") as f:
        r = s.post(
            MT + "/plugins/inspect/upload",
            files={"file": ("p.zip", f, "application/zip")},
            timeout=300,
        )
    if r.status_code != 200:
        print("inspect FAILED:", r.text[:800])
        return None
    data = r.json()["data"]
    manifest = data["release"]["manifest"]
    caps = manifest.get("permissions") or []
    print("inspect ok, ticket:", data["ticket"][:8])
    print("perms:", caps)

    r = s.post(
        MT + "/plugins/install",
        json={"ticket": data["ticket"], "grants": {c: True for c in caps}},
        timeout=300,
    )
    print("install:", r.status_code, r.text[:400])
    if r.status_code != 200:
        return None
    return caps


def wait_ready(s, timeout_s=180):
    P = MT + "/plugins/" + PID
    print("waiting for install ...", end="", flush=True)
    for _ in range(timeout_s // 3):
        time.sleep(3)
        g = s.get(P, timeout=60)
        if g.status_code == 200:
            st = g.json()["data"].get("status")
            print(" up, status=%s" % st)
            return True
        print(".", end="", flush=True)
    print("\nTIMEOUT")
    return False


def enable(s, caps):
    P = MT + "/plugins/" + PID
    body = {
        "enabled": True,
        "config": CFG,
        "grants": {
            "eventProfileIds": [],
            "eventStrmProfileIds": [],
            "autoProfileIds": [],
            "profileRevisions": {},
            "strmRevisions": {},
            "capabilities": caps,
            "roots": ROOTS,
            "profileIds": [954],   # 阿里秒传：AI 识别后整理到这个方案
            "strmProfileIds": [],
            "channelIds": [],
            "http": [],
        },
    }
    r = s.put(P, json=body, timeout=120)
    print("enable:", r.status_code, r.text[:600])
    return r.status_code == 200


def check(s):
    P = MT + "/plugins/" + PID
    g = s.get(P, timeout=60)
    if g.status_code == 200:
        d = g.json()["data"]
        print("status:", d.get("status"), "enabled:", d.get("enabled"))
        print("grants.roots:", (d.get("grants") or {}).get("roots"))

    u = s.get(P + "/ui", timeout=180)
    print("ui:", u.status_code, ("len=%d" % len(u.text)) if u.status_code == 200 else u.text[:800])

    q = s.post(P + "/queries/overview", json={}, timeout=120)
    if q.status_code == 200:
        print("overview:", json.dumps(q.json()["data"], ensure_ascii=False)[:600])
    else:
        print("overview:", q.status_code, q.text[:400])


def main(argv):
    keep = "--keep" in argv
    do_enable = "--no-enable" not in argv

    s = login()
    caps = install(s, drop=not keep)
    if caps is None:
        return 1
    if not wait_ready(s):
        return 1
    if do_enable and not enable(s, caps):
        return 1
    check(s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
