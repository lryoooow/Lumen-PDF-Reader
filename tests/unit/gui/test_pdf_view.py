"""PdfView 单元测试（阶段 4 多页连续滚动视图）。"""

from __future__ import annotations

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pdfreader.engines.types import PixmapData
from pdfreader.gui.widgets import PdfView


def test_pdf_view_instantiates(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    assert view is not None


def test_current_page_initial_none(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    assert view.current_page_num is None


def test_setup_document_sets_page_count(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(3, [(595, 842), (595, 842), (595, 842)])
    assert view.page_count == 3


def test_clear_resets(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(2, [(595, 842), (595, 842)])
    view.clear()
    assert view.page_count == 0


def test_show_pixmap_single_page(qtbot) -> None:  # type: ignore[no-untyped-def]
    """兼容接口：show_pixmap 转为单页文档。"""
    view = PdfView()
    qtbot.addWidget(view)
    pix = QPixmap(100, 100)
    pix.fill()
    view.show_pixmap(pix, page_num=0)
    assert view.page_count == 1


def test_zoom_default(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    assert view.zoom == 1.5


def test_set_zoom(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(2.0)
    assert view.zoom == 2.0


def test_zoom_in_increases(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    before = view.zoom
    view.zoom_in()
    assert view.zoom > before


def test_zoom_out_decreases(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(2.0)
    before = view.zoom
    view.zoom_out()
    assert view.zoom < before


def test_set_zoom_invalid_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(2.0)
    view.set_zoom(0)
    view.set_zoom(-1)
    assert view.zoom == 2.0


def test_on_page_rendered_updates_cache(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(2, [(595, 842)] * 2)
    view.set_zoom(1.0)
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    view.on_page_rendered(0, 1.0, data)
    assert view._rendered_cache.contains(0)


def test_on_page_rendered_wrong_zoom_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.5)
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    view.on_page_rendered(0, 2.0, data)
    assert not view._rendered_cache.contains(0)


def test_render_requested_signal(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.resize(400, 600)
    view.show()
    qtbot.waitExposed(view)
    requests: list[tuple[int, float]] = []
    view.renderRequested.connect(lambda p, z: requests.append((p, z)))
    view.setup_document(3, [(595, 842)] * 3)
    QApplication.processEvents()
    assert len(requests) > 0


def test_jump_to_page(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.resize(400, 800)
    view.show()
    qtbot.waitExposed(view)
    view.setup_document(5, [(595, 842)] * 5)
    view.jump_to_page(3)
    QApplication.processEvents()
    # 不报错即通过
    assert view.page_count == 5
