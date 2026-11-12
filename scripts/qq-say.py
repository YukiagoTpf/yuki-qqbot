#!/usr/bin/env python3
"""往 QQ 群里发一条话，给 herdr 里的女仆（scripts/butler-bridge.py）回消息用，也可以手动用。走 scripts/onebot.py，不经过 AstrBot。

用法：
  scripts/qq-say.py <群号> [--at <QQ号>] [--reply <消息号>] 要说的话……

- 要说的话可以分几个参数，用空格连起来；也可以给 - 从标准输入读（长一点、带引号的用这个）。
- 群里的话要短：超过 MAX 字截掉，提醒去本机看。
- 连着调会碰到 NapCat WebUI 的登录限流（login rate limit），隔一分钟再试。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import onebot  # noqa: E402

MAX = 400


def main() -> None:
    p = argparse.ArgumentParser(description="往 QQ 群里发一条话")
    p.add_argument("group", help="群号")
    p.add_argument("--at", help="要 @ 的 QQ 号")
    p.add_argument("--reply", help="要回复的那条消息的消息号")
    p.add_argument("text", nargs="+", help="要说的话；给 - 从标准输入读")
    a = p.parse_args()
    if not a.group.isdigit() or (a.at and not a.at.isdigit()) or (a.reply and not a.reply.lstrip("-").isdigit()):
        sys.exit("群号、QQ 号、消息号都得是数字")
    text = sys.stdin.read() if a.text == ["-"] else " ".join(a.text)
    text = text.strip()
    if not text:
        sys.exit("没有要说的话")
    if len(text) > MAX:
        text = text[:MAX].rstrip() + "……（太长了，剩下的上 Mac 看）"
    message = []
    if a.reply:
        message.append({"type": "reply", "data": {"id": a.reply}})
    if a.at:
        message += [{"type": "at", "data": {"qq": a.at}}, {"type": "text", "data": {"text": " "}}]
    message.append({"type": "text", "data": {"text": text}})
    onebot.call("send_group_msg", {"group_id": int(a.group), "message": message})
    print("发了")


if __name__ == "__main__":
    main()
