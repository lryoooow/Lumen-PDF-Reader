"""用户设置持久化。

将用户偏好(最近文件、缩放、窗口状态等)保存为 JSON,跨会话恢复。
位置:用户配置目录 ~/.pdfreader/settings.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["Settings", "SettingsStore", "default_settings_path"]


def default_settings_path() -> Path:
    """返回默认设置文件路径(用户主目录下)。"""
    return Path.home() / ".pdfreader" / "settings.json"


@dataclass
class Settings:
    """用户设置。

    Attributes:
        recent_files: 最近打开的文件路径列表
        default_zoom: 默认缩放系数
        window_width / window_height: 上次窗口尺寸
        window_maximized: 是否最大化
    """

    recent_files: list[str] = field(default_factory=list)
    default_zoom: float = 1.5
    window_width: int = 1024
    window_height: int = 768
    window_maximized: bool = False

    def add_recent_file(self, path: str | Path, *, max_count: int = 10) -> None:
        """添加最近文件(去重、置顶、限制数量)。"""
        p = str(path)
        if p in self.recent_files:
            self.recent_files.remove(p)
        self.recent_files.insert(0, p)
        self.recent_files = self.recent_files[:max_count]


class SettingsStore:
    """设置读写。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Settings:
        """加载设置;文件不存在或损坏时返回默认值。"""
        if not self._path.exists():
            return Settings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError, ValueError):
            return Settings()

    def save(self, settings: Settings) -> None:
        """保存设置(自动创建目录)。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
        )
