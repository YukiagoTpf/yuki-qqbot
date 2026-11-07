"""手动整理某个群某一天：补跑、试改过的提示词用。真调 DeepSeek，写的是数据目录里的 groups.yaml。在 astrbot 容器里跑：

  python3 analyze.py <插件父目录> <数据目录> <群号> <日期 YYYY-MM-DD> [群聊记录 json]

- 数据目录：放 groups.yaml（知识库）和 chat/<群号>/<日期>.jsonl（落盘的聊天）。试提示词时用线上 data/astrbot 的副本，
  别直接写线上的；真要补跑线上的，数据目录给 /AstrBot/data，聊天记录在 /AstrBot/data/plugin_data/astrbot_plugin_groupkb。
- 群聊记录 json：get_group_msg_history 拉的（count 3000 大约两天），当成 NapCat 的历史用，和落盘的合在一起。
- 环境变量：DEEPSEEK_API_KEY，SELF（bot 的 QQ 号）。整理用开思考的 deepseek-flash，和线上一样。
"""

import asyncio
import json
import os
import sys
import types
from datetime import date
from pathlib import Path

ROOT, DATA, GID, DAY = sys.argv[1:5]
HIST = json.loads(Path(sys.argv[5]).read_text("utf-8")) if len(sys.argv) > 5 else []
sys.path.insert(0, ROOT)
from openai import AsyncOpenAI  # noqa: E402

from astrbot.api.star import StarTools  # noqa: E402

DATA_DIR = Path(DATA)
StarTools.get_data_dir = classmethod(lambda cls, plugin_name=None: DATA_DIR / "plugin_data" / "astrbot_plugin_groupkb")
import astrbot_plugin_groupkb.main as gm  # noqa: E402

client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com/v1")


class Ctx:
    async def llm_generate(self, chat_provider_id, prompt, system_prompt=None, **kw):
        msgs = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        r = await client.chat.completions.create(model="deepseek-flash", messages=msgs)
        out = r.choices[0].message.content
        print(f"  模型（送了 {len(prompt)} 字）：{out[:800]}")
        return types.SimpleNamespace(completion_text=out)


class Bot:
    async def call_action(self, action, **kw):
        if action == "get_group_msg_history":
            return {"messages": HIST}
        if action == "get_group_info":
            return {}
        raise RuntimeError(action)


async def main():
    (DATA_DIR / "plugin_data" / "astrbot_plugin_groupkb").mkdir(parents=True, exist_ok=True)
    p = gm.GroupKB(Ctx(), {"groups": [GID], "provider": "deepseek-flash"})
    p.kb_path = DATA_DIR / "groups.yaml"
    p.self_id = os.environ.get("SELF", "")
    p.bot = Bot()  # 没给群聊记录就当 NapCat 没有历史，只用落盘的
    added = await p.run_day(GID, date.fromisoformat(DAY))
    print(json.dumps(added, ensure_ascii=False, indent=1))


asyncio.run(main())
