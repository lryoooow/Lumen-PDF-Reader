"""文档标签栏：每个打开的文档一个标签（可拖动排序、中键 / 关闭按钮关闭、右键「关闭其他」）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QMenu, QTabBar, QWidget

__all__ = ["DocumentTabBar"]


class DocumentTabBar(QTabBar):
    """文档标签栏。

    Signals:
        closeOthersRequested(int): 右键「关闭其他标签」，参数为保留的标签下标
    """

    closeOthersRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DocTabs")
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        index = self.tabAt(event.pos())
        if index < 0:
            return
        menu = QMenu(self)
        menu.addAction("关闭", lambda: self.tabCloseRequested.emit(index))
        others = menu.addAction("关闭其他标签", lambda: self.closeOthersRequested.emit(index))
        others.setEnabled(self.count() > 1)
        menu.exec(event.globalPos())
