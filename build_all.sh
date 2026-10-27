#!/bin/bash
# 一键打包并部署「媒体查重」与「剧集查重」到 MT 容器
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo " 正在打包「媒体查重」(media.dup-scan) ..."
echo "=========================================="
docker exec mediatidy rm -rf /app/data/.pack_dup
docker exec mediatidy mkdir -p /app/data/.pack_dup
docker cp "$DIR/media.dup-scan/." mediatidy:/app/data/.pack_dup/
docker exec -w /app/data/.pack_dup mediatidy /usr/local/bin/uv lock
docker exec -e PYTHONDONTWRITEBYTECODE=1 mediatidy python3 /app/data/.packwork/pack_manual.py /app/data/.pack_dup
docker cp mediatidy:/app/data/.pack_dup/dist/media.dup-scan-1.0.0.zip /tmp/dup.zip
chmod 644 /tmp/dup.zip
echo "✔ media.dup-scan 打包成功 -> /tmp/dup.zip"

echo ""
echo "=========================================="
echo " 正在打包「剧集查重」(media.series-dedup) ..."
echo "=========================================="
docker exec mediatidy rm -rf /app/data/.pack_series
docker exec mediatidy mkdir -p /app/data/.pack_series
docker cp "$DIR/media.series-dedup/." mediatidy:/app/data/.pack_series/
docker exec -w /app/data/.pack_series mediatidy /usr/local/bin/uv lock
docker exec -e PYTHONDONTWRITEBYTECODE=1 mediatidy python3 /app/data/.packwork/pack_manual.py /app/data/.pack_series
docker cp mediatidy:/app/data/.pack_series/dist/media.series-dedup-1.0.0.zip /tmp/series.zip
chmod 644 /tmp/series.zip
echo "✔ media.series-dedup 打包成功 -> /tmp/series.zip"

echo ""
echo "=========================================="
echo " 正在自动安装并部署至 MediaTidy ..."
echo "=========================================="
python3 "$DIR/deploy_all.py"
