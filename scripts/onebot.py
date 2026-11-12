#!/usr/bin/env python3
"""通过 NapCat WebUI 的调试接口直接调 OneBot v11 action，不经过 AstrBot。

用法：
  scripts/onebot.py get_group_list
  scripts/onebot.py get_group_member_list '{"group_id": 123456789}'

调用会临时创建 NapCat 的 debug-primary 适配器，结束后关掉。
"""
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env(key, default=None):
    m = re.search(rf"^{key}=(.*)$", (ROOT / ".env").read_text(), re.M)
    return m.group(1).strip() if m else default


BASE = f"http://127.0.0.1:{env('NAPCAT_WEBUI_PORT', '6099')}/api"


def post(path, body, cred=None):
    headers = {"Content-Type": "application/json"}
    if cred:
        headers["Authorization"] = f"Bearer {cred}"
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), headers)
    resp = json.load(urllib.request.urlopen(req, timeout=30))
    if resp.get("code") != 0:
        sys.exit(f"{path}: {resp.get('message')}")
    return resp["data"]


def call(action, params=None):
    """登录 WebUI、调一次 action、关掉调试适配器，返回 data。scripts/qq-say.py 也用它。"""
    token_hash = hashlib.sha256((env("NAPCAT_WEBUI_TOKEN") + ".napcat").encode()).hexdigest()
    cred = post("/auth/login", {"hash": token_hash})["Credential"]
    try:
        result = post("/Debug/call", {"action": action, "params": params or {}}, cred)
    finally:
        post("/Debug/close/debug-primary", {}, cred)
    return result.get("data", result)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip())
    action = sys.argv[1]
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(call(action, params), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
