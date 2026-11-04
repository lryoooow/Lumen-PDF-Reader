"""文档标签栏跨平台初始化回归。"""

import pytest
from PySide6.QtWidgets import QTabBar
from pytestqt.qtbot import QtBot

from pdfreader.gui.widgets.doc_tabs import DocumentTabBar


def test_document_mode_can_measure_tabs_during_init(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = QTabBar.setDocumentMode

    def set_document_mode(tab_bar: DocumentTabBar, enabled: bool) -> None:
        # macOS Qt 在这一步会同步调用 tabSizeHint。
        assert tab_bar.tabSizeHint(0).height() == 32
        original(tab_bar, enabled)

    monkeypatch.setattr(QTabBar, "setDocumentMode", set_document_mode)
    tab_bar = DocumentTabBar()
    qtbot.addWidget(tab_bar)
    assert tab_bar.documentMode()
