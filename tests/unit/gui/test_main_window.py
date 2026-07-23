"""主窗口测试。

阶段 3：验证主窗口能实例化、菜单结构正确、控制器接入正常。
端到端的"打开 PDF 渲染"测试见 tests/integration/。
"""

from __future__ import annotations

from pdfreader.gui.main_window import MainWindow


def test_main_window_instantiates(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window is not None


def test_main_window_title(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert "PDF Reader" in window.windowTitle()


def test_main_window_default_size(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.width() >= 640
    assert window.height() >= 480


def test_open_action_enabled(qtbot) -> None:  # type: ignore[no-untyped-def]
    """阶段 3：打开动作默认启用（阶段 0 是禁用的）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_action.isEnabled() is True


def test_has_controller_and_view(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._controller is not None
    assert window.pdf_view is not None


def test_no_document_initially(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._controller.has_document is False
    # 未打开文档时，编辑/视图相关动作应禁用
    assert window.zoom_in_action.isEnabled() is False
    assert window.close_doc_action.isEnabled() is False


def test_close_document_action_disabled_initially(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.close_doc_action.isEnabled() is False
