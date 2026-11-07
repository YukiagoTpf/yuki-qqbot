"""以图搜图：Google Lens（经 SerpApi，要 key）和 Yandex（直接请求网页），几秒出结果。
Google Lens 在 X 上找到一模一样的图就直接回；其余的筛出社交平台的页面，给 grok 当线索。

没用 PicImageSearch 库：装它会把容器里的 lxml 从 6.x 降到 5.x，可能弄坏 AstrBot 本体。"""

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp

# 线索只要这些平台的页面。Yandex 对真人照片找到的多是色情站、泄露合集和长得像的别人，这些不给 grok、更不发群
SOCIAL = (
    "x.com", "twitter.com", "pixiv.net", "weibo.com", "weibo.cn", "instagram.com", "xiaohongshu.com",
    "bilibili.com", "youtube.com", "tiktok.com", "douyin.com", "threads.net", "threads.com", "bsky.app",
    "facebook.com", "lofter.com", "fanbox.cc", "misskey.io",
)
# Google Lens 的结果大多是 google.com/goto 跳转地址（还原不了），平台要看 source 字段
SOCIAL_SOURCES = {
    "x", "twitter", "pixiv", "weibo", "新浪微博", "微博", "instagram", "小红书", "xiaohongshu", "rednote",
    "bilibili", "哔哩哔哩", "youtube", "tiktok", "抖音", "douyin", "threads", "bluesky", "facebook", "lofter", "fanbox",
}
HANDLE = re.compile(r"\(@([A-Za-z0-9_]{1,15})\)")  # X 页面的标题「NASA (@NASA) on X」
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
TIMEOUT = aiohttp.ClientTimeout(total=40)
YANDEX_ROOT = re.compile(r'<div[^>]*\bid="ImagesApp-[^"]*"[^>]*>')
NO_RESULTS = "hasn't returned any results"  # SerpApi 没结果时放在 error 里，不算出错


@dataclass
class Hit:
    engine: str  # lens / yandex
    url: str
    title: str
    source: str = ""  # Google Lens 给的平台名，如「X」「Facebook」


def wrapped(url: str) -> bool:
    return urlparse(url).path == "/goto" and (urlparse(url).hostname or "").endswith("google.com")


def social(hit: Hit) -> bool:
    host = (urlparse(hit.url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in SOCIAL) or hit.source.strip().lower() in SOCIAL_SOURCES


def on_x(hit: Hit) -> bool:
    return hit.source.strip().lower() in ("x", "twitter") or bool(re.search(r"//(?:www\.|mobile\.)?(?:x|twitter)\.com/", hit.url))


def x_handle(hit: Hit) -> str:
    """X 上的结果是谁发的：地址里的或者标题里的 @账号。"""
    if on_x(hit):
        if m := re.search(r"//(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/", hit.url):
            return m[1]
        if m := HANDLE.search(hit.title):
            return m[1]
    return ""


def clean(url: str) -> str:
    """去掉 Yandex 加的 utm_* 参数。"""
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunparse(p._replace(query=urlencode(query)))


async def lens(session: aiohttp.ClientSession, key: str, jpeg: bytes) -> list[Hit]:
    """先传图拿 image_id（10 分钟有效），再搜「完全匹配」。成功的搜索才扣 SerpApi 的次数。
    出错只报类型，aiohttp 的异常信息里带着含 key 的地址，不能进日志。"""
    try:
        form = aiohttp.FormData()
        form.add_field("api_key", key)
        form.add_field("image", jpeg, filename="image.jpg", content_type="image/jpeg")
        async with session.post("https://serpapi.com/image", data=form, timeout=TIMEOUT) as resp:
            up = await resp.json(content_type=None)
        if not up.get("image_id"):
            raise RuntimeError(f"传图失败：{up.get('error') or resp.status}")
        params = {"engine": "google_lens", "type": "exact_matches", "image_id": up["image_id"], "api_key": key}
        async with session.get("https://serpapi.com/search", params=params, timeout=TIMEOUT) as resp:
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as e:
        raise RuntimeError(type(e).__name__) from None
    error = str(data.get("error") or "")
    if error and NO_RESULTS not in error:
        raise RuntimeError(error)
    return lens_hits(data)


def lens_hits(data: dict) -> list[Hit]:
    items = data.get("exact_matches") or []
    return [Hit("lens", str(i["link"]), str(i.get("title") or ""), str(i.get("source") or "")) for i in items if i.get("link")]


async def yandex(session: aiohttp.ClientSession, jpeg: bytes) -> list[Hit]:
    """Yandex 的「含这张图的网站」。实际也混着长得像的图，只能当线索。"""
    form = aiohttp.FormData()
    form.add_field("prg", "1")
    form.add_field("upfile", jpeg, filename="image.jpg", content_type="image/jpeg")
    params = {"rpt": "imageview", "cbir_page": "sites"}
    async with session.post("https://yandex.com/images/search", params=params, data=form,
                            headers={"User-Agent": UA}, timeout=TIMEOUT) as resp:
        text = await resp.text()
    root = YANDEX_ROOT.search(text)
    state = root and re.search(r'data-state="([^"]*)"', root[0])
    if not state:
        raise RuntimeError("页面里没有结果，可能要验证码")
    sites = (json.loads(html.unescape(state[1])).get("initialState") or {}).get("cbirSites", {}).get("sites") or []
    return [Hit("yandex", clean(str(s["url"])), str(s.get("title") or ""), str(s.get("domain") or "")) for s in sites if s.get("url")]
