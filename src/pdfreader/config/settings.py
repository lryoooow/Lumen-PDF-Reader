"""用户设置持久化。

将用户偏好（最近文件、阅读位置、窗口状态、阅读模式等）保存为 JSON，跨会话恢复。
位置：用户配置目录 ~/.pdfreader/settings.json

读取时逐字段校验类型：设置文件被手工改坏 / 来自旧版本时，坏字段回退默认值，
而不是把错误类型带进程序（例如 ``recent_files: null`` 会在启动时崩溃）。
写入先写临时文件再原子替换，避免写到一半崩溃留下半截 JSON。
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

__all__ = [
    "Bookmark",
    "ReadingPosition",
    "Settings",
    "SettingsStore",
    "default_settings_path",
]

# 记住阅读位置的文件数上限（超出按最近使用淘汰）
MAX_READING_POSITIONS = 200
# 有书签的文件数上限（超出淘汰最久未改动的）
MAX_BOOKMARKED_FILES = 500


def default_settings_path() -> Path:
    """返回默认设置文件路径(用户主目录下)。"""
    return Path.home() / ".pdfreader" / "settings.json"


@dataclass(frozen=True)
class ReadingPosition:
    """某个文件上次的阅读位置。"""

    page: int
    offset: float = 0.0  # 视口顶边在页内的相对位置 0..1
    zoom: float = 0.0  # 0 表示沿用默认（适应宽度）


@dataclass(frozen=True)
class Bookmark:
    """用户书签：只存在本机设置里，不写进 PDF（只读 / 加密文档也能加，也不必另存）。"""

    page: int
    offset: float = 0.0  # 视口顶边在页内的相对位置 0..1（与阅读位置同一套）
    title: str = ""


@dataclass
class Settings:
    """用户设置。"""

    recent_files: list[str] = field(default_factory=list)
    window_width: int = 1024
    window_height: int = 768
    window_maximized: bool = False
    theme: str = "light"  # "light" | "dark" | "system"（跟随系统）
    reading_mode: str = "normal"  # "normal" | "night" | "sepia"
    highlight_color: str = "#F5D64E"  # 高亮颜色（十六进制）
    sidebar_visible: bool = True
    # 文件路径 → [页, 页内偏移, 缩放]，插入顺序即最近使用顺序
    reading_positions: dict[str, list[float]] = field(default_factory=dict)
    # 文件路径 → [[页, 页内偏移, 标题], ...]（按页序）
    bookmarks: dict[str, list[list[object]]] = field(default_factory=dict)

    def add_recent_file(self, path: str | Path, *, max_count: int = 10) -> None:
        """添加最近文件（去重、置顶、限制数量）。"""
        p = str(path)
        if p in self.recent_files:
            self.recent_files.remove(p)
        self.recent_files.insert(0, p)
        self.recent_files = self.recent_files[:max_count]

    def remove_recent_file(self, path: str | Path) -> None:
        """从最近文件中移除（文件已被删除 / 移动时）。"""
        p = str(path)
        if p in self.recent_files:
            self.recent_files.remove(p)

    def remember_position(self, path: str | Path, position: ReadingPosition) -> None:
        key = str(path)
        self.reading_positions.pop(key, None)
        self.reading_positions[key] = [position.page, position.offset, position.zoom]
        while len(self.reading_positions) > MAX_READING_POSITIONS:
            self.reading_positions.pop(next(iter(self.reading_positions)))

    def position_for(self, path: str | Path) -> ReadingPosition | None:
        raw = self.reading_positions.get(str(path))
        if not isinstance(raw, list) or len(raw) < 2:
            return None
        try:
            zoom = float(raw[2]) if len(raw) > 2 else 0.0
            return ReadingPosition(int(raw[0]), float(raw[1]), zoom)
        except (TypeError, ValueError):
            return None

    def bookmarks_for(self, path: str | Path) -> list[Bookmark]:
        result: list[Bookmark] = []
        for raw in self.bookmarks.get(str(path), []):
            try:
                page, offset, title = raw
                result.append(Bookmark(int(page), float(offset), str(title)))  # type: ignore[call-overload,arg-type]
            except (TypeError, ValueError):
                continue
        return result

    def set_bookmarks(self, path: str | Path, marks: list[Bookmark]) -> None:
        key = str(path)
        self.bookmarks.pop(key, None)
        if marks:
            ordered = sorted(marks, key=lambda b: (b.page, b.offset))
            self.bookmarks[key] = [[b.page, round(b.offset, 4), b.title] for b in ordered]
        while len(self.bookmarks) > MAX_BOOKMARKED_FILES:
            self.bookmarks.pop(next(iter(self.bookmarks)))


def _coerce_bookmarks(value: object) -> dict[str, list[list[object]]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[list[object]]] = {}
    for key, items in value.items():
        if not isinstance(items, list):
            continue
        good = [
            [int(it[0]), float(it[1]), it[2]]
            for it in items
            if isinstance(it, list)
            and len(it) == 3
            and isinstance(it[0], int)
            and not isinstance(it[0], bool)
            and isinstance(it[1], (int, float))
            and isinstance(it[2], str)
        ]
        if good:
            result[str(key)] = good
    return result


def _coerce(name: str, value: object, default: object) -> object:
    """把 JSON 里的值校正为字段默认值的类型；不合法时返回默认值。"""
    if name == "bookmarks":
        return _coerce_bookmarks(value)
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        if isinstance(value, list):
            return [str(v) for v in value if isinstance(v, str)]
        return default
    if isinstance(default, dict):
        if isinstance(value, dict):
            return {
                str(k): list(v)
                for k, v in value.items()
                if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)
            }
        return default
    return default


class SettingsStore:
    """设置读写。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Settings:
        """加载设置；文件不存在、损坏或字段类型不对时对应部分回退默认值。"""
        defaults = Settings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defaults
        except (OSError, ValueError):
            return defaults
        if not isinstance(data, dict):
            return defaults
        values = {
            f.name: _coerce(f.name, data[f.name], getattr(defaults, f.name))
            for f in fields(Settings)
            if f.name in data
        }
        return Settings(**values)  # type: ignore[arg-type]

    def save(self, settings: Settings) -> None:
        """保存设置（自动创建目录；先写临时文件再原子替换）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.replace(self._path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
