#!/usr/bin/env python3
"""Install or update media.dup-scan without removing instance data.

Same-source updates preserve the existing config and grants. New permissions
are reported for explicit review instead of being silently granted.
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

    existing_resp = s.get(P, timeout=60)
    if existing_resp.status_code not in (200, 404):
        sys.exit("无法读取现有实例，拒绝继续：" + str(existing_resp.status_code))
    existing = existing_resp.json().get("data") if existing_resp.status_code == 200 else None

    with open(zip_path, "rb") as f:
        d = s.post(MT + "/plugins/inspect/upload", files={"file": ("p.zip", f, "application/zip")}, timeout=180).json()["data"]
    perm = d["release"]["manifest"]["permissions"]
    print("permissions:", perm)

    config = {}
    grants = {}
    if existing:
        old_permissions = set((existing.get("manifest") or {}).get("permissions", []))
        missing = set(perm) - old_permissions
        if missing:
            sys.exit("更新需要新增权限，请先在 MediaTidy 审阅并授予：" + ", ".join(sorted(missing)))
        config = existing.get("config") or {}
        grants = existing.get("grants") or {}
    # Upload inspection alone does not update installed code: consume the ticket
    # for BOTH installations and upgrades. Never DELETE the existing instance.
    result = s.post(MT + "/plugins/install", json={"ticket": d["ticket"],
                    "grants": grants if existing else {"capabilities": perm},
                    "config": config}, timeout=180)
    if result.status_code >= 400:
        sys.exit("安装/更新失败：" + result.text[:500])
    for _ in range(60):
        time.sleep(3)
        installed = s.get(P, timeout=60)
        if installed.status_code == 200:
            current = installed.json().get("data") or {}
            if (current.get("manifest") or {}).get("version") == d["release"]["manifest"]["version"]:
                break
    else:
        sys.exit("安装/更新超时：宿主尚未加载目标版本；未修改配置或授权")

    if not existing:
        # New installs receive only the permissions declared by this release.
        # Existing instances retain their exact grants/configuration above.
        grants = {
            "autoProfileIds": [], "profileRevisions": {}, "strmRevisions": {},
            "capabilities": perm,
            "roots": [{"read": True, "write": True, "delete": True,
                       "id": "root-strm", "name": "strm", "path": ROOT,
                       "storageType": "local"}],
            "profileIds": [], "strmProfileIds": [], "channelIds": [], "http": None,
        }
    if existing:
        current = s.get(P, timeout=60).json()["data"]
        for field in ("instanceId", "config", "grants", "enabled"):
            if current.get(field) != existing.get(field):
                sys.exit("更新后实例字段发生变化，请检查：" + field)
        if current.get("grants") is None:
            sys.exit("更新后宿主未返回授权，拒绝继续")
        print("实例与配置保留；授权由宿主按现有授权规范化返回")
    else:
        r = s.put(P, json={"enabled": True, "config": config, "grants": grants}, timeout=120)
        if r.status_code >= 400:
            sys.exit("启用插件失败：" + r.text[:500])
        print("enable:", r.status_code)

    ui = s.get(P + "/ui", timeout=60)
    print("ui:", ui.status_code, len(ui.text), "字节")
    q = s.post(P + "/queries/rules", json={}, timeout=120).json().get("data", {}).get("result", {})
    print("rules:", json.dumps(q, ensure_ascii=False)[:300])

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "dist", "media.dup-scan-1.4.2.zip"))
