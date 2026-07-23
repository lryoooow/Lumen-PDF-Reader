"""批注控制器测试。"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from pdfreader.controllers import AnnotationController, AnnotationMode


def test_initial_mode_none(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    qtbot.addWidget(ctrl) if False else None  # QObject 不需要 addWidget
    assert ctrl.mode == AnnotationMode.NONE
    assert ctrl.is_active is False


def test_set_mode(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    modes: list[str] = []
    ctrl.modeChanged.connect(modes.append)
    ctrl.set_mode(AnnotationMode.HIGHLIGHT)
    assert ctrl.mode == AnnotationMode.HIGHLIGHT
    assert ctrl.is_active is True
    assert modes == [AnnotationMode.HIGHLIGHT]


def test_set_same_mode_no_signal(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.INK)
    modes: list[str] = []
    ctrl.modeChanged.connect(modes.append)
    ctrl.set_mode(AnnotationMode.INK)  # 相同模式
    assert modes == []


def test_highlight_flow(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.HIGHLIGHT)
    received: list = []
    ctrl.highlightRequested.connect(lambda p, r: received.append((p, r)))
    # 模拟拖选：press → release
    ctrl.on_press(0, QPointF(10, 10))
    ctrl.on_release(0, QPointF(100, 50))
    assert len(received) == 1
    page, rect = received[0]
    assert page == 0
    assert rect.x0 == 10 and rect.y0 == 10
    assert rect.x1 == 100 and rect.y1 == 50


def test_highlight_too_small_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.HIGHLIGHT)
    received: list = []
    ctrl.highlightRequested.connect(lambda p, r: received.append((p, r)))
    ctrl.on_press(0, QPointF(10, 10))
    ctrl.on_release(0, QPointF(12, 11))  # 太小
    assert received == []


def test_ink_flow(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.INK)
    received: list = []
    ctrl.inkRequested.connect(lambda p, s: received.append((p, s)))
    ctrl.on_press(0, QPointF(10, 10))
    ctrl.on_move(0, QPointF(20, 20))
    ctrl.on_move(0, QPointF(30, 30))
    ctrl.on_release(0, QPointF(40, 40))
    assert len(received) == 1
    page, stroke = received[0]
    assert page == 0
    # press 加 1 点 + 2 个 move = 3 点（release 不再追加）
    assert len(stroke.points) == 3


def test_ink_single_point_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.INK)
    received: list = []
    ctrl.inkRequested.connect(lambda p, s: received.append((p, s)))
    ctrl.on_press(0, QPointF(10, 10))
    ctrl.on_release(0, QPointF(10, 10))  # 只有一个点
    assert received == []


def test_text_flow(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.TEXT)
    received: list = []
    ctrl.textRequested.connect(lambda p, pt: received.append((p, pt)))
    ctrl.on_press(0, QPointF(150, 200))
    assert len(received) == 1
    page, point = received[0]
    assert page == 0
    assert point.x == 150 and point.y == 200


def test_none_mode_ignores_events(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    received: list = []
    ctrl.highlightRequested.connect(lambda *a: received.append(a))
    ctrl.on_press(0, QPointF(0, 0))
    ctrl.on_release(0, QPointF(100, 100))
    assert received == []


def test_cancel(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.INK)
    ctrl.on_press(0, QPointF(10, 10))
    ctrl.cancel()
    received: list = []
    ctrl.inkRequested.connect(lambda *a: received.append(a))
    ctrl.on_release(0, QPointF(20, 20))  # cancel 后 release 不触发
    assert received == []
