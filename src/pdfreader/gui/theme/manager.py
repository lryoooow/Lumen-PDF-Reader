"""主题管理器。

持有当前配色板，向控件提供按主题着色的图标，向 :class:`QApplication`
应用全局样式表，并在切换浅/深色时发出 ``changed`` 信号，让各控件刷新图标。
以模块级单例 :func:`get_theme` 供全局访问。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from pdfreader.gui.theme import icons as _icons
from pdfreader.gui.theme.palette import LIGHT, TOKENS, Palette, palette_for
from pdfreader.gui.theme.stylesheet import build_stylesheet

__all__ = ["ThemeManager", "get_theme", "serif_font"]


def _families(spec: str) -> list[str]:
    return [part.strip().strip('"') for part in spec.split(",") if part.strip()]


def _ui_font() -> QFont:
    """界面字体：按令牌里的候选族名依次回退（设计稿 Inter → Segoe UI → 微软雅黑）。"""
    font = QFont()
    font.setFamilies(_families(TOKENS.font_family))
    font.setPixelSize(13)
    return font


def serif_font(pixel_size: int) -> QFont:
    """正文衬线体（标题、引文）：Source Serif 4 / Noto Serif SC，缺失时回退到系统衬线体。"""
    font = QFont()
    font.setFamilies(_families(TOKENS.serif_family))
    font.setPixelSize(pixel_size)
    return font


# 有效的主题模式："system" 跟随操作系统浅/深色
MODES = ("light", "dark", "system", "contrast")


class ThemeManager(QObject):
    """全局主题：配色 + 图标 + 样式表。

    区分**模式**（用户选择：light / dark / system）与**实际配色**
    （system 模式解析出的具体 light/dark 配色板）。选择"跟随系统"时会
    监听操作系统主题变化并自动切换。

    Signals:
        changed(str): 实际配色切换后发出具体名（"light" / "dark"）
    """

    changed = Signal(str)

    def __init__(self, name: str = "light") -> None:
        super().__init__()
        self._mode: str = name if name in MODES else "light"
        self._palette: Palette = palette_for(self._resolve(self._mode))
        self._hints_connected = False

    # ------------------------------------------------------------------ 状态
    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def name(self) -> str:
        """当前**实际**配色名（"light" / "dark"）。"""
        return self._palette.name

    @property
    def mode(self) -> str:
        """当前**模式**（"light" / "dark" / "system"）。"""
        return self._mode

    @property
    def is_dark(self) -> bool:
        return self._palette.is_dark

    # ------------------------------------------------------------------ 系统主题
    @staticmethod
    def _system_scheme() -> str:
        """探测操作系统当前浅/深色（不可用时回退浅色）。"""
        app = QGuiApplication.instance()
        if app is not None:
            with contextlib.suppress(Exception):
                if app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                    return "dark"
        return "light"

    def _resolve(self, mode: str) -> str:
        """把模式解析为具体配色名。"""
        if mode == "system":
            return self._system_scheme()
        return mode if mode in ("light", "dark", "contrast") else "light"

    def _connect_hints(self) -> None:
        """首次需要时连接系统主题变化信号（仅连一次）。"""
        if self._hints_connected:
            return
        app = QGuiApplication.instance()
        if app is None:
            return
        with contextlib.suppress(Exception):
            app.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
            self._hints_connected = True

    def _on_system_scheme_changed(self, _scheme: object = None) -> None:
        if self._mode != "system":
            return
        resolved = self._resolve("system")
        if resolved == self._palette.name:
            return
        self._palette = palette_for(resolved)
        self.apply()
        self.changed.emit(resolved)

    # ------------------------------------------------------------------ 图标
    def icon(self, name: str, *, checkable: bool = False, size: int = 22) -> QIcon:
        """当前主题下的工具图标；checkable 时勾选态自动用主题色。"""
        p = self._palette
        on = p.icon_active if checkable else None
        return _icons.render_icon(name, p.icon, on_color=on, size=size)

    def colored_icon(self, name: str, color: str, *, size: int = 22) -> QIcon:
        return _icons.render_icon(name, color, size=size)

    def app_icon(self) -> QIcon:
        return _icons.app_icon(LIGHT.brand_from, LIGHT.brand_to)

    def logo_pixmap(self, size: int):  # type: ignore[no-untyped-def]
        p = self._palette
        return _icons.app_logo_pixmap(size, p.brand_from, p.brand_to)

    # ------------------------------------------------------------------ 应用 / 切换
    def apply(self, app: QApplication | None = None) -> None:
        """把当前主题应用到 QApplication（Fusion 基底 + 调色板 + 样式表 + 字体）。"""
        app = app or QApplication.instance()
        if app is None:
            return
        if app.style() is None or app.style().objectName().lower() != "fusion":
            app.setStyle("Fusion")  # 一致、可预测、易主题化的基底样式
        app.setFont(_ui_font())
        # 下拉菜单 / 组合框用淡入展开（对应设计稿 dropIn 动效）
        for effect in (
            Qt.UIEffect.UI_AnimateMenu,
            Qt.UIEffect.UI_FadeMenu,
            Qt.UIEffect.UI_AnimateCombo,
        ):
            QApplication.setEffectEnabled(effect, True)
        app.setPalette(self._build_qpalette())
        app.setStyleSheet(build_stylesheet(self._palette))

    def _build_qpalette(self) -> QPalette:
        """据当前配色板构造 QPalette，让未被 QSS 覆盖处（原生对话框等）也协调。"""
        p = self._palette
        pal = QPalette()
        c = QColor

        def setc(role: QPalette.ColorRole, value: str) -> None:
            pal.setColor(QPalette.ColorGroup.Active, role, c(value))
            pal.setColor(QPalette.ColorGroup.Inactive, role, c(value))

        setc(QPalette.ColorRole.Window, p.window)
        setc(QPalette.ColorRole.WindowText, p.text)
        setc(QPalette.ColorRole.Base, p.surface)
        setc(QPalette.ColorRole.AlternateBase, p.surface_hover)
        setc(QPalette.ColorRole.Text, p.text)
        setc(QPalette.ColorRole.Button, p.surface)
        setc(QPalette.ColorRole.ButtonText, p.text)
        setc(QPalette.ColorRole.BrightText, p.on_accent)
        setc(QPalette.ColorRole.Highlight, p.accent)
        setc(QPalette.ColorRole.HighlightedText, p.on_accent)
        setc(QPalette.ColorRole.ToolTipBase, p.surface)
        setc(QPalette.ColorRole.ToolTipText, p.text)
        setc(QPalette.ColorRole.PlaceholderText, p.text_muted)
        setc(QPalette.ColorRole.Link, p.accent)
        # 禁用态
        for role, value in (
            (QPalette.ColorRole.WindowText, p.text_muted),
            (QPalette.ColorRole.Text, p.text_muted),
            (QPalette.ColorRole.ButtonText, p.text_muted),
        ):
            pal.setColor(QPalette.ColorGroup.Disabled, role, c(value))
        return pal

    def set_mode(self, mode: str) -> None:
        """设置主题模式（light / dark / system）并即时应用。"""
        mode = mode if mode in MODES else "light"
        self._mode = mode
        self._connect_hints()  # system 模式需要监听系统变化
        resolved = self._resolve(mode)
        self._palette = palette_for(resolved)
        self.apply()
        self.changed.emit(resolved)

    # 兼容旧名：按具体配色名设置（等价于 set_mode）
    def set_theme(self, name: str) -> None:
        self.set_mode(name)

    def toggle(self) -> None:
        """在浅色 / 深色间切换（明确切换，不含 system）。"""
        self.set_mode("light" if self.is_dark else "dark")


_instance: ThemeManager | None = None


def get_theme() -> ThemeManager:
    """全局主题单例。"""
    global _instance
    if _instance is None:
        _instance = ThemeManager()
    return _instance
