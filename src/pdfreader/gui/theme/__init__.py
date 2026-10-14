"""主题系统：配色令牌、图标、样式表、主题管理器。

统一管理浅色 / 深色两套视觉，为整个 GUI 提供一致的现代观感。
"""

from __future__ import annotations

from pdfreader.gui.theme import icons  # noqa: F401  (确保子模块先于 manager 就绪)
from pdfreader.gui.theme.manager import ThemeManager, get_theme

# 按依赖顺序导入子模块（后者依赖前者），避免循环导入
from pdfreader.gui.theme.palette import DARK, LIGHT, TOKENS, Palette, palette_for
from pdfreader.gui.theme.stylesheet import build_stylesheet

__all__ = [
    "DARK",
    "LIGHT",
    "TOKENS",
    "Palette",
    "ThemeManager",
    "build_stylesheet",
    "get_theme",
    "palette_for",
]
