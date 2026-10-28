import pytest
from PySide6.QtGui import QAction

from pdfreader.gui.dialogs.command_palette import CommandPalette
from pdfreader.gui.main_window import MainWindow
from pdfreader.plugin_api import Command


def test_palette_keeps_unavailable_commands_and_executes_after_close(qtbot) -> None:  # type: ignore[no-untyped-def]
    called: list[str] = []
    first = QAction("阅读笔记")
    first.triggered.connect(lambda: called.append("notes"))
    disabled = QAction("不可用操作")
    disabled.setEnabled(False)
    disabled.setProperty("disabledReason", "请先打开文档")
    dialog = CommandPalette([first, disabled])
    qtbot.addWidget(dialog)
    assert dialog.results.count() == 2
    dialog.query.setText("不可用")
    assert "请先打开文档" in dialog.results.item(0).text()
    dialog.execute()
    assert called == []
    assert "请先打开文档" in dialog.details.text()
    dialog.query.setText("笔记")
    dialog.execute()
    qtbot.waitUntil(lambda: called == ["notes"])


def test_command_disabled_after_accept_does_not_execute(qtbot) -> None:  # type: ignore[no-untyped-def]
    called: list[bool] = []
    action = QAction("操作")
    action.triggered.connect(lambda: called.append(True))
    dialog = CommandPalette([action])
    qtbot.addWidget(dialog)
    dialog.execute()
    action.setEnabled(False)
    qtbot.wait(20)
    assert called == []


def test_command_reason_is_rechecked_and_display_updates(qtbot) -> None:  # type: ignore[no-untyped-def]
    called: list[bool] = []
    action = QAction("复制选区")
    action.triggered.connect(lambda: called.append(True))
    reason = ["请先选中文字"]
    dialog = CommandPalette([action], unavailable_reason=lambda _: reason[0])
    qtbot.addWidget(dialog)
    dialog.execute()
    assert "请先选中文字" in dialog.details.text()
    assert not called
    reason[0] = ""
    action.setText("复制选区（可用）")
    assert "请先选中文字" not in dialog.results.item(0).text()
    dialog.execute()
    qtbot.waitUntil(lambda: called == [True])


def test_menu_commands_keep_parent_conditions_and_execute_once(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    qtbot.addWidget(menu)
    root = QAction("OCR")
    root.setMenu(menu)
    current = menu.addAction("当前页")
    menu.addAction("全部页")
    called: list[bool] = []
    current.triggered.connect(lambda: called.append(True))
    root.setEnabled(False)
    root.setProperty("disabledReason", "当前格式只读，不支持 OCR")
    dialog = CommandPalette([root])
    qtbot.addWidget(dialog)
    dialog.query.setText("OCR 当前页")
    assert dialog.results.count() == 1
    dialog.execute()
    assert "当前格式只读" in dialog.details.text()
    assert not called
    root.setEnabled(True)
    dialog.execute()
    qtbot.waitUntil(lambda: called == [True])


def test_widget_menu_opens_after_palette_exits_modal_state(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QMenu, QPushButton, QWidget, QWidgetAction

    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    menu = QMenu(host)
    color = QWidgetAction(menu)
    color.setDefaultWidget(QPushButton("颜色"))
    menu.addAction(color)
    action = QAction("色板", host)
    action.setMenu(menu)
    dialog = CommandPalette([action], host)
    qtbot.addWidget(dialog)
    QTimer.singleShot(0, dialog.execute)
    assert dialog.exec() == QDialog.DialogCode.Accepted
    assert not dialog.isVisible()
    qtbot.waitUntil(menu.isVisible)
    menu.close()


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
