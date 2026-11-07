"""看图：下载群里的图，缩小后让模型说这是什么图。同一张图（按 NapCat 给的 file）只看一次。"""

import asyncio
import base64
import io
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import aiohttp
from PIL import Image, ImageOps

from .prompts import VISION_CHECK, VISION_PROMPT, parse_json
from .render import image_key

MAX_BYTES = 20 * 1024 * 1024
# 送去看的图最大宽高：竖长的手机截图要留够高度，不然聊天记录的字看不清
MAX_W, MAX_H = 896, 1792
CACHE = 1000
CONCURRENCY = 4


class Vision:
    def __init__(self, ask: Callable[[str, str], Awaitable[str]]):
        """ask(prompt, 图片 base64://) -> 模型回复的文字。"""
        self.ask = ask
        self.done: OrderedDict[str, str] = OrderedDict()
        self.running: dict[str, asyncio.Task] = {}
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self._session: aiohttp.ClientSession | None = None

    async def describe(self, data: dict) -> str:
        key = image_key(data)
        if key in self.done:
            self.done.move_to_end(key)
            return self.done[key]
        task = self.running.get(key)
        if task is None:
            task = self.running[key] = asyncio.create_task(self._describe(data["url"]))
        try:
            caption = await task
        finally:
            self.running.pop(key, None)
        self.done[key] = caption
        while len(self.done) > CACHE:
            self.done.popitem(last=False)
        return caption

    async def check(self, data: dict, claim: str) -> str:
        """带着群友的话再看一遍（不缓存）：图里有没有能证实、推翻它的东西，比如「看对面手臂的影子」。
        判的模型只看得到第一次看图的那句描述，群友指出的细节它核实不了。"""
        async with self.sem:
            image = shrink(await self._download(data["url"]))
            if image is None:
                return ""
            reply = await self.ask(VISION_CHECK.format(claim=claim[:200]), image)
        return str((parse_json(reply) or {}).get("seen") or "").strip()[:150]

    async def _describe(self, url: str) -> str:
        async with self.sem:
            raw = await self._download(url)
            image = shrink(raw)
            if image is None:
                return "动图"
            reply = await self.ask(VISION_PROMPT, image)
        data = parse_json(reply) or {}
        kind, desc = str(data.get("kind") or "").strip(), str(data.get("desc") or "").strip()
        if not kind or kind == "无":
            return ""
        return f"{kind}，{desc}" if desc else kind

    async def _download(self, url: str) -> bytes:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        chunks, size = [], 0
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            async for chunk in resp.content.iter_chunked(1 << 16):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("图片太大")
                chunks.append(chunk)
        return b"".join(chunks)

    async def close(self) -> None:
        for task in self.running.values():
            task.cancel()
        if self._session:
            await self._session.close()


def shrink(raw: bytes) -> str | None:
    """缩到 MAX_W x MAX_H 以内，转成 JPEG 的 base64://。动图不看（基本都是表情包），返回 None。"""
    with Image.open(io.BytesIO(raw)) as img:
        if getattr(img, "is_animated", False):
            return None
        img = ImageOps.exif_transpose(img).convert("RGB")
    scale = min(1.0, MAX_W / img.width, MAX_H / img.height)
    if scale < 1:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "base64://" + base64.b64encode(buf.getvalue()).decode()
