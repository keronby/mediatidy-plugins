# -*- coding: utf-8 -*-
"""一键自动部署已打包的 dup.zip 和 series.zip 到 MT"""
import os, sys, time, requests, json

MT = "http://127.0.0.1:2019/api"

def get_session():
    s = requests.Session()
    r = s.post(f"{MT}/auth/login", json={"username": "admin", "password": "fuck0000"}, timeout=10)
    tok = r.json().get("data", {}).get("token")
    if not tok:
        raise RuntimeError("MT 登录失败")
    s.headers["Authorization"] = f"Bearer {tok}"
    return s

def deploy_plugin(s, zip_path, plugin_id):
    if not os.path.exists(zip_path):
        print(f"[-] 文件不存在: {zip_path}")
        return False
    print(f"\n[+] 开始部署 {plugin_id} 来自 {zip_path} ...")
    
    # 1. 上传检查
    with open(zip_path, "rb") as fp:
        up = s.post(f"{MT}/plugins/inspect/upload", files={"file": ("p.zip", fp, "application/zip")}, timeout=120)
    if up.status_code != 200:
        print(f"[-] 上传检查失败: {up.status_code} {up.text[:200]}")
        return False
    ticket = up.json()["data"]["ticket"]
    print(f"[+] 获得安装 ticket: {ticket}")

    # 2. 提交安装
    inst = s.post(f"{MT}/plugins/install", json={"ticket": ticket}, timeout=180).json()
    job_id = inst.get("data", {}).get("id")
    print(f"[+] 安装任务 ID: {job_id}")

    # 3. 等待安装完成
    for _ in range(15):
        time.sleep(1)
        job = s.get(f"{MT}/plugins/install-jobs/{job_id}").json().get("data", {})
        if job.get("status") == "done":
            print(f"[+] 安装完成: stage={job.get('stage')}")
            break

    # 4. 授权与启用
    all_profiles = s.get(f"{MT}/organize/profiles", timeout=30).json().get("data", [])
    pids = [int(p["id"]) for p in all_profiles if "id" in p]
    
    # 构造标准 roots
    roots = [
        {"read": True, "write": False, "delete": False, "id": "root-strm", "name": "strm", "path": "/strm", "storageType": "cloud"},
        {"read": True, "write": False, "delete": False, "id": "root-strm-ts", "name": "特摄电影", "path": "/strm/特摄电影", "storageType": "cloud"},
        {"read": True, "write": False, "delete": False, "id": "root-strm-wy", "name": "外语电影", "path": "/strm/外语电影", "storageType": "cloud"},
        {"read": True, "write": False, "delete": False, "id": "root-strm-dm", "name": "动漫", "path": "/strm/动漫", "storageType": "cloud"}
    ]
    
    # 读取 manifest 获取权限列表和配置默认值
    p_info = s.get(f"{MT}/plugins/{plugin_id}").json().get("data", {})
    manifest = p_info.get("manifest", {})
    caps = manifest.get("permissions", [])
    cfg = {c["key"]: c.get("default") for c in manifest.get("config", [])}

    grants = {
        "roots": roots,
        "profileIds": pids,
        "strmProfileIds": pids,
        "capabilities": caps,
        "autoProfileIds": [],
        "channelIds": [],
        "http": []
    }

    body = {"enabled": True, "grants": grants}
    if cfg:
        body["config"] = cfg

    en = s.put(f"{MT}/plugins/{plugin_id}", json=body, timeout=30)
    print(f"[+] 授权与启用状态: {en.status_code}")
    
    # 5. 校验 UI
    ui = s.get(f"{MT}/plugins/{plugin_id}/ui", timeout=30)
    if ui.status_code == 200:
        print(f"[✔] UI 工作台加载成功! (返回 {len(ui.text)} 字节)")
    else:
        print(f"[-] UI 校验异常: {ui.status_code} {ui.text[:200]}")
    return ui.status_code == 200

if __name__ == "__main__":
    s = get_session()
    deploy_plugin(s, "/tmp/dup.zip", "media.dup-scan")
    deploy_plugin(s, "/tmp/series.zip", "media.series-dedup")
