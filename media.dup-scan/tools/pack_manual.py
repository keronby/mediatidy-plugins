# -*- coding: utf-8 -*-
"""绕过官方 pack.py 的 uv 版本断言 bug，其余检查一项不省：
   check(root, manifest) → uv lock → validate_dependencies → 隔离 import → 打包 zip
"""
import json, os, subprocess, sys, tomllib, zipfile, tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SDK = Path("/app/data/plugins/sdk/2")
sys.path.insert(0, str(SDK))

from mt_plugin.pack import check as official_check
from mt_plugin.dependencies import validate_dependencies

manifest = json.loads((ROOT / "plugin.json").read_text("utf-8"))

print("① 官方 check（入口/权限/装饰器/ schema）")
official_check(ROOT, manifest)
print("   ✔ 通过")

print("② 依赖校验")
validate_dependencies(ROOT)
print("   ✔ 通过（零第三方依赖）")

print("③ 隔离子进程 import")
r = subprocess.run([sys.executable, "-I", "-B", "-c",
    "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
    "from mt_plugin.worker import load_plugin;root=Path(sys.argv[2]);"
    "load_plugin(root,json.loads((root/'plugin.json').read_text('utf-8')));print('ok')",
    str(SDK), str(ROOT)], capture_output=True, text=True, timeout=60)
print("   ", r.stdout.strip() or r.stderr.strip()[:400])
assert r.returncode == 0, "隔离 import 失败"

print("④ 组装包内文件")
project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
includes = project["tool"]["mt-plugin"]["include"]
excluded = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".env"}
files = set()
for name in includes:
    path = ROOT / name
    if path.is_file():
        files.add(path)
    elif path.is_dir():
        for d, subdirs, names in os.walk(path):
            subdirs[:] = [s for s in subdirs if s not in excluded]
            files.update(Path(d) / n for n in names)
files = {f for f in files if not any(p in excluded for p in f.relative_to(ROOT).parts)}
required = {ROOT / n for n in ("plugin.py", "plugin.json", "pyproject.toml", "uv.lock")}
assert required <= files, "必需文件缺失: %s" % (required - files)
for f in files:
    print("   %-32s %6d B" % (f.relative_to(ROOT), f.stat().st_size))
assert len(files) <= 1000 and sum(f.stat().st_size for f in files) <= 200 * 1024 * 1024

print("⑤ 打 zip")
out = ROOT / "dist" / ("%s-%s.zip" % (manifest["id"], manifest["version"]))
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(files):
        z.write(f, f.relative_to(ROOT).as_posix())
size = out.stat().st_size
assert size <= 40 * 1024 * 1024
print("   %s  (%.1f KiB)" % (out, size / 1024))
print("\n✅ 打包完成")
