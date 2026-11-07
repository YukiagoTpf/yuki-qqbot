"""群消息：缓冲里存的样子，和渲染成给模型看的一行字。"""

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field

STICKER_SUBTYPE = 1  # QQ 标了「动画表情」的图，不送去看
MAX_LINE = 300  # 一条消息最多给模型看多少字
FORWARD_LINES = 15  # 合并转发最多展开几条
FORWARD_LINE = 60


@dataclass(eq=False)  # 按对象认消息，deque.index 不逐字段比
class Msg:
    mid: str
    uid: str
    name: str  # 群名片或昵称，只用于日志
    role: str  # owner / admin / member
    t: float
    segs: list  # OneBot 消息段（array 格式），合并转发展开后会补上 content
    ready: asyncio.Task | None = None  # 看图、拉合并转发，判之前要等它
    captions: dict = field(default_factory=dict)  # 图片 key -> 看图的结果
    judged: bool = False
    call: bool = False  # 是不是在喊「杀」、申诉，这些消息本身不判
    roast: str = ""  # bot 自己发的杀炫区通知里的嘲讽，认复读用
    about: list = field(default_factory=list)  # bot 自己发的话说的是哪几条消息（id），有人引用它申诉时审这几条
    tries: int = 0  # 攒批判了几次（模型出错会重判一次）


def quote_id(segs: list) -> str:
    """引用回复的那条消息的 id，没引用返回空串。"""
    return next((str((s.get("data") or {}).get("id")) for s in segs if s.get("type") == "reply"), "")


def plain(segs: list) -> str:
    return "".join((s.get("data") or {}).get("text", "") for s in segs if s.get("type") == "text").strip()


def image_key(data: dict) -> str:
    return str(data.get("file") or data.get("url") or "")


def iter_images(segs: list) -> Iterator[dict]:
    """要看的图：消息里的和合并转发里的（只展开一层），跳过动画表情。"""
    for seg in segs:
        kind, data = seg.get("type"), seg.get("data") or {}
        if kind == "image" and data.get("url") and int(data.get("sub_type") or 0) != STICKER_SUBTYPE:
            yield data
        elif kind == "forward":
            for item in data.get("content") or []:
                for inner in item.get("message") or []:
                    d = inner.get("data") or {}
                    if inner.get("type") == "image" and d.get("url") and int(d.get("sub_type") or 0) != STICKER_SUBTYPE:
                        yield d


class Codes:
    """QQ 号 -> A、B、C……，按第一次出现的顺序；给模型看代号，不看 QQ 号和群名片。"""

    def __init__(self, self_id: str):
        self.self_id = str(self_id)
        self.map: dict[str, str] = {}

    def __call__(self, uid: str) -> str:
        uid = str(uid)
        if uid == self.self_id:
            return "机器人"
        if uid not in self.map:
            i = len(self.map)
            self.map[uid] = chr(ord("A") + i % 26) + (str(i // 26) if i >= 26 else "")
        return self.map[uid]


class Renderer:
    """把一批消息渲染成「#序号 代号：内容」。numbers 是这一批里消息 id -> 序号，
    lookup 是缓冲里所有消息（引用回复的那条不在这一批里时用）。"""

    def __init__(self, codes: Codes, numbers: dict[str, int], lookup: dict[str, Msg]):
        self.codes, self.numbers, self.lookup = codes, numbers, lookup

    def line(self, n: int, msg: Msg) -> str:
        return f"#{n} {self.codes(msg.uid)}：{self.content(msg) or '（空）'}"

    def content(self, msg: Msg, *, quoting: bool = False) -> str:
        out = []
        for seg in msg.segs:
            kind, data = seg.get("type"), seg.get("data") or {}
            if kind == "text":
                out.append(data.get("text", ""))
            elif kind == "at":
                qq = str(data.get("qq"))
                out.append(" @全体成员 " if qq == "all" else f" @{self.codes(qq)} ")
            elif kind == "reply" and not quoting:
                out.append(f"〔回复 {self._quote(str(data.get('id')))}〕")
            elif kind == "image":
                out.append(self._image(data, msg.captions))
            elif kind in ("face", "mface"):
                face = (data.get("raw") or {}).get("faceText") or data.get("summary") or ""
                out.append(f"[表情{face}]")
            elif kind == "forward":
                out.append("" if quoting else self._forward(data, msg.captions))
            elif kind == "json":
                out.append(self._card(data))
            elif kind == "video":
                out.append("[视频]")
            elif kind == "record":
                out.append("[语音]")
            elif kind == "file":
                out.append("[文件]")
        text = "".join(out).strip()
        return text[:MAX_LINE] + "……" if len(text) > MAX_LINE else text

    def _quote(self, mid: str) -> str:
        if mid in self.numbers:
            return f"#{self.numbers[mid]}"
        if target := self.lookup.get(mid):
            return f"{self.codes(target.uid)} 的「{self.content(target, quoting=True)[:30]}」"
        return "一条更早的消息"

    @staticmethod
    def _image(data: dict, captions: dict) -> str:
        if int(data.get("sub_type") or 0) == STICKER_SUBTYPE:
            return "[图：动画表情]"
        return f"[图：{captions[image_key(data)]}]" if captions.get(image_key(data)) else "[图]"

    def _forward(self, data: dict, captions: dict) -> str:
        items = data.get("content") or []
        if not items:
            return "[合并转发]"
        lines = []
        for item in items[:FORWARD_LINES]:
            sender = item.get("sender") or {}
            name = str(sender.get("card") or sender.get("nickname") or "?")[:12]
            body = []
            for inner in item.get("message") or []:
                kind, d = inner.get("type"), inner.get("data") or {}
                if kind == "text":
                    body.append(d.get("text", ""))
                elif kind == "image":
                    body.append(self._image(d, captions))
                elif kind in ("face", "mface"):
                    body.append("[表情]")
            text = "".join(body).strip().replace("\n", " ")
            lines.append(f"{name}：{text[:FORWARD_LINE]}")
        more = f"（共 {len(items)} 条）" if len(items) > FORWARD_LINES else ""
        return "[合并转发：" + " / ".join(lines) + more + "]"

    @staticmethod
    def _card(data: dict) -> str:
        try:
            card = json.loads(data.get("data") or "{}")
        except (TypeError, json.JSONDecodeError):
            return "[卡片]"
        return f"[卡片：{card.get('prompt') or card.get('app') or ''}]"
