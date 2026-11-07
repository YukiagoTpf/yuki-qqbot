import asyncio
import json
import random
import re
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.groups import Groups
from .core.prompts import JUDGE_SYSTEM, parse_json
from .core.render import STICKER_SUBTYPE, Codes, Msg, Renderer, image_key, iter_images, plain, quote_id
from .core.store import load_json, save_json
from .core.vision import Vision

TICK = 2  # 每隔几秒看一次各群：有没有人喊杀、攒够没有
BUFFER = 300  # 每个群在内存里留多少条，引用回复找原文也靠它
CONTEXT = 15  # 每批带多少条前文
AFTER = 20  # 每批最多带多少条后文（看群友反应）
MAX_TARGETS = 30  # 一批最多判几条，积压多了分几批
READY_TIMEOUT = 90  # 判之前最多等看图多久，等不到就按没看到图判
# 喊杀：短句里带「杀」「🔪」「炫蛆」「炫区」，比如「杀」「杀吗」「这不杀啊」「不杀炫区？」「炫蛆现在不杀了？」，
# 但不是「自杀」「被杀」「杀我」这种
CALL = re.compile(r"[杀鲨🔪]|炫蛆|炫区|炫狗")
NOT_CALL = re.compile(r"自杀|秒杀|杀疯|误杀|被杀|杀我|追杀|暗杀|击杀|绞杀|杀毒|杀价|杀青|抹杀|杀手|杀伤")
CALL_LEN = 10
# @bot 时宽一点，说的话里带这些字就算喊杀
AT_CALL = re.compile(r"[杀鲨🔪炫]")
# 叫管理员来处理：「绿帽呢」「管理呢？」「管理员在哪」「群主出来管管」（绿帽 = 管理员，QQ 里管理员是绿色标识）
SUMMON = re.compile(r"(?:绿帽|管理员?|群主)们?\s*(?:呢|在哪|在吗|人呢|去哪|哪去|出来|出动|管管|管一下|管一管|快来|干活|杀|死哪)")
SHORT = 20  # 超过这么多字的不算叫管理、@bot 喊杀，多半是在聊别的
URGENT_GAP = 20  # 一个群两次立刻审至少隔几秒，期间再喊的合并成一次
ECHO_LOOKBACK = 50  # 往前看多少条认复读
# 管理员 @bot 关、开杀炫区。关的说法要带「杀」「炫」「功能」，光说「暂停」是猜题插件的指令；
# 带问号的不算（「不杀炫区？」是在喊杀）。开只在关着的时候认，平时 @bot 说「杀炫区」是喊杀
PAUSE = re.compile(
    r"暂停.{0,4}(?:杀|炫|功能)|暂时不杀|先别杀|(?:别|不)杀(?:炫区)?了|先不杀|(?:关闭|关掉|停止|停掉|休息).{0,4}(?:杀|炫|功能)"
    r"|(?:杀炫区|这个功能).{0,3}(?:暂停|关了|停了|休息)"
)
RESUME = re.compile(r"杀炫区|(?:开启|打开|恢复|继续|开始|重开).{0,4}(?:杀|炫|功能)")
CALL_WINDOW = 300  # 多久之内喊杀的人算一起
NOBODY = "没人喊"  # _bar 的说明：没人喊杀、叫管理
RECENT = 20  # 喊杀没指明哪条时，重审喊杀之前的多少条
# @bot 申诉：群友举证（「这是他女朋友给他买的」「看对面那只手」），被判的人自己辩解。不限字数：带这些字的、引用了
# 杀炫区判词的、最近被判过的人 @bot 说话，都算；像别的插件指令的（「猜…」「提issue」「搜…」、宵禁）不算
APPEAL_WORDS = re.compile(
    r"[杀鲨🔪炫]|对象|女朋友|女友|男朋友|男友|npy|老婆|媳妇|约会|恋爱|前任|女人|女的|女生|妹子|陪|礼物|送的|买的|冤|造谣|污蔑|证据",
    re.I,
)
OTHER_CMD = re.compile(r"^\s*(?:盲猜|听歌|猜|提\s*issue|清\s*issue|提个|反馈|搜|帮助|菜单|玩法|加题|增加|提示)|宵禁|解禁", re.I)
# 说了理由才算申诉，只喊了声「杀」「这不杀啊」的只是喊杀：去掉这些字以后至少还剩 PLEA_LEN 个字
PLEA_FILLER = re.compile(r"[杀鲨🔪炫区蛆吗嘛啊呢吧了不这他她你我？?！!。，,~\s]")
PLEA_LEN = 3
ACCUSED = 900  # 被判过、被指名喊杀的人多久之内 @bot 说话算辩解
APPEAL_GAP = 60  # 一个人两次 @bot 喊杀、申诉至少隔几秒，免得一个人反复上诉，让模型重判到杀为止
LOOK_AGAIN = 3  # 申诉时最多带着申诉的话重看几张图
DOSSIER_HOURS = 24  # 档案：每人最近这么多小时被列出过的事和申诉结论，判他时给模型看（暗炫要连起来看）
DOSSIER_LINES = 6  # 档案每人最多给模型看几条
DOSSIER_MIN = 70  # 档案里给模型看的最低分（被杀过的不管几分都给）
# 发到群里的话都要短，不复述原话，免得 bot 号被风控。每样几种说法随机挑；嘲讽（roast）和没杀成时回喊杀的人的
# 那句（comeback）由模型现编，这里只是模型没给、给的不能用时的备用
LINES = {
    "mute": ["{roast}（禁言 {minutes} 分钟）", "{roast}（闭麦 {minutes} 分钟）", "{roast}（先冷静 {minutes} 分钟）"],
    "admin": ["{roast}（绿帽免死，记下了）", "{roast}（绿帽禁不了，先记账）", "{roast}（管理也炫是吧，小本本记上了）"],
    "acquit": ["审了，都挺低调", "翻了一遍，没人在炫，是你酸了", "就这也喊杀？草木皆兵了属于是", "没炫，别见风就是雨"],
    "short": ["有点味，差点意思", "炫得太含蓄，还不配挨刀", "差口气，再炫大点就杀", "这点段位，刀都懒得拔"],
    "done": ["已经杀过了", "人已经在小黑屋了，别鞭尸", "杀过了，排队也轮不到第二刀"],
    "lost": ["那条太早了，找不着了", "那条翻不到了，下次喊快点"],
    "self": ["杀我？我又没对象", "杀我？我清白得很，连对象都没有"],
    "failed": ["想杀，但禁不了", "刀举起来了，没砍下去"],
    "error": ["审不动了，等会再喊", "脑子卡了，过会再喊"],
    "pause": ["行，杀炫区先歇了", "收刀了，想炫的抓紧"],
    "pause_again": ["已经歇着呢", "刀早收了，别催"],
    "resume": ["杀炫区回来了", "刀磨好了，杀炫区回来了"],
    "paused": ["杀炫区歇着呢，找绿帽开", "刀收起来了，找绿帽开"],
    "not_admin": ["这得绿帽说了算", "你又不是绿帽，说了不算"],
}
ROAST_MAX = 30
# 模型写的嘲讽里有这些字就不用，换成 ROASTS 里的一句；提到代号、序号的也不用（群友看不懂）
ROAST_BANNED = re.compile(
    r"性|做爱|爱爱|开房|上床|床|睡|胸|腿|身材|裸|色|黄|约炮|处男|处女|射|操|干|屌|逼|鸡|妈|爹|代号|#\d|(?<![A-Za-z])[A-Z]\d*(?![A-Za-z])"
)
ROASTS = [
    "鉴定为：炫", "好好好，就你有", "真好呢，我们没有", "这么想被看见，那就看见了", "炫得这么用力，累不累",
    "群里不是你的朋友圈", "羡慕了，然后呢", "秀完了吗，秀完歇会",
]
ADMINS = ("owner", "admin")


