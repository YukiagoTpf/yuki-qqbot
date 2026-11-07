"""把一段真实群聊按原来的时间回放给插件，看攒批、喊杀、申诉、档案一路怎么走（真调 DeepSeek，禁言、发消息是假的，只打印）。
eval_judge.py 只测提示词；这个测整条流程，查「为什么没杀」用。在 astrbot 容器里跑：

  python3 replay.py <插件父目录> <数据目录> <群聊记录 json> <从 HH:MM> <到 HH:MM> [额外消息 json]

- 插件拷到容器的 /tmp/xxx<数字>/，数据目录用线上的副本（档案从里面的 verdicts.jsonl 读，回放中会往里写），别用线上目录。
- 群聊记录用 get_group_msg_history 拉，取最后一天；「从」之前 30 分钟的消息只进缓冲不判。图片链接过一阵会失效，要现拉。
- 额外消息是自己编的（比如被判的人来辩解），格式同群聊记录，按 time 插进去。
- 环境变量：DEEPSEEK_API_KEY，ADMINS（AstrBot 管理员 QQ 号，逗号分隔），SELF（bot 的 QQ 号）。
- 时钟是假的：插件里的 time.time()、datetime.now() 都走回放的时间。回放里有人引用当时真 bot 的话，换成引用假 bot 回同一条的那句。"""
import asyncio
import datetime as dt
import json
import os
import sys
import time as real_time
import types
from pathlib import Path

ROOT, DATA, HIST, START, END = sys.argv[1:6]
EXTRA = json.loads(Path(sys.argv[6]).read_text("utf-8")) if len(sys.argv) > 6 else []
sys.path.insert(0, ROOT)
from openai import AsyncOpenAI  # noqa: E402

from astrbot.api.star import StarTools  # noqa: E402

StarTools.get_data_dir = classmethod(lambda cls, plugin_name=None: Path(DATA))
import astrbot_plugin_xuanqu.main as xm  # noqa: E402

SELF = os.environ["SELF"]
GID = ""
ADMINS = set(os.environ.get("ADMINS", "").split(","))
client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com/v1")


class Clock:
    now = 0.0

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(real_time, name)


clock = Clock()
xm.time = clock


class FakeDT(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return dt.datetime.fromtimestamp(clock.now)


xm.datetime = FakeDT


class Ctx:
    async def llm_generate(self, chat_provider_id, prompt, system_prompt=None, image_urls=None, **kw):
        content = prompt
        if image_urls:
            url = image_urls[0]
            if url.startswith("base64://"):
                url = "data:image/jpeg;base64," + url[9:]
            content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]
        msgs = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + [{"role": "user", "content": content}]
        extra = {"thinking": {"type": "disabled"}} if chat_provider_id.endswith("fast") else {}
        r = await client.chat.completions.create(model="deepseek-flash", messages=msgs, extra_body=extra)
        out = r.choices[0].message.content
        if system_prompt:
            print("    ┌ 送给模型：\n" + "\n".join("    │ " + x for x in prompt.splitlines() if not x.startswith("#") or "〔不判〕" not in x))
            print("    └ 模型：" + out.replace("\n", " ")[:600])
        elif "群友说" in prompt:
            print("    · 重看图：" + out.replace("\n", " ")[:200])
        return types.SimpleNamespace(completion_text=out)

    async def send_message(self, *a, **kw):
        print("    !! 走了 context.send_message")


class Bot:
    def __init__(self, msgs):
        self.byid = {str(m["message_id"]): m for m in msgs}
        self.n = 0
        self.replied = {}  # 假 bot 发的消息 id -> 它引用的消息 id

    async def call_action(self, action, **kw):
        if action == "get_msg":
            return self.byid.get(str(kw["message_id"]))
        if action == "get_group_member_info":
            return {"role": "member"}
        if action == "set_group_ban":
            print(f"  >>> 禁言 {kw['user_id']} {kw['duration']} 秒")
            return {}
        if action == "send_group_msg":
            self.n += 1
            mid = 900000000 + self.n
            text = "".join(s["data"].get("text", "") for s in kw["message"] if s["type"] == "text")
            reply = next(s["data"]["id"] for s in kw["message"] if s["type"] == "reply")
            print(f"  >>> bot 发（{mid}，引用 {reply}）：{text}")
            self.replied[str(mid)] = str(reply)
            self.byid[str(mid)] = {"message_id": mid, "user_id": int(SELF), "time": clock.now,
                                   "sender": {"user_id": int(SELF), "nickname": "Yuki"}, "message": kw["message"]}
            return {"message_id": mid}
        if action == "get_forward_msg":
            return {"messages": []}
        raise RuntimeError(action)


