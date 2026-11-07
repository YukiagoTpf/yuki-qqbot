import asyncio
import base64
import contextlib
import io
import json
import os
import re
import secrets
import time
from pathlib import Path

import aiohttp
from PIL import Image, ImageOps

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from . import engines

PLUGIN = "astrbot_plugin_xsearch"
SHORT = 12  # 超过这么多字的不算喊搜，多半是在聊别的
MAX_IMAGES = 2  # 一次最多送几张图
MAX_BYTES = 20 * 1024 * 1024
# 图通过 grok 的命令行参数传，太大会超出系统限制；SerpApi 传图最大 500 KB。依次试这几档，直到够小
SIZES = ((1280, 85), (1024, 80), (800, 75))
MAX_JPEG = 500 * 1024
LEADS = 12  # 最多给 grok 几条搜图引擎的线索
REACTION = "124"  # 收到就给那条消息贴个「OK」表情：要搜一两分钟，免得以为没反应，又不多发一条
# 发到群里的话都要短，免得 bot 号被风控
LINES = {
    "hit": "出处：{who}",
    "maybe": "不太确定，可能是 {who}",
    "miss": "X 上没搜到",
    "cooldown": "这小时搜过了，{minutes} 分钟后再来",
    "busy": "上一张还在搜，别急",
    "no_image": "回复一张图再喊我",
    "lost": "那条找不着了",
    "expired": "图下不下来，重新发一下",
    "error": "没搜成，等会再试",
}
# grok 写的回复超过这个字数（链接不算）、多于这么多行，或者带这些词，就不用，换成 render 的固定格式
REPLY_MAX, REPLY_LINES = 80, 5
BANNED = re.compile(r"烧鸡|骚|性感|色情|做爱|裸|露点|胸|乳|屁股|臀|腿|身材|内衣|丝袜|涩|黄图|约炮|成人|福利姬|onlyfans|fansly", re.I)
URL = re.compile(r"https?://[^\s<>\"'）)\]]+")
ADMINS = ("owner", "admin")  # 群主、群管理员，加上 AstrBot 的管理员，不受每小时一次的限制
ACCOUNT = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
POST = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")


def plain(segs: list) -> str:
    return "".join((s.get("data") or {}).get("text", "") for s in segs if s.get("type") == "text").strip()


def image_urls(segs: list) -> list[str]:
    # 收藏里发出来的图 QQ 常标成「动画表情」，也照样搜
    return [d["url"] for s in segs if s.get("type") == "image" and (d := s.get("data") or {}).get("url")]


def shrink(raw: bytes) -> bytes:
    """缩成 MAX_JPEG 以内的 JPEG。动图只取第一帧。"""
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        for side, quality in SIZES:
            im.thumbnail((side, side))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            if buf.tell() <= MAX_JPEG:
                break
    return buf.getvalue()


def leads(hits: list[engines.Hit]) -> list[str]:
    """搜图引擎找到的社交平台页面，给 grok 当线索：Google Lens 的完全匹配在前，其中 X 上的再往前。
    Google 的跳转地址还原不了，只给标题和平台；X 上的标出 @账号。"""
    out = []
    for h in sorted(hits, key=lambda h: (h.engine != "lens", not engines.on_x(h), not engines.x_handle(h))):
        if not engines.social(h):
            continue
        tag = f"Google Lens 完全匹配 · {h.source}" if h.engine == "lens" else "Yandex"
        who = f" @{handle}" if (handle := engines.x_handle(h)) else ""
        url = "" if engines.wrapped(h.url) else " " + h.url[:200]
        if (line := f"[{tag}]{who} {h.title[:80]}{url}") not in out:
            out.append(line)
    return out[:LEADS]


def render(result: dict) -> str:
    """只发账号和帖子链接，模型的其他话（凭什么认定的）只进日志。"""
    accounts, posts = [], []
    for a in result.get("accounts") or []:
        if (m := ACCOUNT.match(str(a).strip())) and m[1] not in accounts:
            accounts.append(m[1])
    for p in result.get("posts") or []:
        if (m := POST.match(str(p).strip())) and (url := f"https://x.com/{m[1]}/status/{m[2]}") not in posts:
            posts.append(url)
            if not accounts:
                accounts.append(m[1])
    if not result.get("found") or not accounts:
        return LINES["miss"]
    sure = result.get("confidence") in ("high", "medium")
    head = LINES["hit" if sure else "maybe"].format(who="、".join(f"@{a}" for a in accounts[:2]))
    return "\n".join([head, *posts[:2]])


