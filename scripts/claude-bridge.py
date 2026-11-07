#!/usr/bin/env python3
"""清 issue 的宿主机桥：bot 管理员在群里 @bot 说「清issue」，AstrBot 容器里的 astrbot_plugin_issue POST 过来，
这里用 herdr 在 astrbot-napcat 工作区开一个新标签页，起一个 bypass 模式的 Claude Code，让它把猜题仓库 open 的
issue 处理完、上线；弄不了的经 scripts/onebot.py 在群里 @管理员说明。

herdr、claude、gh 都在宿主机上，容器里调不到，所以和 gh 桥一样隔一层 HTTP。launchd 常驻（make claude-bridge-install）。

- 只监听 127.0.0.1，容器经 192.168.5.2 访问。请求要带 issue 插件的令牌（和 gh 桥同一个 bridge_token）。
- 只能做这一件事：给 Claude 的话是写死的 PROMPT，插件只传群号和管理员的 QQ 号（纯数字）。
- 上一个清 issue 的 Claude 还在干活（名字以 AGENT 开头、状态 working / blocked）就不再起。没有 open 的 issue 不起。
- 记录在 data/claude-bridge/bridge.log。

python3 scripts/claude-bridge.py --test  开个标签页起 Claude，只让它回一句话，确认能起来，再关掉。
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
LOG = ROOT / "data" / "claude-bridge" / "bridge.log"
HOST, PORT = "127.0.0.1", 6192
HERDR = shutil.which("herdr") or "/opt/homebrew/bin/herdr"
GH = shutil.which("gh") or "/opt/homebrew/bin/gh"
REPO = "YukiagoTpf/astrbot_plugin_guessless"
WORKSPACE = "astrbot-napcat"  # herdr 工作区的名字，没有就建一个
AGENT = "issues-"  # Claude 在 herdr 里的名字前缀，后面接时间
NUMBER = re.compile(r"^\d{5,12}$")
PROMPT = """bot 管理员（QQ {admin}）在 QQ 群 {group} 里 @bot 说了「清issue」。请把猜题插件仓库 {repo} 现在 open 的 issue 都处理掉：
{issues}

1. 先读 CLAUDE.md 和 plugins/astrbot_plugin_guessless/AGENTS.md，按里面的约定改代码、验证、提交。用 gh issue view <号> --repo {repo} --comments 看正文和评论。
2. 能弄的直接弄完上线：bug、调优、规则说清楚了的新功能都直接改，不用先问。管理员这次已经授权：提交后直接推 main，按 CLAUDE.md 的空闲检查重启 AstrBot（有人在玩就等一会再试，别硬重启），确认日志里插件加载了、改到的题库建好了，再用 gh issue close <号> --repo {repo} --comment "<中文，一两句说改了什么>" 关掉。
3. 弄不了的不要动、不要关：描述含糊看不出要改什么、要管理员拍板规则或取舍、碰到 AGENTS.md 红线的。
4. 全部处理完，用 scripts/onebot.py 在群 {group} 发一条消息（群里的话要短，不复述敏感的原话）：
   - 有弄不了的：@管理员，逐个说是哪个 issue、卡在哪、要他定什么，请他上 Mac mini 看。
   - 都弄完了：不用 @，一句话说清了哪几个、已上线。
   发法：scripts/onebot.py send_group_msg '{{"group_id": {group}, "message": [{{"type": "at", "data": {{"qq": "{admin}"}}}}, {{"type": "text", "data": {{"text": " 要说的话"}}}}]}}'（不 @ 就去掉 at 那段）。报 login rate limit 就隔一分钟再试。