def say(key: str, **kw) -> str:
    return random.choice(LINES[key]).format(**kw)


def usable(text) -> str:
    """模型写的嘲讽能不能发：太长、带敏感字、提到代号的不要，返回空串。"""
    text = str(text or "").strip()
    return "" if not text or len(text) > ROAST_MAX or ROAST_BANNED.search(text) else text


def argued(msg: Msg) -> str:
    """@bot 说的话里有没有理由（见 PLEA_FILLER），有就返回原话，只是喊杀返回空串。"""
    text = plain(msg.segs)
    return text if len(PLEA_FILLER.sub("", text)) >= PLEA_LEN else ""


def dossier_line(row: dict) -> str:
    """verdicts.jsonl 的一行写成档案里的一条，不带时间（给模型看时再加）。"""
    why = str(row.get("why") or "")
    if row.get("action") == "appeal":
        return f"{row.get('kind') or '申诉'}{row.get('verdict') or ''}：{why[:60]}"
    done = {"mute": "，被禁言", "admin": "，是管理员没禁", "cooldown": "，正在禁言"}.get(row.get("action"), "")
    return f"{row.get('kind') or '其他'} {row.get('score')} 分{done}：{why[:50]}"


def when(t: float, now: float) -> str:
    """档案里的时间：今天的只写几点，昨天的标上「昨天」，模型才分得清是不是今天刚被禁过。"""
    day = datetime.fromtimestamp(t)
    return ("" if day.date() == datetime.fromtimestamp(now).date() else "昨天 ") + day.strftime("%H:%M")


class XuanquPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_xuanqu")
        self.buffers: dict[str, deque[Msg]] = {}  # 群号 -> 最近的消息
        self.origins: dict[str, str] = {}  # 群号 -> 发消息用的 unified_msg_origin
        self.busy: set[str] = set()  # 正在判的群，一个群同时只判一批
        self.calls: dict[str, deque] = {}  # 群号 -> 最近喊杀的 (时间, QQ 号, 引用的消息 id)，排了立刻审就清空
        self.callers: dict[str, deque] = {}  # 群号 -> 最近喊杀、叫管理的 (时间, QQ 号, 是不是管理员)，定分数线用，不清空
        self.urgent: dict[str, dict] = {}  # 群号 -> 等着立刻审的喊杀，见 _on_call
        self.last_review: dict[str, float] = {}  # 群号 -> 上次立刻审的时间
        self.muted: dict[tuple[str, str], float] = {}  # (群号, QQ 号) -> 禁言到什么时候，期间再炫不重复杀
        self.punished: set[str] = set()  # 已经因为它杀过的消息 id，重审时跳过
        self.paused: set[str] = set(load_json(self.data_dir / "paused.json") or [])  # 管理员关了杀炫区的群
        self.accused: dict[tuple[str, str], float] = {}  # (群号, QQ 号) -> 最近被列出、被指名喊杀的时间，见 ACCUSED
        self.appealed: dict[tuple[str, str], float] = {}  # (群号, QQ 号) -> 上次 @bot 喊杀、申诉的时间
        self.last_case: dict[str, tuple[float, tuple]] = {}  # 群号 -> (时间, 消息 id)：上次指明了审哪几条，没引用的申诉接着审它
        self.dossier: dict[tuple[str, str], dict[str, dict]] = {}  # (群号, QQ 号) -> 档案，见 _remember
        self._load_dossier()
        self.self_id = ""
        self.bot = None  # OneBot 客户端，禁言、拉合并转发用；群里有人说话就记下
        self.vision = Vision(self._look)
        self.groups = Groups(Path(get_astrbot_data_path()) / "groups.yaml")  # 按群的知识库：黑话、群友外号
        self._ticker: asyncio.Task | None = None
        self._warned = False

    async def initialize(self):
        self._ticker = asyncio.create_task(self._tick_loop())

    async def terminate(self):
        if self._ticker:
            self._ticker.cancel()
        await self.vision.close()

    # 优先级比猜题插件高：「@bot 暂停 杀炫区」也会命中猜题的「暂停」指令，这里认出是开关就截住
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=10)
    async def on_group_message(self, event: AstrMessageEvent):
        """旁听：生效的群里每条消息都进缓冲。只有管理员 @bot 开关杀炫区时拦截，别的消息其他插件照常收到。"""
        gid = str(event.get_group_id() or "")
        if gid not in self._groups():
            return
        raw = event.message_obj.raw_message
        segs = raw.get("message") if isinstance(raw, dict) else None
        uid = str(event.get_sender_id())
        self.self_id = str(event.get_self_id())
        if not isinstance(segs, list) or uid == self.self_id:
            return
        self.bot = getattr(event, "bot", None) or self.bot
        self.origins[gid] = event.unified_msg_origin
        msg = Msg(
            mid=str(raw.get("message_id") or event.message_obj.message_id),
            uid=uid,
            name=event.get_sender_name() or uid,
            role=str((raw.get("sender") or {}).get("role") or "member"),
            t=time.time(),
            segs=segs,
        )
        self.buffers.setdefault(gid, deque(maxlen=BUFFER)).append(msg)
        text = plain(segs)
        at_bot = any(s.get("type") == "at" and str((s.get("data") or {}).get("qq")) == self.self_id for s in segs)
        boss = msg.role in ADMINS or event.is_admin()  # 群主、群管理员、AstrBot 管理员
        if at_bot and len(text) <= SHORT and await self._toggle(gid, msg, text, boss):
            msg.call = msg.judged = True
            event.stop_event()
            return
        if gid in self.paused:
            # 关着：不判、不看图，开了也不回头审这段
            msg.judged = True
            if at_bot and len(text) <= SHORT and AT_CALL.search(text):
                await self._send(gid, msg.mid, say("paused"))
            return
        if self._needs_prep(segs):
            msg.ready = asyncio.create_task(self._prepare(msg))
        summon = len(text) <= SHORT and bool(SUMMON.search(text))
        call = len(text) <= CALL_LEN and bool(CALL.search(text)) and not NOT_CALL.search(text)
        appeal = at_bot and self._is_appeal(gid, msg, text)
        if call or summon or appeal:
            msg.call = msg.judged = True  # 喊杀、叫管理、申诉的话本身不判
            self._on_call(gid, msg, at_bot=appeal, summon=summon, boss=boss)
        elif self._is_echo(gid, msg, text):
            msg.judged = True  # 复读不判：群里常复读，复读炫区、复读 bot 的通知都不是自己在炫

    def _is_appeal(self, gid: str, msg: Msg, text: str) -> bool:
        """@bot 说的这句是不是喊杀、申诉：带 APPEAL_WORDS 里的字，引用了杀炫区的判词，或者是 ACCUSED 秒内被判过的人
        在说话（这种只认没引用、引用自己的；引用别的插件的消息不算，比如引用猜题的题目答题）。像别的插件指令的都不算。"""
        if OTHER_CMD.search(text):
            return False
        if APPEAL_WORDS.search(text):
            return True
        if not text:
            return False
        reply = quote_id(msg.segs)
        quoted = self._find(gid, reply) if reply else None
        if quoted is not None and quoted.about:
            return True
        mine = quoted is not None and quoted.uid == msg.uid
        return (not reply or mine) and msg.t - self.accused.get((gid, msg.uid), 0) <= ACCUSED

    def _find(self, gid: str, mid: str) -> Msg | None:
        return next((m for m in self.buffers.get(gid, []) if m.mid == mid), None)

    async def _toggle(self, gid: str, msg: Msg, text: str, admin: bool) -> bool:
        """@bot 关、开杀炫区。是开关的话（不管有没有权限）回一句并返回 True。"""
        if PAUSE.search(text) and not text.rstrip().endswith(("?", "？")):
            if not admin:
                reply = say("not_admin")
            elif gid in self.paused:
                reply = say("pause_again")
            else:
                self.paused.add(gid)
                self.urgent.pop(gid, None)
                for m in self.buffers.get(gid, []):
                    m.judged = True
                reply = say("pause")
        elif gid in self.paused and admin and RESUME.search(text):
            self.paused.discard(gid)
            reply = say("resume")
        else:
            return False
        save_json(self.data_dir / "paused.json", sorted(self.paused))
        logger.info(f"[xuanqu] {gid} {msg.name}({msg.uid})：{text}（{'管理员' if admin else '非管理员'}）→ {reply}")
        await self._send(gid, msg.mid, reply)
        return True

    def _is_echo(self, gid: str, msg: Msg, text: str) -> bool:
        """照抄最近 ECHO_LOOKBACK 条里别人（包括 bot）说过的话，或者 bot 通知里的嘲讽。"""
        text = text.strip()
        if len(text) < 2:
            return False
        for m in list(self.buffers.get(gid, []))[-ECHO_LOOKBACK - 1:-1]:
            if m.uid == msg.uid:
                continue
            said = plain(m.segs)
            if said == text or (m.uid == self.self_id and m.roast and m.roast in text):
                return True
        return False

    def _groups(self) -> set[str]:
        return {str(g).strip() for g in self.config.get("groups", []) if str(g).strip()}

    def _provider(self) -> str:
        return self.config.get("judge_provider", "")

    def _on_call(self, gid: str, msg: Msg, *, at_bot: bool, summon: bool, boss: bool) -> None:
        """@bot 喊杀或申诉、有人叫管理、引用某条喊杀、或者 CALL_WINDOW 秒内喊杀的人够数，就排一次立刻审。
        引用了哪条就审哪条，引用的是杀炫区的判词就审那次判的几条；自己没引用但别人喊杀时引用了同一条，也审那条；
        @bot 申诉没引用也没人指明，接着审上一次指明的。被判的人自己 @bot 是辩解，不算喊杀的人头。分数线另算，见 _bar。"""
        key = (gid, msg.uid)
        if at_bot:
            if msg.t - self.appealed.get(key, 0) < APPEAL_GAP:
                logger.info(f"[xuanqu] {gid} {msg.name}({msg.uid})：{plain(msg.segs)[:20]}（@bot 太勤，不理）")
                return
            self.appealed[key] = msg.t
        reply = quote_id(msg.segs)
        quoted = self._find(gid, reply) if reply else None
        targets = tuple(quoted.about) if quoted is not None and quoted.about else (reply,) if reply else ()
        authors = {m.uid for mid in targets if (m := self._find(gid, mid))}
        defense = msg.uid in authors or (not targets and msg.t - self.accused.get(key, 0) <= ACCUSED)
        calls = self.calls.setdefault(gid, deque(maxlen=50))
        if not defense:
            self.callers.setdefault(gid, deque(maxlen=50)).append((msg.t, msg.uid, boss))
            calls.append((msg.t, msg.uid, targets))
        recent = [c for c in calls if msg.t - c[0] <= CALL_WINDOW]
        crowd = len({c[1] for c in recent}) >= max(1, int(self.config.get("crowd_calls", 2)))
        if not (at_bot or summon or crowd or targets):
            return
        if not targets:
            if pointed := Counter(c[2] for c in recent if c[2]).most_common(1):
                targets = pointed[0][0]
            elif at_bot and (last := self.last_case.get(gid)) and msg.t - last[0] <= CALL_WINDOW:
                targets = last[1]
        # 上一次还没审（隔得太近在排队）就合并：指明的那几条、申诉和「要回话」都留着；只有 @bot 喊的，没杀成也回一句
        prev = self.urgent.get(gid) or {}
        self.urgent[gid] = {
            "msg": msg,
            "targets": targets or prev.get("targets", ()),
            "explicit": at_bot or prev.get("explicit", False),
            "appeals": [*prev.get("appeals", []), *([msg] if at_bot else [])],
        }
        calls.clear()
        why = ("管理员" if boss else "") + ("辩解" if defense else "@bot" if at_bot else "叫管理" if summon else "喊杀")
        logger.info(f"[xuanqu] {gid} {msg.name}({msg.uid})：{plain(msg.segs)[:40]}（{why}），立刻审")

    def _bar(self, gid: str, since: float) -> tuple[int, str]:
        """禁言分数线和一句说明（日志、给模型看）。since 以后喊杀、叫管理的人越多，分数线越低：
        只有一个群友喊（哪怕 @bot）不降，还是 threshold；有管理员喊，从 admin_threshold 起算；
        每多一个人再降 crowd_step，最低 min_threshold。"""
        who: dict[str, bool] = {}
        for t, uid, boss in self.callers.get(gid, []):
            if t >= since:
                who[uid] = who.get(uid, False) or boss
        base = int(self.config.get("threshold", 90))
        boss = any(who.values())
        start = min(base, int(self.config.get("admin_threshold", 75))) if boss else base
        floor = min(start, int(self.config.get("min_threshold", 70)))
        bar = max(floor, start - int(self.config.get("crowd_step", 5)) * max(0, len(who) - 1))
        note = f"{len(who)} 个人在喊" + ("，有管理员" if boss else "") if who else NOBODY
        return bar, note

    def _needs_prep(self, segs: list) -> bool:
        for seg in segs:
            kind, data = seg.get("type"), seg.get("data") or {}
            if kind == "forward":
                return True
            if kind == "image" and int(data.get("sub_type") or 0) != STICKER_SUBTYPE and self.config.get("vision", True):
                return True
        return False

    async def _prepare(self, msg: Msg) -> None:
        """判之前要备好的：没带内容的合并转发去拉，图片送去看。失败了就当没看到。"""
        for seg in msg.segs:
            data = seg.get("data") or {}
            if seg.get("type") == "forward" and not data.get("content") and data.get("id") and self.bot:
                try:
                    res = await self.bot.call_action("get_forward_msg", id=data["id"])
                    data["content"] = (res or {}).get("messages") or []
                except Exception as e:
                    logger.warning(f"[xuanqu] 拉合并转发失败：{e}")
        if not self.config.get("vision", True):
            return
        images = {image_key(d): d for d in iter_images(msg.segs)}
        results = await asyncio.gather(*(self.vision.describe(d) for d in images.values()), return_exceptions=True)
        for key, res in zip(images, results):
            if isinstance(res, Exception):
                # 异常里可能带图片 URL（含 rkey），只记类型
                logger.warning(f"[xuanqu] 看图失败：{type(res).__name__}")
            else:
                msg.captions[key] = res

    async def _look(self, prompt: str, image: str) -> str:
        pid = self.config.get("vision_provider") or self._provider()
        resp = await self.context.llm_generate(chat_provider_id=pid, prompt=prompt, image_urls=[image])
        return resp.completion_text or ""

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            try:
                self._tick()
            except Exception:
                logger.exception("[xuanqu] tick failed")

    def _tick(self) -> None:
        """有人喊杀的群先审。其余的：发出来超过 lag 秒的消息才判，好把后面群友的反应带上；
        攒够一批，或者最早那条又等了 wait 秒，就判。"""
        now = time.time()
        lag = int(self.config.get("lag_seconds", 60))
        wait = int(self.config.get("max_wait_seconds", 60))
        batch = max(1, int(self.config.get("batch_size", 20)))
        for gid, buf in self.buffers.items():
            if gid in self.busy or gid in self.paused:
                continue
            urgent = None
            if gid in self.urgent and now - self.last_review.get(gid, 0) >= URGENT_GAP:
                urgent = self.urgent.pop(gid)
                self.last_review[gid] = now
            ripe = [m for m in buf if not m.judged and now - m.t >= lag]
            if not urgent and (not ripe or (len(ripe) < batch and now - ripe[0].t < lag + wait)):
                continue
            if not self._provider():
                for m in ripe:
                    m.judged = True
                if not self._warned:
                    logger.warning("[xuanqu] 没选判定用的模型（judge_provider），不判")
                    self._warned = True
                continue
            self.busy.add(gid)
            if urgent:
                asyncio.create_task(self._review(gid, urgent))
            else:
                asyncio.create_task(self._judge(gid, ripe[:MAX_TARGETS]))

    async def _judge(self, gid: str, targets: list[Msg]) -> None:
        """平时攒的一批。模型出错（DeepSeek 偶尔返回空）下一轮重判一次，再错就算了。"""
        ok = False
        try:
            buf = list(self.buffers[gid])
            first, last = buf.index(targets[0]), buf.index(targets[-1])
            before, after = buf[max(0, first - CONTEXT):first], buf[last + 1:last + 1 + AFTER]
            hits, _ = await self._ask(gid, before, targets, after, buf)
            ok = True
            # 这批后面有人喊杀（没凑够立刻审的，比如管理员没 @bot 说了句「杀」），分数线照样按人头降
            bar, note = self._bar(gid, targets[0].t)
            self._log_batch(gid, f"攒批（{note}，分数线 {bar}）", targets, hits)
            await self._punish(gid, hits, bar)
        except Exception:
            logger.exception(f"[xuanqu] 判群 {gid} 的一批消息失败")
        finally:
            for m in targets:
                m.tries += 1
                m.judged = ok or m.tries >= 2
            self.busy.discard(gid)

    async def _review(self, gid: str, urgent: dict) -> None:
        """有人喊杀、申诉：指明了哪几条就只审那几条（不在缓冲里就找 NapCat 要）；没指明就重审喊杀之前的 RECENT 条
        （判过的也重审，这回带上了喊杀）。@bot 说了理由的是申诉（举证、辩解），一起给模型；申诉说到图里的东西，判的模型
        看不到图，先带着这话让看图的模型重看一遍。申诉的结论记进被判的人的档案。
        @bot 喊的，不管结果怎样都回一句；杀成了的话，杀炫区的通知就是回应，没杀成用模型现编的 comeback。
        分数线按 CALL_WINDOW 秒内喊的人头算（_bar），后面又有人跟着喊、再审一次时更低。"""
        call: Msg = urgent["msg"]
        pleas = [m for m in urgent["appeals"] if argued(m)]
        targets: list[Msg] = []
        reply = ""
        try:
            buf = list(self.buffers[gid])
            found = []
            for mid in urgent["targets"]:
                if m := next((x for x in buf if x.mid == mid), None) or await self._fetch(gid, mid):
                    found.append(m)
            if urgent["targets"] and not found:
                reply = say("lost")
                return
            if found and all(m.uid == self.self_id for m in found):
                reply = say("self")
                return
            targets = sorted((m for m in found if not m.call and m.uid != self.self_id), key=lambda m: m.t)
            if targets:
                self.last_case[gid] = (call.t, tuple(m.mid for m in targets))
                for m in targets:
                    self.accused[(gid, m.uid)] = call.t
            else:
                targets = [m for m in buf[:buf.index(call)] if not m.call][-RECENT:]
            # 被判的人自己辩解，已经杀过的那条也要审（回他一句、记档案）；_act 不会因为同一条再杀一次
            defense = any(p.uid in {m.uid for m in targets} for p in pleas)
            fresh = targets if defense else [m for m in targets if m.mid not in self.punished]
            if not fresh:
                reply = say("done") if targets else say("acquit")
                return
            targets = fresh
            before, after = [], []
            if all(m in buf for m in targets):
                first, last = buf.index(targets[0]), buf.index(targets[-1])
                before, after = buf[max(0, first - CONTEXT):first], buf[last + 1:last + 1 + AFTER]
            after += sorted((m for m in {*urgent["appeals"], call} if m not in after and m not in targets), key=lambda m: m.t)
            words = "；".join(argued(p) for p in pleas)
            seen = await self._look_again(targets, words) if words else []
            bar, note = self._bar(gid, call.t - CALL_WINDOW)
            hits, data = await self._ask(gid, before, targets, after, buf, reported=note, pleas=pleas, seen=seen)
            self._log_batch(gid, f"喊杀复查（{note}，分数线 {bar}{'，有申诉' if pleas else ''}）", targets, hits)
            if pleas:
                self._settle(gid, targets, hits, pleas, data)
            actions = set(await self._punish(gid, hits, bar))
            if actions & {"mute", "admin"}:
                reply = ""
            elif actions & {"failed", "no_bot"}:
                reply = say("failed")
            elif actions & {"cooldown", "done"} and not defense:
                reply = say("done")
            else:
                reply = usable(data.get("comeback")) or say("short" if hits else "acquit")
        except Exception:
            logger.exception(f"[xuanqu] 群 {gid} 喊杀后的复查失败")
            reply = say("error")
        finally:
            for m in targets:
                m.judged = True
            self.busy.discard(gid)
            if reply and urgent["explicit"]:
                logger.info(f"[xuanqu] {gid} 回 {call.name}：{reply}")
                await self._send(gid, call.mid, reply, about=[m.mid for m in targets])

    async def _look_again(self, targets: list[Msg], words: str) -> list[tuple[Msg, str]]:
        """带着申诉的话把要判的图重看一遍（最近的图先看，最多 LOOK_AGAIN 张），返回 [(消息, 看到了什么)]。"""
        if not self.config.get("vision", True):
            return []
        jobs = [(m, d) for m in reversed(targets) for d in iter_images(m.segs)][:LOOK_AGAIN]
        results = await asyncio.gather(*(self.vision.check(d, words) for _, d in jobs), return_exceptions=True)
        out = []
        for (m, _), res in zip(jobs, results):
            if isinstance(res, Exception):
                logger.warning(f"[xuanqu] 带着申诉重看图失败：{type(res).__name__}")
            elif res:
                out.append((m, res))
        return out

    def _settle(self, gid: str, targets: list[Msg], hits: list, pleas: list[Msg], data: dict) -> None:
        """申诉的结论（模型的 appeal、fact）记进被判的人的档案和 verdicts.jsonl，以后判他时模型能看到。
        要判的不止一个人时记在分最高的那位名下，没人被列出就不记。"""
        verdict, fact = str(data.get("appeal") or "").strip(), str(data.get("fact") or "").strip()
        if not verdict:
            return
        if len({m.uid for m in targets}) == 1:
            subject = targets[-1]
        elif hits:
            subject = max(hits, key=lambda x: x[1]["score"])[0]
        else:
            return
        said = "；".join(argued(p) for p in pleas)
        row = {
            "time": datetime.now().isoformat(timespec="seconds"), "group": gid, "user": subject.uid,
            "name": subject.name, "text": said[:100], "score": None,
            "kind": "辩解" if any(p.uid == subject.uid for p in pleas) else "举证",
            "verdict": verdict, "why": fact[:80] or said[:40], "roast": "", "action": "appeal", "mid": subject.mid,
        }
        logger.info(f"[xuanqu] {gid} {subject.name}({subject.uid}) {row['kind']}{verdict}：{row['why']}（{said[:40]}）")
        self._write(row)
        self._remember(gid, subject.uid, row, time.time())

    async def _fetch(self, gid: str, mid: str, *, prep: bool = True) -> Msg | None:
        """被引用的消息不在缓冲里（太早，或者在重启之前），找 NapCat 要。拿不到返回 None。
        prep=False 不看图，只给模型当引用的原文看。"""
        if self.bot is None:
            return None
        try:
            res = await self.bot.call_action("get_msg", message_id=int(mid))
        except Exception as e:
            logger.warning(f"[xuanqu] 拉消息 {mid} 失败：{e}")
            return None
        if not isinstance(res, dict) or not isinstance(res.get("message"), list):
            return None
        sender = res.get("sender") or {}
        uid = str(sender.get("user_id") or res.get("user_id") or "")
        role = sender.get("role")
        if not role and uid and uid != self.self_id:
            # get_msg 不一定带群身份，管理员禁不了，要先知道
            try:
                info = await self.bot.call_action("get_group_member_info", group_id=int(gid), user_id=int(uid))
                role = (info or {}).get("role")
            except Exception:
                pass
        msg = Msg(
            mid=mid,
            uid=uid,
            name=str(sender.get("card") or sender.get("nickname") or uid),
            role=str(role or "member"),
            t=float(res.get("time") or time.time()),
            segs=res["message"],
        )
        if prep and self._needs_prep(msg.segs):
            msg.ready = asyncio.create_task(self._prepare(msg))
        return msg

    async def _ask(
        self, gid: str, before: list[Msg], targets: list[Msg], after: list[Msg], buf: list[Msg], *,
        reported: str = "", pleas: list[Msg] = (), seen: list[tuple[Msg, str]] = (),
    ) -> tuple[list, dict]:
        """返回 ([(消息, 模型给的判定)], 模型输出的整个 JSON)，判定只含「要判」里的消息。reported 是喊杀复查时几个人在喊，
        这时模型多写一句 comeback（没杀成时回给喊杀的人）。pleas 是说了理由的申诉（要在前后文里），seen 是带着申诉
        重看图的结果；有申诉时模型还写 appeal（采信没有）和 fact（记进档案的一句）。要判的人有档案就一起给模型。"""
        # 要判的消息之间夹着的喊杀、复读、bot 的通知也要原样带上（标〔不判〕），群友反应全在这些里面
        span = targets
        if all(m in buf for m in targets):
            span = buf[buf.index(targets[0]):buf.index(targets[-1]) + 1]
        window = before + span + after
        pending = [m.ready for m in window if m.ready and not m.ready.done()]
        if pending:
            await asyncio.wait(pending, timeout=READY_TIMEOUT)
        numbers = {m.mid: i + 1 for i, m in enumerate(window)}
        lookup = {m.mid: m for m in buf}
        # 引用的那条不在缓冲里（重启前、太早）就去要，不然模型只看到「一条更早的消息」，不知道他在回谁
        quoted = {str((s.get("data") or {}).get("id")) for m in targets for s in m.segs if s.get("type") == "reply"}
        found = await asyncio.gather(*(self._fetch(gid, mid, prep=False) for mid in quoted - lookup.keys()))
        lookup.update((m.mid, m) for m in found if m)
        r = Renderer(Codes(self.self_id), numbers, lookup)
        judging = {m.mid for m in targets}
        # 先渲染消息，代号按说话的先后排；说明、申诉、档案里用到的代号跟着它
        body = []
        for title, msgs in (("前文，只作参考", before), ("要判", span), ("后文，只作参考", after)):
            if msgs:
                body.append(f"【{title}】")
                body += [
                    r.line(numbers[m.mid], m) + (" 〔不判〕" if title == "要判" and m.mid not in judging and m.uid != self.self_id else "")
                    for m in msgs
                ]
        lines = []
        accused = {m.uid for m in targets}
        pleas = [p for p in pleas if p.mid in numbers]
        if reported:
            which = f"指的是 #{numbers[targets[0].mid]}" if len(targets) == 1 else "但没指明是哪条，看看「要判」里谁在炫"
            if reported == NOBODY and pleas and all(p.uid in accused for p in pleas):
                lines.append(f"【说明】没人喊杀，是被判的人自己 @机器人 辩解，{which}。")
            else:
                lines.append(f"【说明】群友在喊「杀」或者叫管理（{reported}），{which}。这只是线索，也可能指错了人（比如指到了转述别人的那条），照标准判，不是炫就不要列出来。")
        if pleas:
            roles = "，".join(f"#{numbers[p.mid]} 是{'被判的人自己辩解' if p.uid in accused else '群友举证'}" for p in pleas)
            lines.append(f"【申诉】{roles}，规矩见「申诉」。")
        if notes := self.groups.notes(gid):
            lines.append("【本群】" + "；".join(notes))
        if named := self._named(gid, [m for m in span if m.mid in judging] + pleas, window, r.codes):
            lines.append("【提到的人】" + "，".join(named))
        lines += [f"【看图复查】带着申诉的话把 #{numbers[m.mid]} 的图又看了一遍：{s}" for m, s in seen if m.mid in numbers]
        if dossier := self._dossier_lines(gid, targets, r.codes):
            lines.append(f"【档案】要判的人最近 {DOSSIER_HOURS} 小时被列出过的事和申诉结论，规矩见「暗炫和档案」：")
            lines += dossier
        lines += body
        try:
            resp = await self.context.llm_generate(
                chat_provider_id=self._provider(), system_prompt=JUDGE_SYSTEM, prompt="\n".join(lines)
            )
        except Exception as e:
            # DeepSeek 的输入审核：一条消息被拦整批都失败，对半拆开重判，拆到一条还被拦就跳过
            if "Content Exists Risk" not in str(e):
                raise
            if len(targets) == 1:
                logger.warning(f"[xuanqu] 这条被模型的内容审核拦了，跳过：{r.content(targets[0])[:50]}")
                return [], {}
            half = len(targets) // 2
            first, first_data = await self._ask(gid, [], targets[:half], [], buf, reported=reported)
            second, second_data = await self._ask(gid, [], targets[half:], [], buf, reported=reported)
            return first + second, first_data or second_data
        data = parse_json(resp.completion_text)
        if data is None:
            logger.warning(f"[xuanqu] 模型输出不是 JSON：{(resp.completion_text or '')[:200]}")
            return [], {}
        by_number = {numbers[m.mid]: m for m in targets}
        hits = []
        for h in data.get("hits") or []:
            try:
                n, score = int(h["n"]), int(h["score"])
            except (TypeError, KeyError, ValueError):
                continue
            if m := by_number.get(n):
                hits.append((m, {**h, "score": score, "text": r.content(m)}))
        return hits, data

    def _named(self, gid: str, msgs: list[Msg], window: list[Msg], codes: Codes) -> list[str]:
        """要判的消息、申诉里提到了谁（群名片，或者知识库里记的外号），告诉模型是哪个代号；提到的人不在这段里，
        就说是另一位群友。模型只看代号，不然不知道「老王一个人会去吃草吗」说的是谁，也会把别人的外号当成说话的人自己。"""
        said = "".join(plain(m.segs) for m in msgs)
        here = {m.uid for m in window}
        names: dict[str, set[str]] = {}
        for m in window:
            if m.uid != codes.self_id:
                names.setdefault(m.name, set()).add(m.uid)
        for uid, nicks in self.groups.nicknames(gid).items():
            for nick in nicks:
                names.setdefault(nick, set()).add(uid)
        out = []
        for name, uids in names.items():
            if len(name) < 2 or name not in said or len(uids) > 1:
                continue
            uid = next(iter(uids))
            out.append(f"「{name}」是 {codes(uid)}" if uid in here else f"「{name}」是另一位群友，不在这段里")
        return out

    def _dossier_lines(self, gid: str, targets: list[Msg], codes: Codes) -> list[str]:
        """要判的人的档案，一人一行。这次要判的消息以前判过的分数不带（这次重判），申诉的结论带上。"""
        now, judging, lines = time.time(), {m.mid for m in targets}, []
        for uid in dict.fromkeys(m.uid for m in targets):
            rows = sorted(
                (e for e in self.dossier.get((gid, uid), {}).values()
                 if e["show"] and now - e["t"] <= DOSSIER_HOURS * 3600 and (e["appeal"] or e["mid"] not in judging)),
                key=lambda e: e["t"],
            )
            if rows:
                lines.append(f"{codes(uid)}：" + "；".join(f"{when(e['t'], now)} {e['line']}" for e in rows[-DOSSIER_LINES:]))
        return lines

    def _remember(self, gid: str, uid: str, row: dict, t: float) -> None:
        """verdicts.jsonl 的一行记进档案。同一条消息判了几次只留最后一次（老记录没有消息 id，按原文认）。
        只给模型看（show）DOSSIER_MIN 分以上、被杀过的，采信了的举证，和他自己的辩解：五六十分的、没采信的举证
        也给模型看的话，模型会当成「又被指过一次」，越滚越多。"""
        book = self.dossier.setdefault((gid, uid), {})
        appeal = row.get("action") == "appeal"
        if appeal:
            show = row.get("kind") == "辩解" or row.get("verdict") in ("采信", "部分采信")
        else:
            show = row.get("action") in ("mute", "admin") or int(row.get("score") or 0) >= DOSSIER_MIN
        key = f"appeal-{t}" if appeal else str(row.get("mid") or row.get("text") or t)
        book[key] = {"t": t, "mid": str(row.get("mid") or ""), "appeal": appeal, "show": show, "line": dossier_line(row)}
        for k in [k for k, e in book.items() if t - e["t"] > DOSSIER_HOURS * 3600]:
            del book[k]

    def _load_dossier(self) -> None:
        """启动时从 verdicts.jsonl 把最近 DOSSIER_HOURS 小时的记录读回档案，重启不丢。"""
        path = self.data_dir / "verdicts.jsonl"
        if not path.exists():
            return
        cutoff = time.time() - DOSSIER_HOURS * 3600
        for line in path.read_text("utf-8").splitlines():
            try:
                row = json.loads(line)
                t = datetime.fromisoformat(row["time"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if t >= cutoff:
                self._remember(str(row.get("group")), str(row.get("user")), row, t)

    def _write(self, row: dict) -> None:
        with open(self.data_dir / "verdicts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _log_batch(gid: str, kind: str, targets: list[Msg], hits: list) -> None:
        images = sum(len(m.captions) for m in targets)
        logger.info(f"[xuanqu] {gid} {kind}：判了 {len(targets)} 条（看图 {images} 张），模型列出 {len(hits)} 条")

    async def _punish(self, gid: str, hits: list, threshold: int) -> list[str]:
        best: dict[str, tuple[Msg, dict]] = {}  # 一个人一批里只算分最高的那条
        for m, h in hits:
            if m.uid not in best or h["score"] > best[m.uid][1]["score"]:
                best[m.uid] = (m, h)
        actions = []
        for m, h in best.values():
            action = await self._act(gid, m, h, threshold)
            self._record(gid, m, h, action)
            actions.append(action)
        return actions

    async def _act(self, gid: str, m: Msg, h: dict, threshold: int) -> str:
        now = time.time()
        minutes = int(self.config.get("mute_minutes", 10))
        if h["score"] < threshold:
            return "below"
        if self.config.get("dry_run", False):
            return "dry_run"
        if m.mid in self.punished:
            return "done"  # 辩解时会重审已经杀过的那条，同一条不杀第二次
        if self.muted.get((gid, m.uid), 0) > now:
            return "cooldown"
        if m.role in ADMINS:
            action = "admin"
        elif self.bot is None:
            return "no_bot"
        else:
            try:
                await self.bot.call_action(
                    "set_group_ban", group_id=int(gid), user_id=int(m.uid), duration=minutes * 60
                )
            except Exception as e:
                logger.warning(f"[xuanqu] 禁言 {m.uid} 失败：{e}")
                return "failed"
            action = "mute"
        self.muted[(gid, m.uid)] = now + minutes * 60
        self.punished.add(m.mid)
        roast = usable(h.get("roast")) or random.choice(ROASTS)
        await self._send(gid, m.mid, say(action, roast=roast, minutes=minutes), roast=roast, about=[m.mid])
        return action

    async def _send(self, gid: str, reply_to: str, text: str, roast: str = "", about: list[str] = ()) -> None:
        """引用 reply_to 发一句。发出去的话也记进缓冲（bot 自己的消息插件收不到），模型才看得出后面有人在复读 bot，
        _is_echo 也靠它。about 是这句说的是哪几条消息，有人引用这句申诉时审它们；要认出引用，得记下真的消息 id，
        所以直接调 OneBot 发。"""
        mid = ""
        try:
            if self.bot is not None:
                message = [{"type": "reply", "data": {"id": str(reply_to)}}, {"type": "text", "data": {"text": text}}]
                res = await self.bot.call_action("send_group_msg", group_id=int(gid), message=message)
                mid = str((res or {}).get("message_id") or "")
            else:
                await self.context.send_message(self.origins[gid], MessageChain([Comp.Reply(id=reply_to), Comp.Plain(text)]))
        except Exception as e:
            logger.warning(f"[xuanqu] 往群 {gid} 发消息失败：{e}")
            return
        self.buffers.setdefault(gid, deque(maxlen=BUFFER)).append(Msg(
            mid=mid or f"bot-{time.time()}", uid=self.self_id, name="bot", role="admin", t=time.time(),
            segs=[{"type": "text", "data": {"text": text}}], judged=True, call=True, roast=roast, about=list(about),
        ))

    def _record(self, gid: str, m: Msg, h: dict, action: str) -> None:
        """模型列出来的（50 分以上）都记下来，调阈值和提示词用，也记进档案。被杀的人 ACCUSED 秒内 @bot 算辩解
        （没到分数线的群里看不到，不算）。"""
        logger.info(f"[xuanqu] {gid} {m.name}({m.uid}) {h['score']} {h.get('kind', '')} -> {action}: {h.get('why', '')}")
        row = {
            "time": datetime.now().isoformat(timespec="seconds"), "group": gid, "user": m.uid, "name": m.name,
            "text": h["text"], "score": h["score"], "kind": h.get("kind", ""), "why": h.get("why", ""),
            "roast": h.get("roast", ""), "action": action, "mid": m.mid,
        }
        self._write(row)
        self._remember(gid, m.uid, row, time.time())
        if action in ("mute", "admin"):
            self.accused[(gid, m.uid)] = time.time()
