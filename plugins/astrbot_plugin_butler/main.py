import asyncio
import contextlib
import re
import secrets
import time

import aiohttp
from aiocqhttp import Event

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

PLUGIN = "astrbot_plugin_butler"
# 「女仆 …」「女仆：…」「让女仆…」「委派 …」「委托一下 …」都算；以前叫「管家」，也还认
TRIGGER = re.compile(r"^\s*(?:(?:让|叫|请|找)\s*)?(?:女仆|管家|委派|委托)\s*(?:一下|下)?\s*[:：，,、]?\s*")
# 后面整句只是这些：关掉女仆重开 / 问女仆在不在忙
RESET = re.compile(r"^(?:重开|重启|重置|重来|新对话|新开|清空|清一下|忘掉|换一个|换个)\s*(?:吧|一下|下)?\s*[!！。.]?$")
STATUS = re.compile(r"^(?:状态|在吗|在不在|在干嘛|在干啥|在干什么|干到哪了|进度|忙吗|忙不忙)?\s*[?？]?$")
REACTION = "124"  # 交给女仆了就贴个「OK」表情，女仆自己回话
MAX_TEXT, MAX_QUOTE = 2000, 3000
INJECTED = "_butler_injected"  # 女仆替主人说的话带这个标记，女仆插件自己不接，免得绕回去
LINES = {
    "denied": "女仆只听主人的",
    "error": "没接上，等会再试（详情见 data/butler-bridge/bridge.log）",
    "reset": "重开了，前面说的都忘了",
    "reset_none": "本来就没开，下次叫我再起",
    "idle": "在，闲着",
    "busy": "在忙，说的话先排着",
    "blocked": "卡在一个确认框上了，主人上 Mac 看看",
    "off": "还没开，下次叫我再起",
}


def render(segs: list) -> str:
    """消息段 -> 文字。@ 写成 @QQ号（女仆可能要知道说的是谁），图和语音留个记号。"""
    out = []
    for s in segs:
        kind, data = s.get("type"), s.get("data") or {}
        if kind == "text":
            out.append(data.get("text", ""))
        elif kind == "at":
            out.append(f"@{data.get('qq')} ")
        elif kind == "image":
            out.append("[图片]")
        elif kind == "record":
            out.append("[语音]")
        elif kind == "forward":
            out.append("[聊天记录]")
        elif kind == "file":
            out.append(f"[文件 {data.get('file') or data.get('name') or ''}]")
    return "".join(out).strip()


class ButlerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir(PLUGIN)
        # 宿主机上的女仆桥（scripts/butler-bridge.py）认这个令牌，它从同一个文件读
        token = self.data_dir / "bridge_token"
        if not token.exists():
            token.write_text(secrets.token_hex(16))
        self.token = token.read_text().strip()
        self.tasks: set[asyncio.Task] = set()
        self._session: aiohttp.ClientSession | None = None
        self._pull_ok = True  # 上一次连桥成没成，只在变化时记日志
        self._puller = asyncio.get_event_loop().create_task(self._pull_loop())
        self.tasks.add(self._puller)

    async def terminate(self):
        for task in self.tasks:
            task.cancel()
        if self._session:
            await self._session.close()

    # 优先级比猜题插件高：题目进行中「女仆 …」会被当成猜答案
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=6)
    async def on_group_message(self, event: AstrMessageEvent):
        """bot 管理员 @bot 说「女仆 …」：交给本机 herdr 里用 DeepSeek 跑的女仆（Claude Code）去办，它办完自己回到这个群。
        「女仆 重开」关掉重来，「女仆」「女仆 状态」问它在不在忙。引用一条消息再说，那条的内容一起交过去。"""
        gid = str(event.get_group_id() or "")
        raw = event.message_obj.raw_message
        segs = raw.get("message") if isinstance(raw, dict) else None
        self_id, uid = str(event.get_self_id()), str(event.get_sender_id())
        if not gid or not isinstance(segs, list) or uid == self_id or raw.get(INJECTED):
            return
        if not any(s.get("type") == "at" and str((s.get("data") or {}).get("qq")) == self_id for s in segs):
            return
        text = render([s for s in segs if not (s.get("type") == "at" and str((s.get("data") or {}).get("qq")) == self_id)])
        if not (m := TRIGGER.match(text)):
            return
        event.stop_event()
        origin, mid = event.unified_msg_origin, str(raw.get("message_id") or event.message_obj.message_id)
        if not event.is_admin():
            logger.info(f"[butler] {gid} 非管理员 {uid} 叫女仆，没理")
            return await self._send(origin, mid, LINES["denied"])
        text = text[m.end():].strip()[:MAX_TEXT]
        task = asyncio.create_task(self._handle(event, origin, mid, gid, uid, text, segs))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _handle(self, event: AstrMessageEvent, origin: str, mid: str, gid: str, uid: str, text: str,
                      segs: list) -> None:
        try:
            if RESET.match(text):
                res = await self._bridge("/reset")
                line = LINES["reset" if res.get("closed") else "reset_none"] if res.get("ok") else LINES["error"]
                logger.info(f"[butler] {gid} 管理员 {uid} 让女仆重开：{res}")
                return await self._send(origin, mid, line)
            if STATUS.match(text):
                res = await self._bridge("/status")
                if not res.get("ok"):
                    line = LINES["error"]
                elif not res.get("alive"):
                    line = LINES["off"]
                else:
                    line = LINES.get({"working": "busy"}.get(res.get("status"), res.get("status")), LINES["idle"])
                return await self._send(origin, mid, line)
            bot = getattr(event, "bot", None)
            quote = await self._quote(bot, segs) if bot else ""
            group_name = ""
            if bot:
                with contextlib.suppress(Exception):  # 拿不到群名就只给群号
                    group_name = str((await bot.call_action("get_group_info", group_id=int(gid)) or {}).get("group_name") or "")
            body = {"group": gid, "group_name": group_name, "admin": uid, "name": event.get_sender_name(), "msg_id": mid,
                    "text": text, "quote": quote}
            logger.info(f"[butler] {gid} 管理员 {event.get_sender_name()}({uid}) 交给女仆：{text[:100]}")
            res = await self._bridge("/task", body, timeout=240)
            if not res.get("ok"):
                logger.warning(f"[butler] 女仆桥报错：{res.get('error')}")
                return await self._send(origin, mid, LINES["error"])
            if bot:
                with contextlib.suppress(Exception):  # 贴不上表情不要紧
                    await bot.call_action("set_msg_emoji_like", message_id=int(mid), emoji_id=REACTION)
        except Exception as e:
            logger.warning(f"[butler] 连不上女仆桥：{e!r}")
            await self._send(origin, mid, LINES["error"])

    async def _pull_loop(self) -> None:
        """女仆替主人说的话（scripts/bot-do.py）：一直向桥要，拿到就派发，再回报结果。宿主机连不进容器，所以这边去取。"""
        while True:
            try:
                res = await self._bridge("/pull", timeout=40)
                if not self._pull_ok:
                    logger.info("[butler] 又连上女仆桥了")
                self._pull_ok = True
                for item in res.get("items") or []:
                    try:
                        error = await self._inject(item)
                    except Exception as e:
                        logger.warning(f"[butler] 替主人说话失败：{e!r}")
                        error = "failed"
                    await self._bridge("/ack", {"id": item.get("id"), "ok": not error, "error": error})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._pull_ok:
                    logger.warning(f"[butler] 连不上女仆桥，女仆没法替主人说话：{e!r}（之后每 10 秒重试，连上了再说）")
                self._pull_ok = False
                await asyncio.sleep(10)

    async def _inject(self, item: dict) -> str:
        """把女仆要替主人说的话构造成主人在群里发的一条消息，交给 QQ 适配器，像收到真消息一样走一遍 AstrBot 的流程：
        白名单、管理员身份、各插件的指令照常生效。出错返回原因，成功返回空。"""
        gid, admin, text = int(item["group"]), str(item["admin"]), str(item["text"])
        admins = {str(a) for a in self.context.get_config().get("admins_id", [])}
        if admin not in admins:
            return "not_admin"
        platform = next((p for p in self.context.platform_manager.platform_insts if p.meta().name == "aiocqhttp"), None)
        if platform is None:
            return "no_platform"
        bot = platform.get_client()
        self_id = int((await bot.call_action("get_login_info"))["user_id"])
        try:
            info = await bot.call_action("get_group_member_info", group_id=gid, user_id=int(admin)) or {}
        except Exception:
            return "not_in_group"
        segs = [{"type": "text", "data": {"text": text}}]
        raw_text = text
        if item.get("at", True):
            segs = [{"type": "at", "data": {"qq": str(self_id)}}, {"type": "text", "data": {"text": " " + text}}]
            raw_text = f"[CQ:at,qq={self_id}] {text}"
        payload = {
            "time": int(time.time()), "self_id": self_id, "post_type": "message", "message_type": "group",
            "sub_type": "normal", "message_id": int(item.get("reply_id") or 0), "group_id": gid, "user_id": int(admin),
            "message": segs, "raw_message": raw_text, "font": 0,
            "sender": {"user_id": int(admin), "nickname": info.get("nickname") or "", "card": info.get("card") or "",
                       "role": info.get("role") or "member"},
            INJECTED: True,
        }
        abm = await platform.convert_message(Event.from_payload(payload))
        if abm is None:
            return "convert_failed"
        await platform.handle_msg(abm)
        logger.info(f"[butler] 女仆替主人在 {gid} 说：{text[:100]}")
        return ""

    @staticmethod
    async def _quote(bot, segs: list) -> str:
        """引用的那条消息的内容，没引用返回空。"""
        reply = next(((s.get("data") or {}).get("id") for s in segs if s.get("type") == "reply"), None)
        if not reply:
            return ""
        try:
            res = await bot.call_action("get_msg", message_id=int(reply)) or {}
        except Exception as e:
            logger.warning(f"[butler] 拉引用的消息 {reply} 失败：{e}")
            return ""
        sender = res.get("sender") or {}
        who = f"{sender.get('card') or sender.get('nickname') or ''}({sender.get('user_id') or ''})"
        return f"{who}：{render(res.get('message') or [])}"[:MAX_QUOTE]

    async def _bridge(self, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        url = str(self.config.get("bridge_url", "http://192.168.5.2:6193")).rstrip("/") + path
        async with self._http().post(url, json=body or {}, headers={"X-Token": self.token},
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return await resp.json(content_type=None)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send(self, origin: str, reply_to: str, text: str) -> None:
        try:
            await self.context.send_message(origin, MessageChain([Comp.Reply(id=reply_to), Comp.Plain(text)]))
        except Exception as e:
            logger.warning(f"[butler] 发消息失败：{e}")
