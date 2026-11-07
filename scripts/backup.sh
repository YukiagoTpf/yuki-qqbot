#!/usr/bin/env bash
# 停机后打包 data/：QQ 的数据库和 AstrBot 的 sqlite 在运行中拷贝可能不一致。
# 打包前在跑的服务，打包完会重新拉起。
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p backups
out="backups/data-$(date +%Y%m%d-%H%M%S).tar.gz"

running=$(docker compose ps -q)
if [ -n "$running" ]; then
  docker compose stop
  trap 'docker compose start' EXIT
fi

tar -czf "$out" data
echo "$out ($(du -h "$out" | cut -f1))"
