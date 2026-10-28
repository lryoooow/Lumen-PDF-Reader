"""命令面板：可检索命令、可见前置条件及安全的延迟执行。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QVBoxLayout, QWidget


@dataclass(frozen=True)
class _Command:
    action: QAction
    parents: tuple[QAction, ...] = ()

    @property
    def label(self) -> str:
        return " > ".join(a.text().replace("&", "") for a in (*self.parents, self.action))


class CommandPalette(QDialog):
    def __init__(
        self,
        actions: Sequence[QAction],
        parent: QWidget | None = None,
        *,
        unavailable_reason: Callable[[QAction], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("命令面板 · 输入筛选 · Enter 执行 · Esc 关闭")
        self.resize(640, 500)
        self._reason = unavailable_reason
        self._commands: list[_Command] = []
        self._shown: list[_Command] = []
        self._collect(actions)
        root = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setAccessibleName("筛选命令")
        self.query.setPlaceholderText("输入命令名称或快捷键；不可用命令会说明原因")
        self.query.textChanged.connect(self.filter)
        self.query.installEventFilter(self)
        self.query.returnPressed.connect(self.execute)
        root.addWidget(self.query)
        self.results = QListWidget()
        self.results.setAccessibleName("命令及可用条件")
        self.results.itemActivated.connect(self.execute)
        self.results.currentRowChanged.connect(self._show_details)
        root.addWidget(self.results)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setAccessibleName("命令说明")
        root.addWidget(self.details)
        observed = {
            action for command in self._commands for action in (*command.parents, command.action)
        }
        for action in observed:
            action.changed.connect(self._refresh)
        self.filter("")
        self.query.setFocus()

    def _collect(self, actions: Sequence[QAction], parents: tuple[QAction, ...] = ()) -> None:
        for action in actions:
            if not action.text() or action.isSeparator() or action in parents:
                continue
            menu = action.menu()
            children = [child for child in menu.actions() if child.text()] if menu else []
            if children:
                self._collect(children, (*parents, action))
            else:
                self._commands.append(_Command(action, parents))

    def _unavailable(self, command: _Command) -> str:
        return _unavailable(command, self._reason)

    def _refresh(self) -> None:
        self.filter(self.query.text())

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
        selected = (
            self._shown[self.results.currentRow()].action
            if 0 <= self.results.currentRow() < len(self._shown)
            else None
        )
        terms = query.casefold().split()
        self._shown = [
            command
            for command in self._commands
            if all(
                term in (command.label + " " + _shortcut(command.action)).casefold()
                for term in terms
            )
        ]
        self.results.blockSignals(True)
        self.results.clear()
        for command in self._shown:
            reason = self._unavailable(command)
            label = command.label + "    " + _shortcut(command.action)
            self.results.addItem(label + (f"\n不可用：{reason}" if reason else ""))
            self.results.item(self.results.count() - 1).setToolTip(
                reason or command.action.toolTip()
            )
        row = next((i for i, c in enumerate(self._shown) if c.action is selected), 0)
        self.results.setCurrentRow(row if self._shown else -1)
        self.results.blockSignals(False)
        self._show_details()

    def _show_details(self) -> None:
        row = self.results.currentRow()
        if 0 <= row < len(self._shown):
            command = self._shown[row]
            reason = self._unavailable(command)
            self.details.setText(
                "不可用：" + reason if reason else command.action.toolTip() or "按 Enter 执行命令"
            )
        else:
            self.details.setText("没有匹配命令，请尝试名称或快捷键。")

    def execute(self) -> None:
        row = self.results.currentRow()
        if not 0 <= row < len(self._shown):
            return
        command = self._shown[row]
        if self._unavailable(command):
            self._show_details()
            return
        menu = command.action.menu()
        reason = self._reason
        if menu is not None:
            # 色板等 QWidgetAction 保留原菜单；先退出模态框，避免其阻塞主窗所属菜单。
            item = self.results.currentItem()
            point = self.results.mapToGlobal(self.results.visualItemRect(item).bottomLeft())
            self.accept()

            def show_menu() -> None:
                if not _unavailable(command, reason):
                    menu.popup(point)

            QTimer.singleShot(0, menu, show_menu)
            return
        self.accept()

        def trigger() -> None:
            # 接受对话框和实际调用之间仍可能发生任务完成、关闭或状态变化。
            if not _unavailable(command, reason):
                command.action.trigger()

        QTimer.singleShot(0, command.action, trigger)


def _shortcut(action: QAction) -> str:
    return action.shortcut().toString() or str(action.property("commandShortcut") or "")


def _unavailable(command: _Command, reason: Callable[[QAction], str] | None) -> str:
    for action in (*command.parents, command.action):
        if reason is not None:
            message = reason(action)
            if message:
                return message
        if not action.isEnabled():
            return str(
                action.property("disabledReason") or "当前条件不满足，请检查文档或任务状态。"
            )
    return ""
