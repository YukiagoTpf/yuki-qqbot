"""判定的回归测试：把构造的炫区（POS）和容易误判的反例（NEG）混进真实群聊，看命中和误杀。
在 astrbot 容器里跑：python3 eval_judge.py <插件父目录> <群聊记录 json> <输出目录> think[,fast]
群聊记录用 get_group_msg_history 拉；DEEPSEEK_API_KEY、SELF（bot 的 QQ 号）从环境变量给。"""

import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, sys.argv[1])
from openai import AsyncOpenAI  # noqa: E402

from astrbot_plugin_xuanqu.core.prompts import JUDGE_SYSTEM, parse_json  # noqa: E402
from astrbot_plugin_xuanqu.core.render import Codes, Msg, Renderer  # noqa: E402

HIST, OUT = sys.argv[2], sys.argv[3]
client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com/v1")
SEM = asyncio.Semaphore(8)
SELF = os.environ.get("SELF", "")

POS = [
    "刚和对象看完电影出来，她非要拍照，烦死了",
    "女朋友给我织了条围巾，丑是丑了点",
    "昨晚陪npy逛到十二点，腿废了",
    "好烦啊，女朋友天天查岗",
    "我女朋友说我打游戏太菜了，你们说怎么办",
    "她回我「嗯嗯」是什么意思啊，在线等挺急的",
    "前女友今天又给我发消息了，删了",
    "以前我前任也喜欢这个牌子",
    "年终奖发了六个月，扣完税心疼",
    "offer 下来了，base 45w，大家觉得要不要去",
    "今天转正了，到手一个月3w5，终于不用吃泡面了",
    "女朋友生日送什么好啊，预算两千",
    "和老婆吵架了，今晚睡沙发",
    "周末带女朋友回家见家长，紧张",
    "学妹又给我送奶茶了，怎么拒绝比较好",
    "又有个妹子加我微信，烦",
    "被女朋友拉去拍情侣写真，累死",
    "对象送的机械键盘到了，手感一般",
    "我们在一起三周年了",
    "昨晚她在我家住的，早上起来给我做了早饭",
    "你们看她这是什么意思[图：聊天记录，微信；截图的人在和备注「宝宝」的人聊，对方像女生，互称宝宝，说晚安、想你，很亲密；摘抄：「宝宝晚安」「想你了」「明天见面吗」]",
    "周末去迪士尼了[图：合照，两个人，一男一女，牵手，像情侣]",
    "唉又是被扣税的一个月[图：收入，工资条，实发 38000 元]",
    "七夕收到的[图：礼物，一束花和一张卡片，写着「送给最爱的你」]",
    "[合并转发：小美：在干嘛呀 / 我：想你 / 小美：讨厌～明天一起吃饭吧]",
    "女友送的switch，不知道玩啥，推荐几个游戏",
    "单身的不懂，谈恋爱真的好累",
    "我跟女朋友都是二次元，她也喜欢芙莉莲",
    "今天去相亲了，女生对我挺满意的，约了下周",
    "刚跟女朋友分手，有点难受",
    "妹妹天天追着要来找我玩，我都说没空了，烦",
]
NEG = [
    "好想有个女朋友啊",
    "我也好想和coser小女友亲嘴",
    "我老婆芙莉莲今天也很可爱",
    "老婆（初音）的新手办到了",
    "单身二十五年了，谁来救救我",
    "我室友的女朋友今天来宿舍了，我只能出门",
    "工资三千五，房租就两千",
    "又加班到十点，打工人",
    "我妈今天给我寄了腊肉",
    "我姐结婚了，随了两千份子钱",
    "女朋友？那是什么，能吃吗",
    "今天和同事去吃火锅了",
    "公司女同事请大家喝奶茶",
    "你们有对象的都给我爬",
    "我女朋友在二次元，你们看不到",
    "我要是有女朋友就好了",
    "这游戏女角色太好看了，我老婆",
    "刚买了个新键盘，手感不错",
    "这个新闻里那男的年薪百万还被裁了",
    "[图：梗图，熊猫头配文「我要炫了」]",
    "出了出了[图：游戏，原神抽卡结果，出了五星角色]",
    "我妈又催我吃饭[图：聊天记录，QQ；截图的人在和备注「老妈」的人聊，对方是母亲，问吃饭了没]",
    "朋友送了我一个袋子",
    "谁懂啊今天被学校的猫亲了",
    "我前女友是AI，昨天被删档了",
    "我打算明年考研，女朋友什么的不重要",
    "你女朋友呢？哦你没有",
    "我的老婆们（指Vtuber）今天都在直播",
    "这番男主后宫也太多了吧",
    "梦到自己有女朋友了，醒来更难受",
    "两张聊天记录截图，内容是妹妹死皮赖脸追着阿强要来找他玩，阿强说没空",
    "阿强刚才发了他和女朋友的聊天记录，已经撤回了，你们没看到亏了",
    "他女朋友给他买了个switch，羡慕死了",
    "老王说他年终奖发了二十万，真的假的",
]


