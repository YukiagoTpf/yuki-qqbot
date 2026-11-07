#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""提交前查一遍：仓库是公开的，群号、QQ 号、群名、群友外号、密钥都不能进去。

不靠猜（「像不像 QQ 号」），而是从只在本机的文件里把这些值读出来，再看要提交的文件里有没有：
.env、data/astrbot/cmd_config.json（白名单、管理员、模型的 key）、data/astrbot/config/*.json（各插件配置里的
群号和带 key / secret / token 的字段）、data/astrbot/groups.yaml（群号、群名、群友 QQ 号和外号）。

用法：scripts/check-public.py [文件……]，不给文件就查 git 会提交的所有文件（已跟踪的加上没被忽略的新文件，
子模块除外，它有自己的仓库）。查到了退出码是 1。make check-public 和 git 的 pre-commit 钩子都调它。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "astrbot"
SECRET_KEY = re.compile(r"key|secret|token|password|cookie", re.I)
NUMBER = re.compile(r"\b\d{6,12}\b")


def read_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError):
        return None


def walk(value, key=""):
    """(键, 值)：嵌套的字典和列表都展开，列表里的值沿用列表的键。"""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, str(k))
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, key)
    else:
        yield key, value


def private_values() -> dict[str, str]:
    """值 -> 它是什么（报错时说明用，不打印值本身以外的东西）。"""
    found: dict[str, str] = {}

    def add(value, what):
        value = str(value).strip()
        if len(value) >= 2 and value not in found:
            found[value] = what

    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.startswith("#") and (k.endswith(("TOKEN", "ACCOUNT", "HOSTNAME", "MAC")) or SECRET_KEY.search(k)):
                add(v, f".env 的 {k}")
    cmd = read_json(DATA / "cmd_config.json") or {}
    for k, v in walk(cmd):
        if k in ("admins_id", "id_whitelist"):
            for n in NUMBER.findall(str(v)):
                add(n, f"cmd_config 的 {k}")
        elif SECRET_KEY.search(k) and isinstance(v, str) and len(v) >= 12:
            add(v, f"cmd_config 的 {k}")
    for path in sorted((DATA / "config").glob("*.json")):
        for k, v in walk(read_json(path) or {}):
            if isinstance(v, str) and SECRET_KEY.search(k) and len(v) >= 12:
                add(v, f"{path.name} 的 {k}")
            elif "group" in k.lower() or "admin" in k.lower():
                for n in NUMBER.findall(str(v)):
                    add(n, f"{path.name} 的 {k}")
    groups = DATA / "groups.yaml"
    if groups.exists():
        import yaml  # uv 按文件头装好

        for gid, info in (yaml.safe_load(groups.read_text("utf-8")) or {}).items():
            add(gid, "groups.yaml 的群号")
            info = info if isinstance(info, dict) else {}
            add(info.get("name") or "", "groups.yaml 的群名")
            for uid, names in (info.get("members") or {}).items():
                add(uid, "groups.yaml 的群友 QQ 号")
                for name in names if isinstance(names, list) else [names]:
                    add(name, "groups.yaml 的群友外号")
    return found


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    subs = subprocess.run(
        ["git", "config", "--file", ".gitmodules", "--get-regexp", r"\.path$"], cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()[1::2]
    files = [f for f in out.split("\0") if f and not any(f == s or f.startswith(s + "/") for s in subs)]
    return [ROOT / f for f in files if (ROOT / f).is_file()]


def main() -> None:
    values = private_values()
    if not values:
        sys.exit("check-public: 一个私有值都没读到，data/ 或 .env 不在？不能保证干净")
    files = [Path(f).resolve() for f in sys.argv[1:]] or tracked_files()
    bad = 0
    for path in files:
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for value, what in values.items():
                # 纯数字要整词匹配，免得长数字里碰巧含着一段
                hit = re.search(rf"(?<!\d){re.escape(value)}(?!\d)", line) if value.isdigit() else value in line
                if hit:
                    bad += 1
                    print(f"{path.relative_to(ROOT)}:{lineno}: 有{what}")
    if bad:
        sys.exit(f"check-public: {bad} 处不能公开，改掉再提交")
    print(f"check-public: 查了 {len(files)} 个文件、{len(values)} 个私有值，干净")


if __name__ == "__main__":
    main()
