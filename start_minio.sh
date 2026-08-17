#!/usr/bin/env bash
# 启动本地 MinIO（S3 兼容），作为 exercise_extractor 的 OSS 后端。
# 默认：http://127.0.0.1:9000，账号 minioadmin/minioadmin，桶 exercises。
# 环境变量可覆盖：MINIO_ROOT_USER / MINIO_ROOT_PASSWORD / MINIO_BUCKET /
# MINIO_PORT / MINIO_CONSOLE_PORT / MINIO_DATA_DIR。
set -euo pipefail

MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_BUCKET="${MINIO_BUCKET:-exercises}"
MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"
MINIO_DATA_DIR="${MINIO_DATA_DIR:-$(pwd)/.minio-data}"

mkdir -p "$MINIO_DATA_DIR"

if docker ps -a --format '{{.Names}}' | grep -qx minio; then
  echo "MinIO 容器已存在，先启动它："
  docker start minio
else
  echo "启动 MinIO 容器（API :${MINIO_PORT}，控制台 :${MINIO_CONSOLE_PORT}）..."
  docker run -d --name minio \
    -p "${MINIO_PORT}:9000" \
    -p "${MINIO_CONSOLE_PORT}:9001" \
    -e MINIO_ROOT_USER="$MINIO_ROOT_USER" \
    -e MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
    -v "$MINIO_DATA_DIR:/data" \
    minio/minio server /data --console-address ":${MINIO_CONSOLE_PORT}"
fi

echo "等待 MinIO 就绪..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${MINIO_PORT}/minio/health/live" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "创建桶 ${MINIO_BUCKET} ..."
docker exec minio mc alias set local \
  "http://127.0.0.1:${MINIO_PORT}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
docker exec minio mc mb --ignore-existing "local/${MINIO_BUCKET}" >/dev/null

echo "MinIO 已就绪："
echo "  API:      http://127.0.0.1:${MINIO_PORT}"
echo "  控制台:   http://127.0.0.1:${MINIO_CONSOLE_PORT}"
echo "  Access:   ${MINIO_ROOT_USER} / ${MINIO_ROOT_PASSWORD}"
echo "  Bucket:   ${MINIO_BUCKET}"
