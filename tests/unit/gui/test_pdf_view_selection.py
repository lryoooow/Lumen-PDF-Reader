"""PdfView 选择手势：双击选词、三击选行、Shift 扩展、Ctrl+A、跨页选择与拖选自动滚动。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdfreader.engines.types import Rect, WordBox
from pdfreader.gui.widgets.pdf_view import PdfView

A4 = (595, 842)
TWO_LINES = (
    WordBox(rect=Rect(10, 10, 40, 22), text="Hello", block=0, line=0, word_no=0),
    WordBox(rect=Rect(45, 10, 80, 22), text="World", block=0, line=0, word_no=1),
    WordBox(rect=Rect(10, 30, 60, 42), text="Second", block=0, line=1, word_no=2),
    WordBox(rect=Rect(65, 30, 95, 42), text="Line", block=0, line=1, word_no=3),
)
CJK = (
    WordBox(rect=Rect(10, 10, 22, 22), text="中", block=0, line=0, word_no=0),
    WordBox(rect=Rect(22, 10, 34, 22), text="文", block=0, line=0, word_no=1),
)


@pytest.fixture
def view(qtbot) -> PdfView:  # type: ignore[no-untyped-def]
    v = PdfView()
    qtbot.addWidget(v)
    v.resize(500, 400)
    v.show()
    qtbot.waitExposed(v)
    return v


def _event(kind: QEvent.Type, pos: QPointF, mods: Qt.KeyboardModifier) -> QMouseEvent:
    return QMouseEvent(
        kind,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        mods,
    )


def _view_pos(view: PdfView, page: int, x: float, y: float) -> QPointF:
    """页内 PDF 点 → 视口坐标。"""
    page_x, page_y = view._page_origin(page)
    return QPointF(view.mapFromScene(QPointF(page_x + x * view.zoom, page_y + y * view.zoom)))


def _press(
    view: PdfView, pos: QPointF, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
) -> None:
    view.mousePressEvent(_event(QEvent.Type.MouseButtonPress, pos, mods))


def _move(view: PdfView, pos: QPointF) -> None:
    view.mouseMoveEvent(_event(QEvent.Type.MouseMove, pos, Qt.KeyboardModifier.NoModifier))


def _release(
    view: PdfView, pos: QPointF, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
) -> None:
    view.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, pos, mods))


def _double_click(view: PdfView, pos: QPointF) -> None:
    view.mouseDoubleClickEvent(
        _event(QEvent.Type.MouseButtonDblClick, pos, Qt.KeyboardModifier.NoModifier)
    )


def _click(view: PdfView, pos: QPointF) -> None:
    _press(view, pos)
    _release(view, pos)


class TestClickGestures:
    def test_native_double_click_remains_double_when_dispatch_is_delayed(
        self, view: PdfView
    ) -> None:
        view.set_text_provider(lambda p: CJK if p == 0 else ())
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        pos = _view_pos(view, 0, 12, 15)
        _click(view, pos)
        # 系统已识别为双击，但主线程忙导致事件晚于自行计时的阈值才被处理。
        view._last_click_time -= QApplication.doubleClickInterval() / 1000 + 1
        _double_click(view, pos)
        _release(view, pos)
        assert view.selected_text == "中文"

    def test_double_click_selects_word(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        view.set_text_provider(lambda p: TWO_LINES if p == 0 else ())
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        pos = _view_pos(view, 0, 12, 15)
        _click(view, pos)
        qtbot.wait(60)
        _double_click(view, pos)
        _release(view, pos)
        assert view.selected_text == "Hello"
        assert len(view.selection_rects()[1]) == 1  # type: ignore[index]

    def test_double_click_joins_cjk_glyphs(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        view.set_text_provider(lambda p: CJK if p == 0 else ())
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        pos = _view_pos(view, 0, 12, 15)
        _click(view, pos)
        qtbot.wait(60)
        _double_click(view, pos)
        _release(view, pos)
        assert view.selected_text == "中文"

    def test_triple_click_selects_line(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        view.set_text_provider(lambda p: TWO_LINES if p == 0 else ())
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        pos = _view_pos(view, 0, 12, 15)
        _click(view, pos)
        qtbot.wait(60)
        _double_click(view, pos)
        _release(view, pos)
        qtbot.wait(60)
        _press(view, pos)
        _release(view, pos)
        assert view.selected_text == "Hello World"

    def test_shift_click_extends_selection(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        events: list[bool] = []
        view.selectionFinished.connect(lambda: events.append(True))
        view.set_text_provider(lambda p: TWO_LINES if p == 0 else ())
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        view._select_range(0, QPointF(12, 15), QPointF(50, 15))
        assert view.selected_text == "Hello World"
        pos = _view_pos(view, 0, 70, 35)
        _press(view, pos, Qt.KeyboardModifier.ShiftModifier)
        _release(view, pos, Qt.KeyboardModifier.ShiftModifier)
        assert view.selected_text == "Hello World\nSecond Line"
        assert events

    def test_ctrl_a_selects_current_page(self, view: PdfView) -> None:
        view.set_text_provider(lambda p: TWO_LINES if p == 0 else ())
        view.setup_document(2, [A4, A4])
        view.set_zoom(1.0)
        view.jump_to_page(0)
        view.setFocus()
        QApplication.processEvents()
        QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert view.selected_text == "Hello World\nSecond Line"
        assert len(view.selection_spans()) == 1


class TestCrossPageSelection:
    def _setup(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        view.resize(400, 500)
        view.set_text_provider(
            lambda p: (
                WordBox(rect=Rect(10, 10, 60, 22), text=f"Page{p}", block=0, line=0, word_no=0),
            )
        )
        view.setup_document(2, [(200, 150), (200, 150)])
        view.set_zoom(1.0)

    def test_drag_across_pages_keeps_both_pages(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        self._setup(view, qtbot)
        start = _view_pos(view, 0, 30, 20)
        end = _view_pos(view, 1, 30, 20)
        _press(view, start)
        _move(view, end)
        _release(view, end)
        assert view.selected_text == "Page0\nPage1"
        assert view.selection_rects() is None
        assert len(view.selection_spans()) == 2

    def test_cross_page_cannot_add_markup(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        self._setup(view, qtbot)
        start = _view_pos(view, 0, 30, 20)
        end = _view_pos(view, 1, 30, 20)
        _press(view, start)
        _move(view, end)
        _release(view, end)
        requested: list[tuple[int, object, str]] = []
        view.markupRequested.connect(lambda p, r, k: requested.append((p, r, k)))
        view._request_markup("highlight")
        assert requested == []
        assert view.has_selection


class TestAutoScroll:
    def test_dragging_at_bottom_edge_scrolls(self, view: PdfView) -> None:
        view.resize(300, 200)
        view.set_text_provider(
            lambda p: (
                WordBox(rect=Rect(10, 10, 60, 22), text=f"p{p}", block=0, line=0, word_no=0),
            )
        )
        view.setup_document(5, [A4] * 5)
        view.set_zoom(1.0)
        bar = view.verticalScrollBar()
        bar.setValue(0)
        start = _view_pos(view, 0, 30, 20)
        _press(view, start)
        _move(view, QPointF(20, view.viewport().height() - 5))
        before = bar.value()
        view._autoscroll_tick()
        assert bar.value() > before
        assert view._drag_scrolled
        _release(view, QPointF(20, view.viewport().height() - 5))
        assert not view._autoscroll_timer.isActive()
