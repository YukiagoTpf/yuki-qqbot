"""按群的知识库：AstrBot 数据目录下的 groups.yaml（宿主机上是 data/astrbot/groups.yaml），记群里的黑话、规矩和群友外号，
不进仓库。格式见仓库根目录的 groups.example.yaml。改了不用重启：文件的修改时间变了，下次用到时重读。"""

from pathlib import Path

import yaml

from astrbot.api import logger


class Groups:
    def __init__(self, path: Path):
        self.path = path
        self.mtime: float | None = None
        self.data: dict = {}

    def get(self, gid: str) -> dict:
        """这个群的那一段，没有返回空字典。读不了、格式不对就当没有，不影响判。"""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self.mtime, self.data = None, {}
            return {}
        if mtime != self.mtime:
            self.mtime = mtime
            try:
                data = yaml.safe_load(self.path.read_text("utf-8")) or {}
                self.data = {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
            except (OSError, yaml.YAMLError) as e:
                logger.warning(f"[xuanqu] 读 {self.path.name} 失败：{e}")
                self.data = {}
        return self.data.get(str(gid)) or {}

    def notes(self, gid: str) -> list[str]:
        notes = self.get(gid).get("notes") or []
        return [str(n).strip() for n in (notes if isinstance(notes, list) else [notes]) if str(n).strip()]

    def nicknames(self, gid: str) -> dict[str, list[str]]:
        """QQ 号 -> 外号（群名片以外大家怎么叫他）。"""
        members = self.get(gid).get("members") or {}
        if not isinstance(members, dict):
            return {}
        out = {}
        for uid, names in members.items():
            names = names if isinstance(names, list) else [names]
            out[str(uid)] = [str(n).strip() for n in names if str(n).strip()]
        return out
