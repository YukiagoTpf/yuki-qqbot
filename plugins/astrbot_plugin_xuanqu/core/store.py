"""插件数据目录里的 JSON 文件读写。"""

import json
import os
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def save_json(path: Path, data) -> None:
    """先写临时文件再替换，进程中途被杀也不会留下写了一半的文件。"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)
