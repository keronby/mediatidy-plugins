#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在宿主本地直接把 media.dup-scan 打成标准 zip 包。
无需 docker/su，纯 Python 实现。
"""
import json, os, sys, tomllib, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
manifest = json.loads((ROOT / "plugin.json").read_text("utf-8"))

print("① 检查必需文件与规范")
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

for f in sorted(files):
    print("   %-32s %6d B" % (f.relative_to(ROOT).as_posix(), f.stat().st_size))

assert len(files) <= 1000 and sum(f.stat().st_size for f in files) <= 200 * 1024 * 1024

print("② 打 zip")
out = ROOT / "dist" / ("%s-%s.zip" % (manifest["id"], manifest["version"]))
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(files):
        z.write(f, f.relative_to(ROOT).as_posix())

size = out.stat().st_size
assert size <= 40 * 1024 * 1024
print("   %s  (%.1f KiB)" % (out, size / 1024))
print("\n✅ 打包完成")
