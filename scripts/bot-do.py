#!/usr/bin/env python3
"""替主人在群里对 bot 说一句话，触发插件：猜题、杀炫区、搜烧鸡、提issue……给 herdr 里的女仆用。

用法：
  scripts/bot-do.py <群号> <主人QQ号> [--reply <消息号>] [--no-at] 要说的话……

- 等于主人在那个群里发了「@bot 要说的话」：经女仆桥（scripts/butler-bridge.py）交给 AstrBot 里的女仆插件，它当成主人
  的消息派发给各插件，白名单、管理员身份、各插件的指令照常生效。
- --reply：插件回话时引用哪条消息。在主人说话的那个群里给他那条的消息号；别的群不给（不引用）。
- --no-at：不带 @bot（插件是旁听群聊、不要 @ 的才用）。
- 以「女仆」「管家」「委派」「委托」开头的不收，免得绕回女仆自己。
- 成功只表示插件收到并派发了，插件怎么回的去群里看（scripts/onebot.py get_group_msg_history）。
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data" / "astrbot" / "plugin_data" / "astrbot_plugin_butler" / "bridge_token"
URL = "http://127.0.0.1:6193/inject"
ERRORS = {
    "plugin_offline": "AstrBot 那头没来取（AstrBot 没起来或者女仆插件没加载）",
    "not_admin": "这个 QQ 号不是 bot 管理员",
    "not_in_group": "主人不在这个群，或者拿不到群成员信息",
    "no_platform": "AstrBot 里没有 QQ 适配器",
}


def main() -> None:
    p = argparse.ArgumentParser(description="替主人在群里对 bot 说一句话")
    p.add_argument("group", help="群号")
    p.add_argument("admin", help="主人的 QQ 号")
    p.add_argument("--reply", default="0", help="插件回话时引用的消息号")
    p.add_argument("--no-at", action="store_true", help="不带 @bot")
    p.add_argument("text", nargs="+", help="要说的话")
    a = p.parse_args()
    body = {"group": a.group, "admin": a.admin, "reply_id": a.reply, "at": not a.no_at, "text": " ".join(a.text)}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Content-Type": "application/json", "X-Token": TOKEN_FILE.read_text().strip()})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=40))
    except urllib.error.HTTPError as e:
        sys.exit(f"女仆桥拒绝了：{e.code}（群号、QQ 号、消息号要是数字；不能以女仆/管家/委派/委托开头）")
    if not res.get("ok"):
        sys.exit("没说成：" + ERRORS.get(res.get("error"), str(res.get("error"))))
    print("说了，插件已经收到")


if __name__ == "__main__":
    main()