class Ev:
    def __init__(self, m):
        self.m, self.bot = m, BOT
        self.message_obj = types.SimpleNamespace(raw_message=m, message_id=m["message_id"])
        self.unified_msg_origin = f"napcat:GroupMessage:{GID}"

    def get_group_id(self):
        return GID

    def get_sender_id(self):
        return str(self.m["user_id"])

    def get_self_id(self):
        return SELF

    def get_sender_name(self):
        s = self.m["sender"]
        return s.get("card") or s.get("nickname")

    def is_admin(self):
        return str(self.m["user_id"]) in ADMINS

    def stop_event(self):
        pass


def at(day, hhmm):
    return dt.datetime.combine(day, dt.time(*map(int, hhmm.split(":")))).timestamp()


def show(m):
    s = m["sender"]
    text = "".join(x["data"].get("text", "") if x["type"] == "text" else f"[{x['type']}]" for x in m["message"])
    return f"{dt.datetime.fromtimestamp(m['time']):%H:%M:%S} {s.get('card') or s.get('nickname')}：{text[:60]}"


async def main():
    global BOT
    global GID
    hist = json.loads(Path(HIST).read_text("utf-8"))
    GID = str(hist[-1]["group_id"])
    day = dt.date.fromtimestamp(hist[-1]["time"])
    t0, t1 = at(day, START), at(day, END)
    msgs = [m for m in hist if t0 - 1800 <= m["time"] <= t1] + EXTRA
    msgs.sort(key=lambda m: m["time"])
    BOT = Bot(hist + EXTRA)
    cfg = {"groups": [GID], "judge_provider": "deepseek-flash", "vision_provider": "deepseek-flash-fast", "vision": True,
           "threshold": 90, "admin_threshold": 75, "crowd_step": 5, "min_threshold": 70, "mute_minutes": 10,
           "batch_size": 20, "lag_seconds": 60, "max_wait_seconds": 60, "crowd_calls": 2, "dry_run": False}
    clock.now = msgs[0]["time"]
    p = xm.XuanquPlugin(Ctx(), cfg)
    # 真 bot 当时发的消息 -> 它引用的那条；回放里有人引用真 bot 的话，换成假 bot 引用同一条的那句
    real = {str(m["message_id"]): next((x["data"]["id"] for x in m["message"] if x["type"] == "reply"), "")
            for m in hist if str(m["user_id"]) == SELF}
    for i, m in enumerate(msgs):
        clock.now = m["time"]
        for seg in m["message"]:
            rid = str(seg["data"].get("id")) if seg["type"] == "reply" else ""
            if rid in real and m["time"] >= t0:
                fake = [k for k, v in BOT.replied.items() if v == str(real[rid])]
                if fake:
                    seg["data"]["id"] = fake[-1]
                    print(f"  (引用真 bot 的 {rid} 换成假 bot 的 {fake[-1]})")
        replay = m["time"] >= t0
        if replay:
            print(show(m))
        await p.on_group_message(Ev(m))
        if not replay:
            for x in p.buffers[GID]:
                x.judged = True
            p.urgent.pop(GID, None)
            p.calls.pop(GID, None)
            continue
        nxt = msgs[i + 1]["time"] if i + 1 < len(msgs) else m["time"] + 150
        t = m["time"]
        while t < nxt:
            clock.now = t
            await step(p)
            t += 2


async def step(p):
    """_tick 的同步版：到点就判，等它判完再往下走。"""
    now = clock.now
    buf = p.buffers.get(GID, [])
    if GID in p.urgent and now - p.last_review.get(GID, 0) >= xm.URGENT_GAP:
        u = p.urgent.pop(GID)
        p.last_review[GID] = now
        p.busy.add(GID)
        print(f"  -- {dt.datetime.fromtimestamp(now):%H:%M:%S} 立刻审")
        await p._review(GID, u)
        return
    ripe = [m for m in buf if not m.judged and now - m.t >= 60]
    if ripe and (len(ripe) >= 20 or now - ripe[0].t >= 120):
        p.busy.add(GID)
        print(f"  -- {dt.datetime.fromtimestamp(now):%H:%M:%S} 攒批 {len(ripe)} 条")
        await p._judge(GID, ripe[:xm.MAX_TARGETS])


asyncio.run(main())
