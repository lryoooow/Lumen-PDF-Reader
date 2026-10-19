"""侧栏宽度拖动条。

叠在侧栏内侧边缘上的一条 6px 透明热区（不占布局）：悬停 / 拖动时画一条强调色细线，
光标变为左右调整；拖动时把新宽度交给回调，松开时发 ``released``，双击发 ``resetRequested``。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from pdfreader.gui.theme import get_theme

__all__ = ["GRIP_WIDTH", "PanelGrip"]

GRIP_WIDTH = 6


class PanelGrip(QWidget):
    """跟随 ``panel`` 的宽度拖动条（父控件是侧栏与阅读区的共同容器）。

    ``side="left"``：面板在左，拖动条贴在它右边缘，向右拖变宽；``"right"`` 反之。
    """

    released = Signal()
    resetRequested = Signal()

    def __init__(
        self,
        panel: QWidget,
        side: str,
        on_drag: Callable[[int], None],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._panel = panel
        self._side = side
        self._on_drag = on_drag
        self._press_x: float | None = None
        self._press_width = 0
        self._hover = False
        self.setObjectName("PanelGrip")
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip("拖动调整宽度，双击恢复默认")
        self.setMouseTracking(True)
        panel.installEventFilter(self)
        self.hide()

    @property
    def dragging(self) -> bool:
        return self._press_x is not None

    # ------------------------------------------------------------------ 位置
    def sync(self) -> None:
        """按面板当前几何放到它的内侧边缘；面板收起时隐藏。"""
        panel = self._panel
        if not panel.isVisible() or panel.width() < 40:
            self.hide()
            return
        geo = panel.geometry()
        # 热区整个落在面板里，不盖住阅读区（右侧阅读区边上就是滚动条）
        x = geo.right() + 1 - GRIP_WIDTH if self._side == "left" else geo.left()
        self.setGeometry(QRect(x, geo.top(), GRIP_WIDTH, geo.height()))
        self.show()
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._panel and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        ):
            self.sync()
        return False

    # ------------------------------------------------------------------ 拖动
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x = event.globalPosition().x()
            self._press_width = self._panel.width()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_x is not None:
            delta = round(event.globalPosition().x() - self._press_x)
            if self._side == "right":
                delta = -delta
            self._on_drag(self._press_width + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._press_x is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_x = None
            self.update()
            self.released.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.resetRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------ 外观
    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        if not (self._hover or self.dragging):
            return
        painter = QPainter(self)
        color = QColor(get_theme().palette.accent)
        if not self.dragging:
            color.setAlphaF(0.6)
        # 细线画在面板与阅读区的交界处
        x = self.width() - 2 if self._side == "left" else 0
        painter.fillRect(x, 0, 2, self.height(), color)
        painter.end()
