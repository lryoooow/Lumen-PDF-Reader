"""批注控制器测试（仅模式状态机；实际交互由 PdfView 处理）。"""

from __future__ import annotations

from pdfreader.controllers import AnnotationController, AnnotationMode


def test_initial_mode_none(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
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
    ctrl.set_mode(AnnotationMode.INK)  # 相同模式，不再发信号
    assert modes == []


def test_back_to_none_deactivates(qtbot) -> None:  # type: ignore[no-untyped-def]
    ctrl = AnnotationController()
    ctrl.set_mode(AnnotationMode.TEXT)
    ctrl.set_mode(AnnotationMode.NONE)
    assert ctrl.is_active is False