5. 仓库是公开的：群号、QQ 号不能写进代码、文档、提交信息和 GitHub 评论。"""
TEST_PROMPT = "这是连通测试。\n只回复「测试收到」四个字，\n别的什么都不做。"  # 分几行，顺便测多行能不能一次交上去

LOCK = threading.Lock()  # 一次只起一个


def log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} {text}\n")


def run(cmd: list[str], timeout: int = 60) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def herdr(*args: str, timeout: int = 60) -> dict:
    """herdr 的控制命令都回 JSON，出错的有 error 字段。"""
    out = json.loads(run([HERDR, *args], timeout) or "{}")
    if out.get("error"):
        raise RuntimeError(f"herdr {' '.join(args[:2])}: {out['error']}")
    return out.get("result") or {}


def open_issues() -> list[dict]:
    out = run([GH, "issue", "list", "--repo", REPO, "--state", "open", "--limit", "50", "--json", "number,title"])
    return [{"number": i["number"], "title": i["title"]} for i in json.loads(out)]


def running() -> str:
    """还在干活的清 issue 的 Claude 的名字，没有返回空。"""
    for a in herdr("agent", "list").get("agents", []):
        name = str(a.get("name") or "")
        if name.startswith(AGENT) and a.get("agent_status") in ("working", "blocked"):
            return name
    return ""


def launch(prompt: str) -> dict:
    """在 WORKSPACE 开个不抢焦点的新标签页，等 shell 起好后起 bypass 模式的 Claude，把 prompt 交给它。"""
    spaces = herdr("workspace", "list").get("workspaces", [])
    ws = next((w["workspace_id"] for w in spaces if w.get("label") == WORKSPACE), "")
    if not ws:
        ws = herdr("workspace", "create", "--cwd", str(ROOT), "--label", WORKSPACE, "--no-focus")["workspace"]["workspace_id"]
    tab = herdr("tab", "create", "--workspace", ws, "--cwd", str(ROOT), "--label", "清issue", "--no-focus")
    tab_id, pane = tab["tab"]["tab_id"], tab["root_pane"]["pane_id"]
    name = AGENT + time.strftime("%m%d-%H%M")
    # 新标签页的 shell 还在加载配置时 agent start 会报 pane 不可用，等一下再试
    for attempt in range(10):
        try:
            herdr("agent", "start", name, "--kind", "claude", "--pane", pane, "--timeout", "60000",
                  "--", "--dangerously-skip-permissions", timeout=90)
            break
        except RuntimeError:
            if attempt == 9:
                raise
            time.sleep(2)
    herdr("agent", "prompt", name, prompt)
    # 多行的话贴进去以后，紧跟着的回车有时被 Claude Code 吞掉，字停在输入框里：几秒内没开始干活就再按一次回车
    for _ in range(4):
        time.sleep(3)
        if status(name) in ("working", "blocked"):
            break
        herdr("agent", "send-keys", name, "enter")
    return {"workspace": ws, "tab": tab_id, "pane": pane, "agent": name, "status": status(name)}


def status(name: str) -> str:
    agent = herdr("agent", "get", name)
    return str((agent.get("agent") or agent).get("agent_status") or "")


def clear(group: str, admin: str) -> dict:
    with LOCK:
        if busy := running():
            log(f"{busy} 还在干活，不再起")
            return {"ok": False, "error": "busy"}
        issues = open_issues()
        if not issues:
            log("没有 open 的 issue，不起")
            return {"ok": True, "issues": []}
        lines = "\n".join(f"- #{i['number']} {i['title']}" for i in issues)
        where = launch(PROMPT.format(admin=admin, group=group, repo=REPO, issues=lines))
    log(f"起了 {where['agent']}（{where['tab']}），{len(issues)} 个 issue：{' '.join('#%d' % i['number'] for i in issues)}")
    return {"ok": True, "issues": issues, "agent": where["agent"]}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/clear":
            return self._reply(404, {"ok": False, "error": "not_found"})
        try:
            token = TOKEN_FILE.read_text().strip()
        except OSError:
            return self._reply(503, {"ok": False, "error": "no_token"})
        if not hmac.compare_digest(self.headers.get("X-Token", ""), token):
            return self._reply(403, {"ok": False, "error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            assert 0 <= length <= 1000
            body = json.loads(self.rfile.read(length) or b"{}")
            group, admin = str(body["group"]), str(body["admin"])
            assert NUMBER.match(group) and NUMBER.match(admin)
        except Exception:
            return self._reply(400, {"ok": False, "error": "bad_request"})
        try:
            return self._reply(200, clear(group, admin))
        except Exception as e:
            log(f"失败：{type(e).__name__} {e}")
            return self._reply(500, {"ok": False, "error": "launch_failed"})

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # 有用的都在 bridge.log 里


def test() -> None:
    where = launch(TEST_PROMPT)
    print("起了", where)
    try:
        herdr("agent", "wait", where["agent"], "--timeout", "120000", timeout=150)
        print(run([HERDR, "agent", "read", where["agent"], "--source", "recent-unwrapped", "--lines", "20"])[-1500:])
        print([a for a in herdr("agent", "list").get("agents", []) if a.get("pane_id") == where["pane"]])
    finally:
        herdr("tab", "close", where["tab"])
        print("关了", where["tab"])


def main() -> None:
    if sys.argv[1:2] == ["--test"]:
        return test()
    log(f"启动，监听 {HOST}:{PORT}，herdr 工作区 {WORKSPACE}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