def compose(result: dict) -> str:
    """发到群里的话：用 grok 写的 reply。没写、太长、带敏感词、带社交平台以外的链接，就用 render 的固定格式。"""
    text = str(result.get("reply") or "").strip()
    text = re.sub(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", r"\1 \2", text).replace("**", "").replace("`", "")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    words = URL.sub("", text)
    if (not words.strip() or len(words) > REPLY_MAX or text.count("\n") >= REPLY_LINES or BANNED.search(text)
            or not all(engines.social(engines.Hit("", u, "")) for u in URL.findall(text))):
        return render(result)
    if not URL.search(text):  # 找到了却没附链接，补上
        posts = [f"https://x.com/{m[1]}/status/{m[2]}" for p in result.get("posts") or [] if (m := POST.match(str(p).strip()))]
        text = "\n".join([text, *dict.fromkeys(posts[:2])])
    return text


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


class XSearchPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir(PLUGIN)
        last = self.data_dir / "last.json"
        self.last: dict[str, float] = json.loads(last.read_text("utf-8")) if last.exists() else {}  # 群号 -> 上次搜成的时间
        # 宿主机上的 grok 桥（scripts/grok-bridge.py）认这个令牌，它从同一个文件读
        token = self.data_dir / "bridge_token"
        if not token.exists():
            token.write_text(secrets.token_hex(16))
        self.token = token.read_text().strip()
        self.busy = False  # grok 一次只搜一张
        self.tasks: set[asyncio.Task] = set()
        self._session: aiohttp.ClientSession | None = None

    async def terminate(self):
        for task in self.tasks:
            task.cancel()
        if self._session:
            await self._session.close()

    # 优先级比猜题插件高：题目进行中「搜烧鸡」会被当成猜答案
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=5)
    async def on_group_message(self, event: AstrMessageEvent):
        """回复一张图（或者带着图）@bot 说「搜烧鸡」，就让 grok 去 X 上找出处。一个群每小时一次，管理员不限。"""
        gid = str(event.get_group_id() or "")
        if gid not in self._groups():
            return
        raw = event.message_obj.raw_message
        segs = raw.get("message") if isinstance(raw, dict) else None
        self_id = str(event.get_self_id())
        if not isinstance(segs, list) or str(event.get_sender_id()) == self_id:
            return
        text = re.sub(r"\s+", "", plain(segs))
        at_bot = any(s.get("type") == "at" and str((s.get("data") or {}).get("qq")) == self_id for s in segs)
        # 「搜 烧鸡」「搜一下烧鸡」「搜索一下烧鸡」也算：空格不管，触发词第一个字后面可以夹三个字
        trigger = str(self.config.get("trigger", "搜烧鸡"))
        loose = re.escape(trigger[:1]) + ".{0,3}" + re.escape(trigger[1:])
        if not at_bot or not re.search(loose, text) or len(text) > SHORT:
            return
        event.stop_event()
        origin, mid = event.unified_msg_origin, str(raw.get("message_id") or event.message_obj.message_id)
        who = f"{event.get_sender_name()}({event.get_sender_id()})"
        admin = str((raw.get("sender") or {}).get("role")) in ADMINS or event.is_admin()
        if self.busy:
            return await self._send(origin, mid, LINES["busy"])
        wait = self.last.get(gid, 0) + int(self.config.get("cooldown_minutes", 60)) * 60 - time.time()
        if wait > 0 and not admin:
            logger.info(f"[xsearch] {gid} {who} 喊搜，冷却中")
            return await self._send(origin, mid, LINES["cooldown"].format(minutes=max(1, round(wait / 60))))
        bot = getattr(event, "bot", None)
        urls = image_urls(segs)
        reply = next((str((s.get("data") or {}).get("id")) for s in segs if s.get("type") == "reply"), "")
        if not urls and reply and bot:
            try:
                res = await bot.call_action("get_msg", message_id=int(reply))
            except Exception as e:
                logger.warning(f"[xsearch] 拉消息 {reply} 失败：{e}")
                return await self._send(origin, mid, LINES["lost"])
            urls = image_urls((res or {}).get("message") or [])
        if not urls:
            return await self._send(origin, mid, LINES["no_image"])
        self.busy = True
        if bot:
            with contextlib.suppress(Exception):  # 贴不上表情不要紧
                await bot.call_action("set_msg_emoji_like", message_id=int(mid), emoji_id=REACTION)
        logger.info(f"[xsearch] {gid} {who}{'（管理员）' if admin else ''} 搜 {len(urls[:MAX_IMAGES])} 张图")
        # 管理员搜的不算次数，不占群友这小时的那一次
        task = asyncio.create_task(self._search(gid, origin, mid, urls[:MAX_IMAGES], None if admin else time.time()))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _search(self, gid: str, origin: str, mid: str, urls: list[str], asked: float | None) -> None:
        """放到后台跑，搜完引用那条喊搜的消息回话。先用搜图引擎搜第一张图（几秒）：Google Lens 在 X 上找到一模一样的
        就直接回；没有就把社交平台的结果当线索，连图一起交给 grok（一两分钟）。
        搜成了（不管找没找到）才算掉这小时的次数，从 asked（喊搜的时间）算起；asked 是 None（管理员搜的）不算。"""
        try:
            try:
                images = [await asyncio.to_thread(shrink, await self._download(u)) for u in urls]
            except Exception as e:
                logger.warning(f"[xsearch] 下载图片失败：{e}")
                return await self._send(origin, mid, LINES["expired"])
            hits = await self._lookup(images[0])
            if posts := [m for h in hits if h.engine == "lens" and (m := POST.match(h.url))]:
                accounts = list(dict.fromkeys(m[1] for m in posts))
                result = {"found": True, "accounts": accounts, "posts": [m[0] for m in posts], "confidence": "high",
                          "clue": "Google Lens 完全匹配"}
            else:
                try:
                    res = await self._ask([base64.b64encode(i).decode() for i in images], leads(hits))
                except Exception as e:
                    logger.warning(f"[xsearch] 调 grok 桥失败：{e!r}")
                    return await self._send(origin, mid, LINES["error"])
                if not res.get("ok"):
                    logger.warning(f"[xsearch] grok 桥报错：{res.get('error')}（详情见 data/grok-bridge/bridge.log）")
                    return await self._send(origin, mid, LINES["busy" if res.get("error") == "busy" else "error"])
                result = res.get("result") or {}
            logger.info(f"[xsearch] {gid} 结果：{json.dumps(result, ensure_ascii=False)}")
            if asked is not None:
                self.last[gid] = asked
                save_json(self.data_dir / "last.json", self.last)
            await self._send(origin, mid, compose(result))
        finally:
            self.busy = False

    async def _download(self, url: str) -> bytes:
        chunks, size = [], 0
        async with self._http().get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            async for chunk in resp.content.iter_chunked(1 << 16):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("图片太大")
                chunks.append(chunk)
        return b"".join(chunks)

    async def _lookup(self, jpeg: bytes) -> list[engines.Hit]:
        """Google Lens（配了 key 才用）和 Yandex 一起搜，出错的跳过。"""
        jobs = {}
        if key := str(self.config.get("serpapi_key", "")).strip():
            jobs["Google Lens"] = engines.lens(self._http(), key, jpeg)
        if self.config.get("yandex", True):
            jobs["Yandex"] = engines.yandex(self._http(), jpeg)
        hits = []
        for name, res in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
            if isinstance(res, BaseException):
                logger.warning(f"[xsearch] {name} 失败：{type(res).__name__} {res}")
                continue
            logger.info(f"[xsearch] {name} 找到 {len(res)} 个页面，社交平台的 {sum(engines.social(h) for h in res)} 个")
            hits += res
        return hits

    async def _ask(self, images: list[str], leads: list[str]) -> dict:
        url = str(self.config.get("bridge_url", "http://192.168.5.2:6190")).rstrip("/") + "/search"
        body = {"images": [{"mime": "image/jpeg", "data": d} for d in images], "leads": leads}
        timeout = aiohttp.ClientTimeout(total=int(self.config.get("timeout_seconds", 240)))
        async with self._http().post(url, json=body, headers={"X-Token": self.token}, timeout=timeout) as resp:
            return await resp.json(content_type=None)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _groups(self) -> set[str]:
        return {str(g).strip() for g in self.config.get("groups", []) if str(g).strip()}

    async def _send(self, origin: str, reply_to: str, text: str) -> None:
        try:
            await self.context.send_message(origin, MessageChain([Comp.Reply(id=reply_to), Comp.Plain(text)]))
        except Exception as e:
            logger.warning(f"[xsearch] 发消息失败：{e}")
