"""动效原语：开关关闭直达终态；打开时按时长插值、同 key 互相打断、收起后隐藏。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QObject, QPoint
from PySide6.QtWidgets import QLabel, QWidget

from pdfreader.gui.widgets import motion


@pytest.fixture
def motion_on() -> Iterator[None]:
    motion.set_enabled(True)
    yield
    motion.set_enabled(False)


def test_tween_disabled_applies_end_value() -> None:
    owner = QObject()
    got: list[float] = []
    assert motion.tween(owner, 0.0, 1.0, 300, got.append) is None
    assert got == [1.0]
    motion.set_enabled(True)
    try:
        assert motion.tween(owner, 0.0, 2.0, 0, got.append) is None  # 时长 0 也直达
    finally:
        motion.set_enabled(False)
    assert got[-1] == 2.0


def test_tween_interpolates_and_supersedes(qtbot, motion_on) -> None:  # type: ignore[no-untyped-def]
    owner = QObject()
    values: list[float] = []
    anim = motion.tween(owner, 0.0, 1.0, 120, values.append)
    assert anim is not None
    qtbot.waitUntil(lambda: bool(values) and values[-1] == pytest.approx(1.0), timeout=2000)
    assert all(0.0 <= v <= 1.0 for v in values)

    # 延迟启动：先回调起点；被同 key 的新补间打断后不会再跑
    first: list[float] = []
    second: list[float] = []
    motion.tween(owner, 5.0, 6.0, 100, first.append, delay=60)
    assert first == [5.0]
    motion.tween(owner, 0.0, 3.0, 60, second.append)
    qtbot.waitUntil(lambda: bool(second) and second[-1] == pytest.approx(3.0), timeout=2000)
    qtbot.wait(150)
    assert first == [5.0]

    # 不同 key 互不干扰
    a: list[float] = []
    b: list[float] = []
    motion.tween(owner, 0.0, 1.0, 60, a.append, key="_a")
    motion.tween(owner, 0.0, 1.0, 60, b.append, key="_b", delay=20)
    qtbot.waitUntil(lambda: bool(a) and bool(b) and a[-1] == b[-1] == 1.0, timeout=2000)


def test_pop_out_hides_and_calls_back(qtbot) -> None:  # type: ignore[no-untyped-def]
    w = QLabel("x")
    qtbot.addWidget(w)
    w.show()
    done: list[bool] = []
    motion.pop_out(w, on_done=lambda: done.append(True))  # 动效关闭：立即隐藏
    assert not w.isVisible() and done == [True]

    motion.set_enabled(True)
    try:
        w.show()
        motion.pop_out(w, duration=60, on_done=lambda: done.append(True))
        assert w.isVisible()  # 淡出中仍可见
        qtbot.waitUntil(lambda: not w.isVisible(), timeout=2000)
        assert done == [True, True] and w.graphicsEffect() is None
        motion.pop_out(w)  # 已隐藏：直接收尾
        assert done == [True, True]
    finally:
        motion.set_enabled(False)


def test_slide_and_pop_in_end_at_layout_position(qtbot, motion_on) -> None:  # type: ignore[no-untyped-def]
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(200, 120)
    child = QLabel("页", parent)
    child.move(30, 20)
    parent.show()
    qtbot.waitExposed(parent)
    motion.slide_in(child, 18, duration=60)
    qtbot.wait(20)
    qtbot.waitUntil(
        lambda: child.pos() == QPoint(30, 20) and child.graphicsEffect() is None, timeout=2000
    )
    motion.pop_in(child, duration=60)
    qtbot.waitUntil(
        lambda: child.pos() == QPoint(30, 20) and child.graphicsEffect() is None, timeout=2000
    )
