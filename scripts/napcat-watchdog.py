#!/usr/bin/env python3
"""NapCat 掉线看门狗：launchd 每 5 分钟跑一次（make watchdog-install 安装）。

- 要扫码（日志里出了二维码，之后没再收到消息）：弹 Mac 通知、打开二维码，不重启。
- 静默掉线：腾讯在服务端断了会话，NapCat 不报错、get_status 照样 online，只是再也收不到消息
  （NapCatQQ#2071）。白天 SILENT_MINUTES 分钟一条消息都没收到，或者日志里报了「账号状态变更为离线」，
  就重启 NapCat：会话还有效会快速登录回来，失效了会出二维码，下一轮通知扫码。
  过去几天白天这么久没消息的，只有真掉线的那一次。
- 重启也是一次登录，登录太勤本身招风控：两次重启至少隔 RESTART_GAP，隔得近就只通知。
- 夜里（QUIET_HOURS）群里本来就安静，不按没消息判掉线。
- 容器没在跑（make down 停的）不管；Docker 连不上（Colima 没起来）通知。

python3 scripts/napcat-watchdog.py --dry-run 只打印判断，不重启、不弹通知。
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "watchdog" / "state.json"
LOG = ROOT / "data" / "watchdog" / "watchdog.log"
CONTAINER = "napcat"
SILENT_MINUTES = 30
QUIET_HOURS = range(1, 9)  # 01:00–08:59
RESTART_GAP = 2 * 3600
RECENT = 6 * 60  # 每 5 分钟跑一次，往回看 6 分钟，二维码每 2 分钟刷新一次，不会漏
NOTIFY_GAP = {"qr": 600, "silent": 1800, "docker": 1800}  # 同一件事多久提醒一次
QR_FILE = "/tmp/napcat-qrcode.png"
RECEIVED = "接收 <-"
DRY = "--dry-run" in sys.argv


def log(text: str) -> None:
    line = f"{time.strftime('%m-%d %H:%M:%S')} {text}"
    if DRY:
        print(line)
        return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def logs(since: float) -> str:
    r = docker("logs", "--since", str(int(since)), CONTAINER)
    return r.stdout + r.stderr


def notify(state: dict, key: str, title: str, text: str, now: float) -> bool:
    """弹 Mac 通知；同一件事 NOTIFY_GAP 内只弹一次。返回这次弹了没有。"""
    sent = state.setdefault("notified", {})
    if now - sent.get(key, 0) < NOTIFY_GAP[key]:
        return False
    sent[key] = now
    log(f"通知：{title}，{text}")
    if not DRY:
        script = "display notification {} with title {} sound name \"Glass\"".format(
            json.dumps(text, ensure_ascii=False), json.dumps(title, ensure_ascii=False)
        )
        subprocess.run(["osascript", "-e", script], capture_output=True)
    return True


def started_at(value: str) -> float:
    """docker inspect 的 StartedAt（2026-10-29T13:15:56.123456789Z，UTC）转成时间戳。"""
    return datetime.fromisoformat(value[:19] + "+00:00").timestamp()


def check(state: dict, now: float) -> None:
    try:
        r = docker("inspect", "-f", "{{.State.Running}} {{.State.StartedAt}}", CONTAINER, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        r = None
    if r is None or r.returncode != 0:
        notify(state, "docker", "QQ 机器人", "连不上 Docker，Colima 可能没起来", now)
        return
    running, started = r.stdout.split()
    if running != "true":
        return
    started = started_at(started)
    # 只看这次启动以后的日志：docker restart 不清日志，重启前的二维码、离线不能再算一遍
    recent = logs(max(now - RECENT, started))
    qr = recent.rfind("二维码已保存")
    if qr >= 0 and recent.rfind(RECEIVED) < qr:
        if notify(state, "qr", "QQ 机器人掉线了", "要重新扫码，二维码已经打开", now) and not DRY:
            docker("cp", f"{CONTAINER}:/app/napcat/cache/qrcode.png", QR_FILE)
            subprocess.run(["open", QR_FILE])
        return
    offline = "账号状态变更为离线" in recent
    if not offline:
        if time.localtime(now).tm_hour in QUIET_HOURS or now - started < SILENT_MINUTES * 60:
            return
        if RECEIVED in logs(now - SILENT_MINUTES * 60):
            return
    why = "日志里报了离线" if offline else f"{SILENT_MINUTES} 分钟没收到消息"
    if now - state.get("restarted", 0) < RESTART_GAP:
        notify(state, "silent", "QQ 机器人可能掉线了", f"{why}，刚自动重启过，这次没重启，去看看", now)
        return
    log(f"{why}，重启 NapCat")
    if not DRY:
        docker("restart", CONTAINER, timeout=120)
    state["restarted"] = now
    notify(state, "silent", "QQ 机器人掉线了", f"{why}，已经自动重启 NapCat", now)


def main() -> None:
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    check(state, time.time())
    if not DRY:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
