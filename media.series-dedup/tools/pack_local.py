import os
import sys
import zipfile

def pack():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dist_dir = os.path.join(base_dir, "dist")
    os.makedirs(dist_dir, exist_ok=True)
    zip_path = os.path.join(dist_dir, "media.series-dedup-1.0.0.zip")

    # 需要包含的文件与目录
    include_files = [
        "plugin.json",
        "plugin.py",
        "pyproject.toml",
        "uv.lock",
        "ui/icon.png",
    ]
    include_dirs = [
        "core",
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in include_files:
            fp = os.path.join(base_dir, f)
            if os.path.exists(fp):
                zf.write(fp, arcname=f)
        for d in include_dirs:
            dp = os.path.join(base_dir, d)
            if os.path.exists(dp):
                for root, _, files in os.walk(dp):
                    for file in files:
                        if file.endswith((".py", ".json", ".txt")) and not file.endswith(".pyc"):
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, base_dir)
                            zf.write(full_path, arcname=rel_path)

    print(f"打包完成: {zip_path} ({os.path.getsize(zip_path)} 字节)")
    return zip_path

if __name__ == "__main__":
    pack()
