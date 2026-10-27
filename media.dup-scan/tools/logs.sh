#!/bin/bash
# 看插件日志（宿主 run 只给一句概括，真因都在这里）
# 用法：bash tools/logs.sh [条数]
N="${1:-20}"
python3 - "$N" <<'PY'
import json, sys, requests
n = int(sys.argv[1])
s = requests.Session()
tok = s.post("http://127.0.0.1:2019/api/auth/login",
             json={"username": "admin", "password": "fuck0000"}, timeout=30).json()["data"]["token"]
s.headers["Authorization"] = "Bearer " + tok
it = s.get("http://127.0.0.1:2019/api/plugins/media.dup-scan/logs", timeout=60).json().get("data", {}).get("items", [])
for x in it[:n]:
    print("[%s] %s" % (x.get("level"), x.get("message")))
PY
