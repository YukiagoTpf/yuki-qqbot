#!/usr/bin/env python3
"""提 issue 的宿主机桥：AstrBot 容器里的 astrbot_plugin_issue 把 DeepSeek 整理好的 issue POST 过来，这里调本机的 gh 建到猜题仓库。

gh 装在宿主机上，登录态在钥匙串里，容器里调不到，也不该把 token 给容器，所以隔一层 HTTP。launchd 常驻（make gh-bridge-install）。

- 只监听 127.0.0.1。Colima 的容器经 192.168.5.2 访问宿主机的 127.0.0.1；host.docker.internal 会被 Clash 劫持。
- 请求要带插件生成的令牌（插件数据目录里的 bridge_token）。
- 只能对 REPO 列出 open 的 issue、建 issue，标签只能是 LABELS 里的。别的仓库、别的操作都不行。
- 每小时最多建 HOURLY 个，插件出了毛病也不会往公开仓库里刷屏。
- 记录在 data/gh-bridge/bridge.log。

python3 scripts/gh-bridge.py --list  打印 open 的 issue，不起服务。
"""

from __future__ import annotations

import hmac
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data" / "astrbot" / "plugin_data" / "astrbot_plugin_issue" / "bridge_token"
LOG = ROOT / "data" / "gh-bridge" / "bridge.log"
HOST, PORT = "127.0.0.1", 6191
GH = shutil.which("gh") or "/opt/homebrew/bin/gh"
REPO = "YukiagoTpf/astrbot_plugin_guessless"
LABELS = {"bug", "enhancement"}
MAX_TITLE, MAX_BODY = 80, 6000
HOURLY = 10
TIMEOUT = 30
ISSUE_URL = re.compile(rf"https://github\.com/{re.escape(REPO)}/issues/(\d+)")

LOCK = threading.Lock()  # 一次只建一个，HOURLY 才数得准
created: list[float] = []


def log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} {text}\n")


def gh(*args: str, stdin: str | None = None) -> str:
    r = subprocess.run([GH, *args, "--repo", REPO], input=stdin, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:])
    return r.stdout


def open_issues() -> list[dict]:
    out = json.loads(gh("issue", "list", "--state", "open", "--limit", "50", "--json", "number,title,url"))
    return [{"number": i["number"], "title": i["title"], "url": i["url"]} for i in out]


def create(title: str, body: str, label: str) -> dict:
    with LOCK:
        now = time.time()
        created[:] = [t for t in created if now - t < 3600]
        if len(created) >= HOURLY:
            log(f"这小时已经建了 {HOURLY} 个，不建：{title}")
            return {"ok": False, "error": "hourly_limit"}
        out = gh("issue", "create", "--title", title, "--body-file", "-", "--label", label, stdin=body)
        m = ISSUE_URL.search(out)
        if not m:
            raise RuntimeError(f"gh 没返回 issue 地址：{out[-200:]}")
        created.append(now)
    log(f"建了 #{m[1]} [{label}] {title}")
    return {"ok": True, "number": int(m[1]), "url": m[0]}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ("/list", "/create"):
            return self._reply(404, {"ok": False, "error": "not_found"})
        try:
            token = TOKEN_FILE.read_text().strip()
        except OSError:
            return self._reply(503, {"ok": False, "error": "no_token"})
        if not hmac.compare_digest(self.headers.get("X-Token", ""), token):
            return self._reply(403, {"ok": False, "error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= MAX_BODY * 4:
            return self._reply(413, {"ok": False, "error": "too_large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/create":
                title, text, label = str(body["title"]).strip(), str(body["body"]).strip(), body["label"]
                assert 0 < len(title) <= MAX_TITLE and "\n" not in title and 0 < len(text) <= MAX_BODY and label in LABELS
        except Exception:
            return self._reply(400, {"ok": False, "error": "bad_request"})
        try:
            if self.path == "/list":
                return self._reply(200, {"ok": True, "issues": open_issues()})
            return self._reply(200, create(title, text, label))
        except Exception as e:
            log(f"{self.path} 失败：{type(e).__name__} {e}")
            return self._reply(500, {"ok": False, "error": "gh_failed"})

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # 有用的都在 bridge.log 里


def main() -> None:
    if sys.argv[1:2] == ["--list"]:
        print(json.dumps(open_issues(), ensure_ascii=False, indent=2))
        return
    log(f"启动，监听 {HOST}:{PORT}，仓库 {REPO}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
