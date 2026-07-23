"""验证主窗口能被实例化与显示。

这是阶段 0 的 GUI 冒烟测试——确保 PySide6 与 GUI 层骨架工作正常。
"""

from __future__ import annotations

from pdfreader import __version__
from pdfreader.gui.main_window import MainWindow


def test_main_window_instantiates(qtbot) -> None:  # type: ignore[no-untyped-def]
    """主窗口能被实例化。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window is not None


def test_main_window_title(qtbot) -> None:  # type: ignore[no-untyped-def]
    """窗口标题包含应用名。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert "PDF Reader" in window.windowTitle()


def test_main_window_has_open_action(qtbot) -> None:  # type: ignore[no-untyped-def]
    """菜单包含"打开"动作（阶段 0 禁用，阶段 3 启用）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert hasattr(window, "open_action")
    assert window.open_action.isEnabled() is False


def test_main_window_default_size(qtbot) -> None:  # type: ignore[no-untyped-def]
    """默认窗口尺寸合理。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.width() >= 640
    assert window.height() >= 480


def test_main_window_shows_version(qtbot) -> None:  # type: ignore[no-untyped-def]
    """窗口中央显示版本号。"""
    from PySide6.QtWidgets import QLabel

    window = MainWindow()
    qtbot.addWidget(window)
    central = window.centralWidget()
    labels = central.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any(__version__ in t for t in texts)
