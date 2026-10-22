"""命令面板：键盘可检索并执行当前可用动作。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QVBoxLayout, QWidget


class CommandPalette(QDialog):
    def __init__(self, actions: Sequence[QAction], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("命令面板 · 输入筛选 · Enter 执行")
        self.resize(640, 500)
        self._actions = [action for action in actions if action.text() and action.isEnabled()]
        self._shown: list[QAction] = []
        root = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setAccessibleName("筛选命令")
        self.query.setPlaceholderText("输入命令名称或快捷键")
        self.query.textChanged.connect(self.filter)
        self.query.installEventFilter(self)
        self.query.returnPressed.connect(self.execute)
        root.addWidget(self.query)
        self.results = QListWidget()
        self.results.setAccessibleName("可用命令")
        self.results.itemActivated.connect(self.execute)
        root.addWidget(self.results)
        self.filter("")
        self.query.setFocus()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.query
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up)
            and self._shown
        ):
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            self.results.setCurrentRow((self.results.currentRow() + step) % len(self._shown))
            return True
        return super().eventFilter(watched, event)

    def filter(self, query: str) -> None:
        self._shown = [
            action
            for action in self._actions
            if query.casefold()
            in (action.text().replace("&", "") + " " + action.shortcut().toString()).casefold()
        ]
        self.results.clear()
        self.results.addItems(
            [
                action.text().replace("&", "") + "    " + action.shortcut().toString()
                for action in self._shown
            ]
        )
        if self._shown:
            self.results.setCurrentRow(0)

    def execute(self) -> None:
        row = self.results.currentRow()
        if 0 <= row < len(self._shown):
            action = self._shown[row]
            self.accept()
            if action.isEnabled():
                QTimer.singleShot(0, action, action.trigger)