def load_history():
    msgs = []
    for m in json.load(open(HIST)):
        msgs.append(Msg(mid=str(m["message_id"]), uid=str(m["user_id"]), name="", role=m["sender"].get("role", "member"),
                        t=m["time"], segs=m["message"]))
    return [m for m in msgs if m.uid != SELF]


def fake(text: str, uid: str) -> Msg:
    return Msg(mid=f"case-{random.random()}", uid=uid, name="", role="member", t=0, segs=[{"type": "text", "data": {"text": text}}])


async def judge(before, targets, after, lookup, thinking):
    window = before + targets + after
    numbers = {m.mid: i + 1 for i, m in enumerate(window)}
    r = Renderer(Codes(SELF), numbers, lookup)
    lines = []
    for title, msgs in (("前文，只作参考", before), ("要判", targets), ("后文，只作参考", after)):
        if msgs:
            lines.append(f"【{title}】")
            lines += [r.line(numbers[m.mid], m) for m in msgs]
    extra = {} if thinking else {"thinking": {"type": "disabled"}}
    async with SEM:
        t = time.time()
        resp = await client.chat.completions.create(
            model="deepseek-flash", extra_body=extra,
            messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": "\n".join(lines)}])
        dt = time.time() - t
    data = parse_json(resp.choices[0].message.content) or {"hits": [], "bad": resp.choices[0].message.content}
    by_n = {numbers[m.mid]: m for m in targets}
    hits = []
    for h in data.get("hits") or []:
        try:
            m = by_n.get(int(h["n"]))
        except Exception:
            continue
        if m:
            hits.append((m, int(h.get("score", 0)), h.get("why", ""), h.get("roast", ""), r.content(m)))
    u = resp.usage
    return hits, (u.prompt_tokens, u.completion_tokens, dt), data.get("bad")


async def run(variant: str):
    thinking = variant == "think"
    random.seed(7)
    hist = load_history()
    lookup = {m.mid: m for m in hist}
    tasks, meta = [], []
    # 1. 真实群聊按批判，算误杀
    for i in range(15, len(hist) - 20, 20):
        tasks.append(judge(hist[i - 15:i], hist[i:i + 20], hist[i + 20:i + 40], lookup, thinking))
        meta.append(("real", None))
    # 2. 构造的消息插进真实群聊里
    for label, cases in (("pos", POS), ("neg", NEG)):
        for text in cases:
            i = random.randrange(15, len(hist) - 30)
            targets = hist[i:i + 9]
            case = fake(text, f"9{random.randrange(10**8)}")
            targets.insert(random.randrange(len(targets) + 1), case)
            tasks.append(judge(hist[i - 15:i], targets, hist[i + 9:i + 24], lookup, thinking))
            meta.append((label, case))
    results = await asyncio.gather(*tasks)
    tag = "think" if thinking else "fast"
    real_hits, pos_scores, neg_scores, usage, bad = [], [], [], [], 0
    for (label, case), (hits, u, b) in zip(meta, results):
        usage.append(u)
        bad += bool(b)
        for m, score, why, roast, text in hits:
            if case is not None and m is case:
                (pos_scores if label == "pos" else neg_scores).append((score, text, why, roast))
            elif m.mid in lookup:
                real_hits.append((score, text, why))
        if case is not None and not any(m is case for m, *_ in hits):
            (pos_scores if label == "pos" else neg_scores).append((0, plain_text(case), ""))
    n_real = sum(1 for lb, _ in meta if lb == "real") * 20 + len(POS + NEG) * 9
    out = {
        "variant": tag,
        "calls": len(tasks), "bad_json": bad,
        "avg_prompt_tokens": sum(u[0] for u in usage) / len(usage),
        "avg_completion_tokens": sum(u[1] for u in usage) / len(usage),
        "avg_latency": sum(u[2] for u in usage) / len(usage),
        "real_msgs_judged": n_real,
        "real_hits_ge80": sorted([h for h in real_hits if h[0] >= 80], reverse=True),
        "real_hits_70_79": sorted([h for h in real_hits if 70 <= h[0] < 80], reverse=True),
        "real_hits_50_69_count": sum(1 for h in real_hits if h[0] < 70),
        "pos": pos_scores, "neg": neg_scores,
    }
    json.dump(out, open(f"{OUT}/{tag}.json", "w"), ensure_ascii=False, indent=1)
    pos_hit = sum(1 for s, *_ in pos_scores if s >= 80)
    neg_hit = sum(1 for s, *_ in neg_scores if s >= 80)
    print(f"[{tag}] calls={len(tasks)} bad_json={bad} tokens in/out={out['avg_prompt_tokens']:.0f}/{out['avg_completion_tokens']:.0f} "
          f"latency={out['avg_latency']:.1f}s | pos>=80 {pos_hit}/{len(POS)} | neg>=80 {neg_hit}/{len(NEG)} | "
          f"real>=80 {len(out['real_hits_ge80'])}/{n_real}, real70-79 {len(out['real_hits_70_79'])}")


def plain_text(m):
    return "".join(s["data"].get("text", "") for s in m.segs)


async def main():
    await asyncio.gather(*(run(v) for v in sys.argv[4].split(",")))


asyncio.run(main())
