"""欢迎页（未打开文档时，Lumen 设计稿）。

左对齐的一栏（最大宽 576）：品牌方块 → 衬线大标题「专注阅读，*安静*思考。」→ 说明 →
虚线拖放区「打开 PDF」→「最近文件」列表（悬停显示「打开 →」）。进入时各元素依次自下淡入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import ThemeManager
from pdfreader.gui.theme.manager import serif_font
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["WelcomeWidget"]

_MAX_RECENT = 8
_COLUMN_WIDTH = 576


class _RecentRow(QPushButton):
    """最近文件行：文件图标 + 名称 + 悬停才出现的「打开 →」。"""

    def __init__(self, path: Path, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecentRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(str(path))
        self.path = path
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(12)
        self._icon = QLabel()
        self._icon.setPixmap(
            theme.colored_icon("file", theme.palette.text_muted, size=16).pixmap(16, 16)
        )
        self._name = QLabel(path.name)
        self._name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._hint = QLabel("打开 →")
        self._hint.setProperty("variant", "muted")
        self._hint.setVisible(False)
        lay.addWidget(self._icon)
        lay.addWidget(self._name, stretch=1)
        lay.addWidget(self._hint)
        self.setMinimumHeight(40)
        for child in (self._icon, self._name, self._hint):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().enterEvent(event)
        self._hint.setVisible(True)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().leaveEvent(event)
        self._hint.setVisible(False)


class WelcomeWidget(QWidget):
    """空状态欢迎页。

    Signals:
        openRequested: 点击「打开 PDF」
        openRecentRequested(str): 点击某个最近文件（路径）
    """

    openRequested = Signal()
    openRecentRequested = Signal(str)

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = theme
        self._rows: list[_RecentRow] = []
        self._build()
        theme.changed.connect(self._refresh_theme)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.addStretch(2)

        self._column = QWidget()
        self._column.setMaximumWidth(_COLUMN_WIDTH)
        self._column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        col = QVBoxLayout(self._column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._logo = QLabel()
        self._logo.setFixedSize(56, 56)
        col.addWidget(self._logo)
        col.addSpacing(24)

        self._title = QLabel()
        self._title.setObjectName("WelcomeTitle")
        self._title.setTextFormat(Qt.TextFormat.RichText)
        self._title.setFont(serif_font(36))
        col.addWidget(self._title)
        col.addSpacing(12)

        self._subtitle = QLabel("打开一篇文献开始阅读，或将 PDF 拖放到此处。")
        self._subtitle.setObjectName("WelcomeSubtitle")
        col.addWidget(self._subtitle)
        col.addSpacing(24)

        # 虚线拖放区（整块可点）
        self._open_btn = QPushButton()
        self._open_btn.setObjectName("DropZone")
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.setMinimumHeight(132)
        self._open_btn.clicked.connect(self.openRequested.emit)
        zone = QVBoxLayout(self._open_btn)
        zone.setContentsMargins(16, 24, 16, 24)
        zone.setSpacing(6)
        self._zone_icon = QLabel()
        self._zone_icon.setFixedSize(40, 40)
        self._zone_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_title = QLabel("打开 PDF")
        zone_title.setObjectName("DropZoneTitle")
        zone_hint = QLabel("Ctrl+O · 或拖放文件")
        zone_hint.setObjectName("DropZoneHint")
        for w in (self._zone_icon, zone_title, zone_hint):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            zone.addWidget(w, alignment=Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self._open_btn)

        # 最近文件
        self._recent_header = QLabel("最近文件")
        self._recent_header.setObjectName("RecentHeader")
        header_font = QFont(self._recent_header.font())
        header_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        header_font.setCapitalization(QFont.Capitalization.AllUppercase)
        self._recent_header.setFont(header_font)
        col.addSpacing(32)
        col.addWidget(self._recent_header)
        col.addSpacing(8)
        self._recent_box = QVBoxLayout()
        self._recent_box.setSpacing(0)
        col.addLayout(self._recent_box)

        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(self._column, stretch=100)
        center.addStretch(1)
        root.addLayout(center)
        root.addStretch(3)
        self._refresh_theme()

    # ------------------------------------------------------------------
    def set_recent(self, paths: list[str]) -> None:
        """填充最近文件（只列仍存在的，最多 8 个）；没有则隐藏该区域。"""
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        existing = [Path(p) for p in paths if Path(p).exists()][:_MAX_RECENT]
        for path in existing:
            row = _RecentRow(path, self._theme)
            row.clicked.connect(lambda _c=False, p=str(path): self.openRecentRequested.emit(p))
            self._recent_box.addWidget(row)
            self._rows.append(row)
        self._recent_header.setVisible(bool(existing))

    def recent_paths(self) -> list[str]:
        return [str(row.path) for row in self._rows]

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        motion.fade_up(self._column)
        for i, row in enumerate(self._rows):
            motion.fade_up(row, delay=120 + i * 50, duration=TOKENS.anim_up)

    def _refresh_theme(self) -> None:
        p = self._theme.palette
        self._logo.setPixmap(self._theme.logo_pixmap(56))
        self._title.setText(f'专注阅读，<i style="color:{p.accent};">安静</i>思考。')
        self._zone_icon.setStyleSheet(f"background-color:{p.accent}; border-radius:20px;")
        self._zone_icon.setPixmap(
            self._theme.colored_icon("open", p.on_accent, size=18).pixmap(QSize(18, 18))
        )
        for row in list(self._rows):
            row._icon.setPixmap(
                self._theme.colored_icon("file", p.text_muted, size=16).pixmap(16, 16)
            )
