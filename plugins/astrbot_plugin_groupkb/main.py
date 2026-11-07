import asyncio
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core import chatlog, kb
from .core.prompts import DIGEST_SYSTEM, EXTRACT_SYSTEM, parse_json

TICK = 60  # 每隔几秒看一次到点没有
CHUNK = 500  # 一段聊天送多少条给模型
MIN_ROWS = 30  # 一天少于这么多条就不整理
CATCH_UP = 3  # 电脑睡过了点，醒来最多补几天
HISTORY = 3000  # 整理时找 NapCat 要多少条历史，补上 AstrBot 重启那几秒漏记的（大约两天的量）
RETRY = 3600  # 整理失败（模型出错）隔多久再试
MAX_TRIES = 3  # 同一天失败几次就跳过
NOTES_PER_DAY = 3  # 一天最多新记几条黑话
NICK_SEEN = 2  # 外号当天至少被叫几次才记
KEYS = ("name", "notes", "members", "events")  # groups.yaml 里一个群的字段，按这个顺序写


class GroupKB(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_groupkb")
        self.chat_dir = self.data_dir / "chat"
        self.kb_path = Path(get_astrbot_data_path()) / "groups.yaml"
        self.state_path = self.data_dir / "state.json"
        self.state: dict[str, str] = self._load_state()  # 群号 -> 整理到了哪天
        self.failed: dict[tuple[str, str], tuple[int, float]] = {}  # (群号, 日期) -> (失败几次, 下次什么时候试)
        self.self_id = ""
        self.bot = None  # OneBot 客户端，补历史、查群名用；群里有人说话就记下
        self._ticker: asyncio.Task | None = None

    async def initialize(self):
        self._ticker = asyncio.create_task(self._loop())

    async def terminate(self):
        if self._ticker:
            self._ticker.cancel()

    # 优先级最高、从不拦截：别的插件认出指令会截住消息（猜题的「猜游戏」），要在它们之前记下来
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100)
    async def on_group_message(self, event: AstrMessageEvent):
        """生效的群里每条消息都落盘，包括 bot 自己发的。"""
        gid = str(event.get_group_id() or "")
        if gid not in self._groups():
            return
        raw = event.message_obj.raw_message
        segs = raw.get("message") if isinstance(raw, dict) else None
        if not isinstance(segs, list):
            return
        self.self_id = str(event.get_self_id())
        self.bot = getattr(event, "bot", None) or self.bot
        uid = str(event.get_sender_id())
        sender = raw.get("sender") or {}
        name = "机器人" if uid == self.self_id else str(sender.get("card") or sender.get("nickname") or uid)
        mid = raw.get("message_id") or event.message_obj.message_id
        try:
            chatlog.append(self.chat_dir, gid, chatlog.row(raw.get("time") or time.time(), mid, uid, name, segs))
        except OSError as e:
            logger.warning(f"[groupkb] 记聊天失败：{e}")

    def _groups(self) -> set[str]:
        return {str(g).strip() for g in self.config.get("groups", []) if str(g).strip()}

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), "utf-8")
        tmp.replace(self.state_path)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            try:
                await self._due()
            except Exception:
                logger.exception("[groupkb] 定时整理出错")

    async def _due(self) -> None:
        """过了 run_at，把每个群还没整理的日子（最多 CATCH_UP 天，到昨天为止）挨个整理。"""
        now = datetime.now()
        if now.strftime("%H:%M") < str(self.config.get("run_at") or "04:00"):
            return
        if self.bot is None:
            return  # 重启后还没人说过话：拿不到 OneBot 客户端补不了历史，也不知道 bot 自己的 QQ 号，等一等
        today = now.date()
        for gid in sorted(self._groups()):
            done = self.state.get(gid)
            day = date.fromisoformat(done) + timedelta(days=1) if done else today - timedelta(days=1)
            day = max(day, today - timedelta(days=CATCH_UP))
            while day < today:
                tries, next_try = self.failed.get((gid, day.isoformat()), (0, 0))
                if time.time() < next_try:
                    break
                try:
                    await self.run_day(gid, day)
                except Exception:
                    tries += 1
                    logger.exception(f"[groupkb] 整理 {gid} {day} 失败（第 {tries} 次）")
                    if tries < MAX_TRIES:
                        self.failed[(gid, day.isoformat())] = (tries, time.time() + RETRY)
                        break
                    logger.warning(f"[groupkb] {gid} {day} 失败 {tries} 次，跳过")
                self.state[gid] = day.isoformat()
                self._save_state()
                day += timedelta(days=1)
        chatlog.prune(self.chat_dir, int(self.config.get("keep_days", 14)))

    async def run_day(self, gid: str, day: date) -> dict:
        """整理一个群的一天：分段让模型挑外号、黑话、有意思的事，再汇总一次，写进 groups.yaml。返回加了什么。"""
        rows = chatlog.merge(chatlog.load(self.chat_dir, gid, day), await self._history(gid, day))
        if len(rows) < MIN_ROWS:
            logger.info(f"[groupkb] {gid} {day} 只有 {len(rows)} 条，不整理")
            return {}
        r = chatlog.Render(rows, self.self_id)
        entry = kb.load(self.kb_path).get(gid) or {}
        nicks, notes, events = [], [], []
        for i in range(0, len(r.lines), CHUNK):
            got = await self._extract(r, r.lines[i:i + CHUNK], entry)
            nicks += [n for n in got.get("nicknames") or [] if isinstance(n, dict)]
            notes += [r.decode(n) for n in got.get("notes") or [] if n]
            events += [{**e, "text": r.decode(e["text"])} for e in got.get("events") or [] if isinstance(e, dict) and e.get("text")]
        digest = await self._digest(day, events, notes, entry) if events or notes else {}

        # 整理要好几分钟，期间可能有人手改了文件：重新读一遍，再往里加
        data = kb.load(self.kb_path)
        entry = data.get(gid) or {}
        # 只数原话里的字（不含引用带的原文），外号当天至少出现 NICK_SEEN 次才记：只出现一次的多半拿不准是在叫谁
        said = "\n".join("".join(s["data"].get("text", "") for s in row["segs"] if s["type"] == "text") for row in rows)
        added = {"nicknames": [], "notes": [], "events": 0, "rows": len(rows)}
        for n in nicks:
            uid, name = r.roster.get(str(n.get("who"))), str(n.get("name") or "").strip()
            if uid and uid != self.self_id and name and said.count(name) >= NICK_SEEN and kb.add_nickname(entry, uid, name, r.names.get(uid, "")):
                added["nicknames"].append(f"{r.names.get(uid) or uid}={name}")
                logger.info(f"[groupkb] {gid} 外号 {r.names.get(uid)}({uid}) = {name}：{str(n.get('why', ''))[:60]}")
        cap = int(self.config.get("max_notes", 20))
        new_notes = [r.decode(n) for n in digest.get("notes") or []][:NOTES_PER_DAY]
        added["notes"] = [n for n in new_notes if kb.add_note(entry, n, cap)]
        limit = int(self.config.get("events_per_day", 5))
        added["events"] = kb.set_events(entry, day.isoformat(), [r.decode(e) for e in digest.get("events") or []][:limit])
        if "name" not in entry and (name := await self._group_name(gid)):
            entry["name"] = name
        data[gid] = {k: entry[k] for k in KEYS if k in entry} | {k: v for k, v in entry.items() if k not in KEYS}
        kb.save(self.kb_path, data)
        logger.info(
            f"[groupkb] {gid} {day}：{len(rows)} 条，新外号 {added['nicknames'] or '无'}，"
            f"新黑话 {added['notes'] or '无'}，留档 {added['events']} 件"
        )
        return added

    async def _history(self, gid: str, day: date) -> list[dict]:
        """找 NapCat 要最近的历史，挑出这一天的，补上没落盘的（AstrBot 重启的那几秒、插件装上之前）。
        拉不到就报错，这一天过一会再试：只靠落盘的可能缺一大段，整理出来不全还会被记成整理过了。"""
        if self.bot is None:
            raise RuntimeError("还没有 OneBot 客户端，拉不了历史")
        res = await self.bot.call_action("get_group_msg_history", group_id=int(gid), count=HISTORY)
        start, end = chatlog.day_range(day)
        rows = []
        for m in (res or {}).get("messages") or []:
            if not start <= float(m.get("time") or 0) < end:
                continue
            uid, sender = str(m.get("user_id") or ""), m.get("sender") or {}
            name = "机器人" if uid == self.self_id else str(sender.get("card") or sender.get("nickname") or uid)
            rows.append(chatlog.row(m["time"], m.get("message_id"), uid, name, m.get("message") or []))
        return rows

    async def _group_name(self, gid: str) -> str:
        if self.bot is None:
            return ""
        try:
            return str((await self.bot.call_action("get_group_info", group_id=int(gid)) or {}).get("group_name") or "")
        except Exception:
            return ""

    def _provider(self) -> str:
        provider = self.config.get("provider", "")
        if not provider:
            raise RuntimeError("没选整理用的模型（provider）")
        return provider

    async def _ask(self, system: str, prompt: str) -> dict:
        resp = await self.context.llm_generate(chat_provider_id=self._provider(), system_prompt=system, prompt=prompt)
        data = parse_json(resp.completion_text)
        if data is None:
            raise ValueError(f"模型输出不是 JSON：{(resp.completion_text or '')[:200]}")
        return data

    async def _extract(self, r: chatlog.Render, lines: list[str], entry: dict) -> dict:
        """一段聊天挑外号、黑话、事。DeepSeek 的内容审核拦了就对半拆开，拆到很短还被拦就跳过这段。"""
        have_nicks = "、".join(n for names in kb.nicknames(entry).values() for n in names) or "（无）"
        have_notes = "；".join(kb.notes(entry)) or "（无）"
        prompt = "\n".join([
            "【代号】", *r.roster_lines(),
            "【知识库里已经记了的】", f"外号：{have_nicks}", f"黑话、梗：{have_notes}",
            "【聊天记录】", *lines,
        ])
        try:
            return await self._ask(EXTRACT_SYSTEM, prompt)
        except Exception as e:
            if "Content Exists Risk" not in str(e):
                raise
            if len(lines) < 40:
                logger.warning(f"[groupkb] 这段被模型的内容审核拦了，跳过：{lines[0][:30]}")
                return {}
            half = len(lines) // 2
            a, b = await self._extract(r, lines[:half], entry), await self._extract(r, lines[half:], entry)
            return {k: (a.get(k) or []) + (b.get(k) or []) for k in ("nicknames", "notes", "events")}

    async def _digest(self, day: date, events: list[dict], notes: list[str], entry: dict) -> dict:
        """各段挑出来的事合并、挑最值得留档的几件；新黑话去重。"""
        prompt = "\n".join([
            f"日期：{day.isoformat()}",
            "【各段挑出来的事】", *(f"{e.get('time', '')} {e.get('text')}" for e in events),
            "【新发现的黑话、梗】", *(notes or ["（无）"]),
            "【已有的黑话】", *(kb.notes(entry) or ["（无）"]),
        ])
        return await self._ask(DIGEST_SYSTEM.format(n=int(self.config.get("events_per_day", 5))), prompt)
