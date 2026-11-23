#!/usr/bin/env bash
# 生产启动（Linux/macOS）：单 worker + 信任反向代理头，禁止 --reload
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python}"
if [ -x ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; fi

LISTEN_HOST="${MAIC_HOST:-${HOST:-0.0.0.0}}"
LISTEN_PORT="${MAIC_PORT:-${PORT:-8001}}"
# 仅当 Nginx 与后端在同一台机器时保留默认值；跨机器请改成 Nginx 的 IP
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

exec "$PYTHON" -m uvicorn app.main:app \
  --host "$LISTEN_HOST" --port "$LISTEN_PORT" --workers 1 \
  --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" --log-level info
