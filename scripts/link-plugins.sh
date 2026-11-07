#!/usr/bin/env bash
# 把 plugins/<name> 以软链形式放进 AstrBot 的插件目录。
# 软链指向容器内路径 /AstrBot/local-plugins/<name>，在宿主机上看是断链，属正常。
# 软链插件不会被插件市场的同名安装覆盖（AstrBot 拒绝覆盖软链目录）。
set -euo pipefail
cd "$(dirname "$0")/.."

store=data/astrbot/plugins
target_root=/AstrBot/local-plugins
mkdir -p "$store"

for dir in plugins/*/; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir")
  case "$name" in _* | .*) continue ;; esac
  link="$store/$name"
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo "跳过 $name：$link 是真实目录（可能是插件市场装的同名插件）" >&2
    continue
  fi
  if [ "$(readlink "$link" 2>/dev/null)" != "$target_root/$name" ]; then
    ln -sfn "$target_root/$name" "$link"
    echo "linked   $name"
  fi
done

# 源目录已删掉的插件，清理对应软链。
for link in "$store"/*; do
  [ -L "$link" ] || continue
  name=$(basename "$link")
  case "$(readlink "$link")" in
    "$target_root"/*)
      if [ ! -d "plugins/$name" ]; then
        rm "$link"
        echo "unlinked $name"
      fi
      ;;
  esac
done
