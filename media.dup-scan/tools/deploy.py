#!/usr/bin/env python3
"""把 dist/media.dup-scan-1.0.0.zip 装进 MediaTidy 并启用。

分三步（顺序不能乱，都是踩坑换来的）：
  1. inspect/upload —— 上传拿到 ticket，否则 install 404
  2. install —— grants 只传权限布尔（多传 capabilities/roots 会被拒）
  3. PUT /api/plugins/<id> —— 只传 enabled+config+grants。
     **不能只传 enabled**，否则 grants 被清空，插件拿不到 rules.read 等能力。
"""
import json, os, sys, time, requests

MT = os.environ.get("MT_BASE", "http://127.0.0.1:2019/api")
USER = os.environ.get("MT_USER", "admin")
PWD = os.environ.get("MT_PASSWORD", "")
PLUGIN = "media.dup-scan"
ROOT = "/strm"          # 容器内路径！宿主 /vol1/1000/strm 在 mediatidy 里不存在

def main(zip_path):
    s = requests.Session()
    tok = s.post(MT + "/auth/login", json={"username": USER, "password": PWD}, timeout=30).json()["data"]["token"]
    s.headers["Authorization"] = "Bearer " + tok
    P = "%s/plugins/%s" % (MT, PLUGIN)

    s.delete(P, timeout=60)
    time.sleep(2)

    with open(zip_path, "rb") as f:
        d = s.post(MT + "/plugins/inspect/upload", files={"file": ("p.zip", f, "application/zip")}, timeout=180).json()["data"]
    perm = d["release"]["manifest"]["permissions"]
    print("permissions:", perm)

    s.post(MT + "/plugins/install", json={"ticket": d["ticket"], "grants": {p: True for p in perm}, "config": {}}, timeout=180)
    for _ in range(20):
        time.sleep(3)
        if s.get(P, timeout=60).status_code == 200:
            break
    else:
        sys.exit("安装超时：插件未出现在列表里")

    # 自动拉取系统当前所有的整理方案 ID 授权给插件
    prof_resp = s.get(MT + "/organize/profiles", timeout=10)
    pids = []
    if prof_resp.status_code == 200:
        p_items = prof_resp.json().get("data") or []
        pids = [p["id"] for p in p_items if isinstance(p, dict) and "id" in p]

    grants = {
        "autoProfileIds": [], "profileRevisions": {}, "strmRevisions": {},
        "capabilities": perm,
        "roots": [{"read": True, "write": True, "delete": True,
                   "id": "root-strm", "name": "strm", "path": ROOT, "storageType": "local"}],
        "profileIds": pids, "strmProfileIds": pids, "channelIds": [], "http": None,
    }
    r = s.put(P, json={"enabled": True, "config": {}, "grants": grants}, timeout=120)
    print("enable:", r.status_code)

    ui = s.get(P + "/ui", timeout=60)
    print("ui:", ui.status_code, len(ui.text), "字节")
    q = s.post(P + "/queries/rules", json={}, timeout=120).json().get("data", {}).get("result", {})
    print("rules:", json.dumps(q, ensure_ascii=False)[:300])

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "dist", "media.dup-scan-1.3.3.zip"))
