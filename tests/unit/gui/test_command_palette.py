import pytest
from PySide6.QtGui import QAction

from pdfreader.gui.dialogs.command_palette import CommandPalette
from pdfreader.gui.main_window import MainWindow
from pdfreader.plugin_api import Command


def test_palette_filters_enabled_commands_and_executes_after_close(qtbot) -> None:  # type: ignore[no-untyped-def]
    called: list[str] = []
    first = QAction("阅读笔记")
    first.triggered.connect(lambda: called.append("notes"))
    disabled = QAction("不可用操作")
    disabled.setEnabled(False)
    dialog = CommandPalette([first, disabled])
    qtbot.addWidget(dialog)
    assert dialog.results.count() == 1
    dialog.query.setText("笔记")
    dialog.execute()
    qtbot.waitUntil(lambda: called == ["notes"])


def test_explicit_command_api_validates_duplicate_ids_and_context(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.document_context() is None
    window.register_command(Command("test.command", "测试命令", lambda: None))
    with pytest.raises(ValueError, match="重复"):
        window.register_command(Command("test.command", "重复", lambda: None))
    with pytest.raises(ValueError, match="无效"):
        window.register_command(Command("../invalid", "无效", lambda: None))
    window.close()
