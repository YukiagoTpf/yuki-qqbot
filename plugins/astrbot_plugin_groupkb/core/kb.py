"""按群的知识库 groups.yaml（AstrBot 数据目录下，宿主机上是 data/astrbot/groups.yaml）的读写。格式见仓库根目录的
groups.example.yaml。手写的和自动加的混在一起，自动整理只往里加，不删不改；注释每次重写，数据都保留。"""

import os
from pathlib import Path

import yaml

HEADER = """\
# 按群的知识库，键是群号，格式见仓库根目录的 groups.example.yaml。不进仓库。
# 群知识库插件每天凌晨读前一天的聊天，往 members（外号）、notes（黑话、梗）、events（留档）里加东西，只加不删。
# 手改也行，改了不用重启；自动加错的外号、黑话直接删掉。注释会被重写，数据会保留。

"""
NAME_LEN = (2, 8)  # 外号的长短
NOTE_LEN = 40
EVENT_LEN = 60


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 不是按群号分的字典")
    return {str(k): v if isinstance(v, dict) else {} for k, v in data.items()}


def save(path: Path, data: dict) -> None:
    """先写临时文件再替换：杀炫区随时在读，不能让它读到写了一半的。"""
    text = HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


def nicknames(entry: dict) -> dict[str, list[str]]:
    members = entry.get("members") or {}
    return {str(k): [str(n) for n in (v if isinstance(v, list) else [v])] for k, v in members.items()} if isinstance(members, dict) else {}


def notes(entry: dict) -> list[str]:
    n = entry.get("notes") or []
    return [str(x) for x in (n if isinstance(n, list) else [n])]


def add_nickname(entry: dict, uid: str, name: str, card: str = "") -> bool:
    """记一个外号。太短太长、是群名片（的一部分）、已经有了的不记。记了返回 True。"""
    name = name.strip()
    if not NAME_LEN[0] <= len(name) <= NAME_LEN[1] or (card and name in card):
        return False
    members = nicknames(entry)
    if any(name in names for names in members.values()):
        return False  # 已经记给了别人（或者他自己），拿不准就不动
    members.setdefault(str(uid), []).append(name)
    entry["members"] = members
    return True


def add_note(entry: dict, note: str, cap: int) -> bool:
    note = note.strip()[:NOTE_LEN]
    have = notes(entry)
    if not note or len(have) >= cap or any(note in h or h in note for h in have):
        return False
    entry["notes"] = [*have, note]
    return True


def set_events(entry: dict, day: str, events: list[str]) -> int:
    """当天的留档整段替换（重跑同一天不会叠两份），旧的按日期排在前面。"""
    events = [e.strip()[:EVENT_LEN] for e in events if e and e.strip()]
    old = entry.get("events") if isinstance(entry.get("events"), dict) else {}
    old = {str(k): v for k, v in old.items() if str(k) != day}
    if events:
        old[day] = events
    entry["events"] = dict(sorted(old.items()))
    return len(events)
