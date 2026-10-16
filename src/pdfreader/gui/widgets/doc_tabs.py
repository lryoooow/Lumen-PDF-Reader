"""文档标签栏：每个打开的文档一个标签（可拖动排序、中键 / 关闭按钮关闭、右键「关闭其他」）。

设计稿：高 32 的圆顶标签（最窄 80、最宽 220），左侧文件图标，当前标签底色同阅读区，
底边一条强调色细线；未保存的文档名后带圆点（文字由主窗口给出）。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QFont, QFontMetrics, QIcon, QMouseEvent
from PySide6.QtWidgets import QApplication, QMenu, QTabBar, QWidget

__all__ = ["DocumentTabBar"]

_TAB_HEIGHT = 32
_TAB_MIN_WIDTH = 80
_TAB_MAX_WIDTH = 220
# 左内边距 14 + 图标 13 + 间距 8 …… 文字 …… 间距 8 + 关闭按钮 20 + 右内边距 6
_TAB_CHROME = 14 + 13 + 8 + 8 + 20 + 6


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
        self.setIconSize(QSize(13, 13))
        self._icon = QIcon()
        # 样式表里的字号不参与 tabSizeHint 的计算：按同样的 12px 自己量宽度，免得过早省略
        self._tab_font = QFont(QApplication.font())
        self._tab_font.setPixelSize(12)

    def set_tab_icon(self, icon: QIcon) -> None:
        """所有标签左侧的文件图标（主题切换时重设）。"""
        self._icon = icon
        for index in range(self.count()):
            self.setTabIcon(index, icon)

    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        if not self._icon.isNull():
            self.setTabIcon(index, self._icon)

    def tabSizeHint(self, index: int) -> QSize:
        text_w = QFontMetrics(self._tab_font).horizontalAdvance(self.tabText(index))
        width = min(_TAB_MAX_WIDTH, max(_TAB_MIN_WIDTH, text_w + _TAB_CHROME))
        return QSize(width, _TAB_HEIGHT)

    def minimumTabSizeHint(self, index: int) -> QSize:
        return QSize(_TAB_MIN_WIDTH, _TAB_HEIGHT)

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
