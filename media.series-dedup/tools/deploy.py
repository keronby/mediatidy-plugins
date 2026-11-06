import json
import os
import sys
import requests
import subprocess

MT = os.environ.get("MT_BASE", "http://127.0.0.1:2019/api")
PLUGIN_ID = "media.series-dedup"

def get_auth_token():
    try:
        r = requests.post(f"{MT}/auth/login", json={"username": os.environ.get("MT_USER","admin"), "password": os.environ.get("MT_PASSWORD","")}, timeout=10)
        return r.json().get("data", {}).get("token")
    except Exception as e:
        print("登录获取 token 失败:", e)
        return None

def main():
    pack_script = os.path.join(os.path.dirname(__file__), "pack_local.py")
    res = subprocess.run([sys.executable, pack_script], capture_output=True, text=True)
    if res.returncode != 0:
        print("打包失败:\n", res.stderr)
        sys.exit(1)
    print(res.stdout.strip())

    zip_path = os.path.join(os.path.dirname(__file__), "..", "dist", f"{PLUGIN_ID}-1.0.0.zip")
    token = get_auth_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    print(f"准备上传并安装插件 {PLUGIN_ID}...")
    with open(zip_path, "rb") as f:
        r = requests.post(
            f"{MT}/plugins/inspect/upload",
            headers=headers,
            files={"file": (f"{PLUGIN_ID}-1.0.0.zip", f, "application/zip")},
            timeout=180
        )
    print("上传 inspect 结果:", r.status_code, r.text[:300])

    if r.status_code == 200:
        inspect_data = r.json().get("data", {})
        ticket = inspect_data.get("ticket")
        release = inspect_data.get("release", {})
        rel_hash = release.get("hash")
        print(f"获取到 ticket: {ticket}, hash: {rel_hash}")

        # 获取所有方案 ID 动态授权
        prof_res = requests.get(f"{MT}/organize/profiles", headers=headers, timeout=10).json()
        p_ids = [str(p["id"]) for p in prof_res.get("data", [])]

        install_payload = {
            "ticket": ticket,
            "releaseHash": rel_hash,
            "installDependencies": False,
            "grant": {
                "roots": ["/strm", "/vol1/1000/strm", "/data", "/config"],
                "profileIds": p_ids,
                "strmProfileIds": p_ids,
                "ruleIds": ["*"]
            }
        }
        inst_r = requests.post(f"{MT}/plugins/{PLUGIN_ID}/install", headers=headers, json=install_payload, timeout=60)
        print("安装响应:", inst_r.status_code, inst_r.text[:300])

        en_r = requests.post(f"{MT}/plugins/{PLUGIN_ID}/enable", headers=headers, timeout=10)
        print("启用响应:", en_r.status_code, en_r.text[:300])

if __name__ == "__main__":
    main()
