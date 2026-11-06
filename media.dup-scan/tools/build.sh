#!/bin/bash
# 打包 media.dup-scan 并推到 MT 容器 /app/data/.pack2，产出 dist/media.dup-scan-1.3.3.zip
# 用法：bash tools/build.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="/tmp/mdp-build"

rm -rf "$SRC"; mkdir -p "$SRC"
cp -r "$HERE/plugin.py" "$HERE/plugin.json" "$HERE/pyproject.toml" "$HERE/uv.lock" "$HERE/core" "$HERE/ui" "$SRC/"
rm -rf "$SRC/core/__pycache__"
chmod -R a+rX "$SRC"

su ${MT_DEPLOY_USER:-hua} -c "
  docker exec mediatidy rm -rf /app/data/.pack2 &&
  docker exec mediatidy mkdir -p /app/data/.pack2 &&
  docker cp $SRC/. mediatidy:/app/data/.pack2/ &&
  docker exec -w /app/data/.pack2 mediatidy /usr/local/bin/uv lock >/dev/null 2>&1;
  docker exec -w /app/data/.pack2 mediatidy /usr/local/bin/uv lock 2>&1 | tail -1;
  docker exec -e PYTHONDONTWRITEBYTECODE=1 mediatidy python3 /app/data/.packwork/pack_manual.py /app/data/.pack2 2>&1 | tail -4;
  docker cp mediatidy:/app/data/.pack2/dist/media.dup-scan-1.3.3.zip /tmp/dup.zip &&
  chmod 644 /tmp/dup.zip
" 2>/dev/null
mkdir -p "$HERE/dist"
cp /tmp/dup.zip "$HERE/dist/media.dup-scan-1.3.3.zip"
echo "✅ 产物：$HERE/dist/media.dup-scan-1.3.3.zip"
