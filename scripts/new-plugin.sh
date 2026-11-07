#!/usr/bin/env bash
# 用法：scripts/new-plugin.sh astrbot_plugin_xxx
# 生成最小插件骨架，注册一条 /xxx 指令。
set -euo pipefail
cd "$(dirname "$0")/.."

name=${1:-}
if [[ ! "$name" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "用法：make new-plugin name=astrbot_plugin_xxx（小写字母、数字、下划线）" >&2
  exit 1
fi
dir="plugins/$name"
if [ -e "$dir" ]; then
  echo "$dir 已存在" >&2
  exit 1
fi
cmd=${name#astrbot_plugin_}
if [[ ! "$cmd" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "去掉 astrbot_plugin_ 前缀后的 \"$cmd\" 不能当指令名" >&2
  exit 1
fi

mkdir -p "$dir"
cat >"$dir/metadata.yaml" <<EOF
name: $name
desc: TODO
version: 0.1.0
author: $(id -un)
EOF

cat >"$dir/main.py" <<EOF
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class Plugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("$cmd")
    async def $cmd(self, event: AstrMessageEvent):
        """TODO"""
        logger.info(f"$cmd from {event.get_sender_name()}")
        yield event.plain_result("ok")
EOF

touch "$dir/requirements.txt"
echo "created  $dir"
scripts/link-plugins.sh
