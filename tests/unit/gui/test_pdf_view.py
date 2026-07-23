"""PdfView 单元测试。"""

from __future__ import annotations

from PySide6.QtGui import QPixmap

from pdfreader.gui.widgets import PdfView


def test_pdf_view_instantiates(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    assert view is not None


def test_pdf_view_current_page_initial_none(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    assert view.current_page_num is None


def test_show_pixmap_sets_page(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    pix = QPixmap(100, 100)
    pix.fill()
    view.show_pixmap(pix, page_num=2)
    assert view.current_page_num == 2


def test_page_rendered_signal(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    received: list[int] = []
    view.pageRendered.connect(received.append)
    pix = QPixmap(50, 50)
    pix.fill()
    view.show_pixmap(pix, page_num=5)
    assert received == [5]


def test_clear_resets_page(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    pix = QPixmap(10, 10)
    pix.fill()
    view.show_pixmap(pix, page_num=0)
    view.clear()
    assert view.current_page_num is None


def test_zoom_in_out_changes_transform(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    before = view.transform().m11()  # 水平缩放系数
    view.zoom_in()
    assert view.transform().m11() > before
    view.zoom_out()
    assert abs(view.transform().m11() - before) < 0.001
