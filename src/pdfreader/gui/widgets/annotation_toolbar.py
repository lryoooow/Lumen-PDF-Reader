"""批注工具栏。

提供切换批注模式（高亮/手写/便签）的按钮组。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QToolBar,
    QToolButton,
)

from pdfreader.controllers import AnnotationMode

__all__ = ["AnnotationToolbar"]


class AnnotationToolbar(QToolBar):
    """批注工具栏：选择当前批注模式。

    Signals:
        modeRequested(str): 请求切换到某批注模式（AnnotationMode 常量）
    """

    modeRequested = Signal(str)

    def __init__(self, title: str = "批注", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(title, parent)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._setup_buttons()

    def _setup_buttons(self) -> None:
        self.select_btn = self._add_mode_button("选择", AnnotationMode.NONE, checked=True)
        self.addSeparator()
        self.highlight_btn = self._add_mode_button("高亮", AnnotationMode.HIGHLIGHT)
        self.ink_btn = self._add_mode_button("手写", AnnotationMode.INK)
        self.text_btn = self._add_mode_button("便签", AnnotationMode.TEXT)

    def _add_mode_button(self, label: str, mode: str, *, checked: bool = False) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.clicked.connect(lambda: self.modeRequested.emit(mode))
        self._button_group.addButton(btn)
        self.addWidget(btn)
        return btn

    def reset_to_select(self) -> None:
        """重置回选择模式。"""
        self.select_btn.setChecked(True)
        self.modeRequested.emit(AnnotationMode.NONE)
