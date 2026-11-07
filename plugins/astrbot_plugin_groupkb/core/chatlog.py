"""群聊落盘和渲染。每个群每天一个 jsonl：插件数据目录的 chat/<群号>/<YYYY-MM-DD>.jsonl，一行一条消息
{"t": 时间戳, "mid": 消息 id, "uid": QQ 号, "name": 群名片, "segs": 精简过的消息段}。只在本机，不进仓库。"""

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_LINE = 150  # 一条消息最多给模型看多少字
CODE = re.compile(r"(?<![A-Za-z0-9])P\d+(?!\d)")
QUOTE = 20  # 引用回复带原文的前几个字


def slim(segs: list) -> list:
    """只留看得懂聊天要用的：文字、@、引用、表情的字面；图片只留是不是动画表情，链接会过期，不存。"""
    out = []
    for seg in segs or []:
        kind, data = seg.get("type"), seg.get("data") or {}
        if kind == "text":
            out.append({"type": "text", "data": {"text": data.get("text", "")}})
        elif kind in ("at", "reply"):
            key = "qq" if kind == "at" else "id"
            out.append({"type": kind, "data": {key: str(data.get(key))}})
        elif kind == "image":
            out.append({"type": "image", "data": {"sub_type": data.get("sub_type"), "summary": data.get("summary", "")}})
        elif kind in ("face", "mface"):
            text = (data.get("raw") or {}).get("faceText") or data.get("summary") or ""
            out.append({"type": "face", "data": {"text": text}})
        elif kind == "json":
            try:
                prompt = json.loads(data.get("data") or "{}").get("prompt", "")
            except (TypeError, ValueError):
                prompt = ""
            out.append({"type": "json", "data": {"prompt": prompt}})
        elif kind:
            out.append({"type": kind, "data": {}})
    return out


def row(t: float, mid, uid, name: str, segs: list) -> dict:
    return {"t": int(t), "mid": str(mid), "uid": str(uid), "name": name, "segs": slim(segs)}


def path(root: Path, gid: str, day: date) -> Path:
    return root / str(gid) / f"{day.isoformat()}.jsonl"


def append(root: Path, gid: str, item: dict) -> None:
    p = path(root, gid, datetime.fromtimestamp(item["t"]).date())
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load(root: Path, gid: str, day: date) -> list[dict]:
    p = path(root, gid, day)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text("utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def merge(*sources: list[dict]) -> list[dict]:
    """几份记录按消息 id 去重、按时间排（落盘的加上 NapCat 历史里补的）。"""
    seen = {}
    for rows in sources:
        for r in rows:
            seen.setdefault(r["mid"], r)
    return sorted(seen.values(), key=lambda r: (r["t"], r["mid"]))


def prune(root: Path, keep_days: int) -> None:
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    for p in root.glob("*/*.jsonl"):
        if p.stem < cutoff:
            p.unlink()


def day_range(day: date) -> tuple[float, float]:
    start = datetime.combine(day, datetime.min.time()).timestamp()
    return start, start + 86400


class Render:
    """把一天的记录渲染成「HH:MM 代号：内容」。代号 P1、P2……按第一次出现排，bot 自己是「机器人」；
    给模型看群名片（认外号要靠它），不给 QQ 号。roster 是代号 -> QQ 号，模型说的代号要换回 QQ 号存。"""

    def __init__(self, rows: list[dict], self_id: str):
        self.rows, self.self_id = rows, str(self_id)
        self.codes: dict[str, str] = {}
        self.names: dict[str, str] = {}
        for r in rows:
            self.names[r["uid"]] = r.get("name") or self.names.get(r["uid"], "")
        self.by_mid = {r["mid"]: r for r in rows}
        self.lines = [self._line(r) for r in rows]

    def code(self, uid: str) -> str:
        uid = str(uid)
        if uid == self.self_id:
            return "机器人"
        if uid not in self.codes:
            self.codes[uid] = f"P{len(self.codes) + 1}"
        return self.codes[uid]

    @property
    def roster(self) -> dict[str, str]:
        return {c: uid for uid, c in self.codes.items()}

    def decode(self, text: str) -> str:
        """模型写的黑话、留档里漏了代号的，换回群名片：代号只在当天这份记录里有意义。"""
        roster = self.roster
        return CODE.sub(lambda m: self.names.get(roster.get(m.group(), ""), "") or m.group(), str(text))

    def roster_lines(self) -> list[str]:
        return [f"{c}：群名片「{self.names.get(uid) or '（今天没说话）'}」" for c, uid in self.roster.items()]

    def _line(self, r: dict) -> str:
        who = self.code(r["uid"])
        text = self.content(r["segs"])
        text = text[:MAX_LINE] + "……" if len(text) > MAX_LINE else text
        return f"{time.strftime('%H:%M', time.localtime(r['t']))} {who}：{text or '（空）'}"

    def content(self, segs: list, quoting: bool = False) -> str:
        out = []
        for seg in segs:
            kind, data = seg.get("type"), seg.get("data") or {}
            if kind == "text":
                out.append(data.get("text", ""))
            elif kind == "at":
                qq = str(data.get("qq"))
                out.append(" @全体成员 " if qq == "all" else f" @{self.code(qq)} ")
            elif kind == "reply" and not quoting:
                src = self.by_mid.get(str(data.get("id")))
                out.append(f"〔回复 {self.code(src['uid'])}「{self.content(src['segs'], True)[:QUOTE]}」〕" if src else "〔回复〕")
            elif kind == "image":
                out.append("[动画表情]" if int(data.get("sub_type") or 0) == 1 else "[图]")
            elif kind == "face":
                out.append(f"[表情{data.get('text', '')}]")
            elif kind == "json":
                out.append(f"[卡片：{data.get('prompt', '')}]")
            elif kind == "forward":
                out.append("[合并转发]")
            elif kind == "record":
                out.append("[语音]")
            elif kind == "video":
                out.append("[视频]")
        return "".join(out).replace("\n", " ").strip()
