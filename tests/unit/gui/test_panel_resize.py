"""两侧栏拖动调整宽度：拖动条跟随面板、宽度夹在范围内、双击恢复默认、宽度写进设置。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.panels.ai_panel import AI_PANEL_MIN_WIDTH, AI_PANEL_WIDTH
from pdfreader.gui.panels.sidebar import SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, SIDEBAR_WIDTH
from pdfreader.gui.widgets.panel_grip import PanelGrip


def _doc(path: Path) -> Path:
    d = pymupdf.open()
    d.new_page().insert_text((72, 80), "Hello", fontsize=14)
    d.save(str(path))
    d.close()
    return path


def _send(widget: QWidget, kind: QEvent.Type, x: float) -> None:
    local = QPointF(2, 10)
    glob = QPointF(x, 10)
    button = Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    event = QMouseEvent(kind, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def _drag(grip: PanelGrip, dx: float) -> None:
    _send(grip, QEvent.Type.MouseButtonPress, 500)
    _send(grip, QEvent.Type.MouseMove, 500 + dx / 2)
    _send(grip, QEvent.Type.MouseMove, 500 + dx)
    _send(grip, QEvent.Type.MouseButtonRelease, 500 + dx)


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 860)
    w.show()
    qtbot.waitExposed(w)
    w.open_path(_doc(tmp_path / "a.pdf"))
    yield w
    w._ai.shutdown()
    if w._controller.document is not None:
        w._controller.document.mark_saved()
        w._controller.close_document()


def test_sidebar_drag_clamp_and_reset(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    window._set_sidebar_visible(True)
    grip = window._sidebar_grip
    qtbot.waitUntil(lambda: grip.isVisible(), timeout=2000)
    assert grip.geometry().right() == window.sidebar.geometry().right()  # 贴在面板内侧边缘

    _drag(grip, 80)
    assert window.sidebar.panel_width == SIDEBAR_WIDTH + 80
    qtbot.waitUntil(lambda: window.sidebar.width() == SIDEBAR_WIDTH + 80, timeout=2000)
    assert window._settings.sidebar_width == SIDEBAR_WIDTH + 80
    assert window._settings_store.load().sidebar_width == SIDEBAR_WIDTH + 80  # 松手即保存

    _drag(grip, 2000)
    assert window._settings.sidebar_width == SIDEBAR_MAX_WIDTH
    _drag(grip, -2000)
    assert window._settings.sidebar_width == SIDEBAR_MIN_WIDTH

    grip.resetRequested.emit()  # 双击
    assert window._settings.sidebar_width == SIDEBAR_WIDTH

    window._set_sidebar_visible(False)
    qtbot.waitUntil(lambda: not grip.isVisible(), timeout=3000)


def test_ai_panel_drag_keeps_reading_room(window: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    window._set_ai_panel_visible(True)
    dock = window._ai_dock
    grip = window._ai_grip
    qtbot.waitUntil(lambda: dock.width() == AI_PANEL_WIDTH and grip.isVisible(), timeout=3000)
    assert grip.geometry().left() == dock.geometry().left()

    _drag(grip, -120)  # 右侧面板向左拖 = 变宽
    assert window._settings.ai_panel_width == AI_PANEL_WIDTH + 120
    qtbot.waitUntil(lambda: dock.width() == AI_PANEL_WIDTH + 120, timeout=2000)

    _drag(grip, -5000)  # 夹住：阅读区至少留 360
    qtbot.waitUntil(lambda: window._stack.width() >= 360, timeout=2000)
    assert window._stack.width() >= 360
    _drag(grip, 5000)
    assert window._settings.ai_panel_width == AI_PANEL_MIN_WIDTH

    # 关掉再打开：恢复到用户拖出的宽度
    _drag(grip, -60)
    width = window._settings.ai_panel_width
    window._set_ai_panel_visible(False)
    qtbot.waitUntil(lambda: not dock.isVisible(), timeout=3000)
    window._set_ai_panel_visible(True)
    qtbot.waitUntil(lambda: dock.width() == width, timeout=3000)
