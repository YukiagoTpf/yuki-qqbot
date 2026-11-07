#!/usr/bin/env bash
# 等群里没有进行中的题再重启 AstrBot：最近 130 秒内 bot 没发过题图 / 提示图，20 秒内没人 @bot 开题。
# 题目限时 2 分钟，所以 130 秒内没发过纯图片就说明没有进行中的题。最多等 3 小时，等不到就什么都不做。
set -euo pipefail
cd "$(dirname "$0")/.."

BOT_QQ=$(sed -n 's/^NAPCAT_ACCOUNT=//p' .env)
log() { echo "[$(date +%H:%M:%S)] $*"; }
idle() {
  local img cmd
  img=$(docker logs --since 130s napcat 2>&1 | grep -cE "发送 -> 群聊 .*\\($BOT_QQ\\)\\] \\[图片\\][[:space:]]*$" || true)
  cmd=$(docker logs --since 20s napcat 2>&1 | grep -cE "接收 <- 群聊 .*@[^ ]* \\($BOT_QQ\\).*猜" || true)
  [ "$img" -eq 0 ] && [ "$cmd" -eq 0 ]
}

log "waiting for idle"
deadline=$((SECONDS + 3 * 3600))
until idle; do
  [ $SECONDS -lt $deadline ] || { log "not idle within 3h, nothing changed"; exit 1; }
  sleep 5
done
log "idle, restarting astrbot"
docker compose restart -t 5 astrbot
log "done"
