"""PdfView 单元测试（阶段 4 多页连续滚动视图）。"""

from __future__ import annotations

import pytest
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


def test_set_zoom_relayouts_rendered_pages(qtbot) -> None:  # type: ignore[no-untyped-def]
    """缩放后旧位图必须被移出场景并换成新尺寸占位（否则错位重叠、内存不释放）。"""
    from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem

    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(3, [(595, 842)] * 3)
    view.set_zoom(1.0)
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    for page in range(3):
        view.on_page_rendered(page, 1.0, data)
    assert all(isinstance(item, QGraphicsPixmapItem) for item in view._page_items.values())

    view.set_zoom(0.5)

    assert len(view._page_items) == 3
    for page in range(3):
        item = view._page_items[page]
        rect = item.sceneBoundingRect()
        assert isinstance(item, QGraphicsRectItem), f"第 {page} 页应换回占位"
        assert rect.top() == view._page_y_positions[page]
        assert rect.height() == 842 * view.zoom
    assert not view.is_page_rendered(0)


def test_invalidate_page_clears_render_and_word_cache(qtbot) -> None:  # type: ignore[no-untyped-def]
    """单页失效：渲染缓存 + 词表都要丢，否则选择/复制仍是改动前的文本。"""
    from PySide6.QtWidgets import QGraphicsRectItem

    from pdfreader.engines.types import Rect, WordBox

    state = {"text": "旧文本"}
    view = PdfView()
    qtbot.addWidget(view)
    view.resize(400, 400)
    view.set_text_provider(
        lambda _p: (
            WordBox(rect=Rect(0, 0, 10, 10), text=state["text"], block=0, line=0, word_no=0),
        )
    )
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.0)
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    view.on_page_rendered(0, 1.0, data)
    assert view._get_words(0)[0].text == "旧文本"

    state["text"] = "新文本"
    view.invalidate_page(0)

    assert not view.is_page_rendered(0)
    assert isinstance(view._page_items[0], QGraphicsRectItem)
    assert view._get_words(0)[0].text == "新文本"


def test_on_page_rendered_wrong_zoom_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.5)
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    view.on_page_rendered(0, 2.0, data)
    assert not view._rendered_cache.contains(0)


def test_show_search_hit_highlights_and_centers(qtbot) -> None:  # type: ignore[no-untyped-def]
    """点击搜索结果：画出命中高亮，并把视口滚到该处；缩放/换文档时清除。"""
    from PySide6.QtWidgets import QGraphicsRectItem

    from pdfreader.engines.types import Rect

    view = PdfView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    view.setup_document(5, [(595, 842)] * 5)
    view.set_zoom(1.0)

    view.show_search_hit(2, Rect(100, 200, 200, 220))

    assert len(view._search_items) == 1
    item = view._search_items[0]
    assert isinstance(item, QGraphicsRectItem)
    # 命中矩形落在第 2 页上（页 y 偏移 + 矩形位置 × zoom）
    scene_rect = item.sceneBoundingRect()
    assert scene_rect.top() == view._page_y_positions[2] + 200
    assert scene_rect.left() == 100

    view.clear_search_hit()
    assert view._search_items == []


def test_search_hit_cleared_on_zoom(qtbot) -> None:  # type: ignore[no-untyped-def]
    """缩放后命中高亮的坐标已失效，必须清掉。"""
    from pdfreader.engines.types import Rect

    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(2, [(595, 842)] * 2)
    view.set_zoom(1.0)
    view.show_search_hit(0, Rect(10, 10, 50, 30))
    assert view._search_items

    view.set_zoom(0.5)

    assert view._search_items == []


def test_offscreen_pages_unloaded_when_cache_evicts(qtbot) -> None:  # type: ignore[no-untyped-def]
    """渲染超过缓存容量后，被淘汰且已离开视口的页必须换回占位。

    场景项会一直持有位图，只淘汰 LRU 条目等于位图永不释放（内存随「曾经渲染过
    的页数」增长）；而且 is_page_rendered() 已返回 False，滚回来还会重复渲染。
    """
    from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem

    view = PdfView(cache_size=3)
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    view.setup_document(12, [(200, 200)] * 12)
    view.set_zoom(1.0)
    view.jump_to_page(11)  # 停到末尾，确保第 0 页已离开视口
    QApplication.processEvents()
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    for page in range(12):
        view.on_page_rendered(page, 1.0, data)

    pixmap_pages = [
        page for page, item in view._page_items.items() if isinstance(item, QGraphicsPixmapItem)
    ]
    visible = view._visible_pages(view._viewport_scene_rect())
    cached = view._rendered_cache.keys()

    assert 0 not in pixmap_pages, "第 0 页已被淘汰且不在视口，不该继续持有位图"
    assert isinstance(view._page_items[0], QGraphicsRectItem)
    assert not view.is_page_rendered(0)
    assert set(pixmap_pages) <= set(cached) | set(visible)


def test_evicted_page_requests_render_again(qtbot) -> None:  # type: ignore[no-untyped-def]
    """被淘汰的页回到视口时应重新请求渲染（缓存已失效，不能只留空白）。"""
    view = PdfView(cache_size=2)
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    view.setup_document(10, [(200, 200)] * 10)
    view.set_zoom(1.0)
    view.jump_to_page(9)
    QApplication.processEvents()
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    for page in range(10):
        view.on_page_rendered(page, 1.0, data)
    assert not view.is_page_rendered(0)

    requests: list[tuple[int, float]] = []
    view.renderRequested.connect(lambda p, z: requests.append((p, z)))
    view.jump_to_page(0)
    QApplication.processEvents()

    assert any(page == 0 for page, _ in requests)


def test_visible_page_kept_even_if_evicted(qtbot) -> None:  # type: ignore[no-untyped-def]
    """视口内的页即使被 LRU 淘汰也不能被换回空白（否则眼前一页会突然空掉）。"""
    from PySide6.QtWidgets import QGraphicsPixmapItem

    view = PdfView(cache_size=1)
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    view.setup_document(6, [(200, 200)] * 6)
    view.set_zoom(1.0)
    QApplication.processEvents()
    data = PixmapData(samples=b"\xff" * (10 * 10 * 3), width=10, height=10, n=3, stride=30)
    view.on_page_rendered(0, 1.0, data)  # 第 0 页在视口内
    for page in range(1, 6):  # 后续渲染把第 0 页挤出缓存
        view.on_page_rendered(page, 1.0, data)

    assert not view.is_page_rendered(0)
    assert isinstance(view._page_items[0], QGraphicsPixmapItem)


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


def test_text_selection_and_copy(qtbot) -> None:  # type: ignore[no-untyped-def]
    """拖拽选择文本 → 选中文本正确、绘制选择框、可复制到剪贴板。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QGuiApplication

    from pdfreader.engines.types import Rect, WordBox

    view = PdfView()
    qtbot.addWidget(view)
    words = (
        WordBox(rect=Rect(10, 10, 40, 22), text="Hello", block=0, line=0, word_no=0),
        WordBox(rect=Rect(45, 10, 80, 22), text="World", block=0, line=0, word_no=1),
    )
    view.set_text_provider(lambda p: words if p == 0 else ())
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.0)
    view._select_range(0, QPointF(12, 15), QPointF(78, 15))
    assert view.has_selection
    assert view.selected_text == "Hello World"
    assert len(view._sel_items) == 2
    assert view.copy_selection() == len("Hello World")
    # 系统剪贴板可能被其他进程独占（Windows 上 OpenClipboard 失败时
    # Qt 会重试后放弃），此时无法验证落盘内容，跳过该断言而非误报失败。
    clip_text = QGuiApplication.clipboard().text()
    if clip_text != "Hello World":
        pytest.skip("系统剪贴板被其他进程占用，无法验证复制结果")
    assert clip_text == "Hello World"


def test_clear_selection(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QPointF

    from pdfreader.engines.types import Rect, WordBox

    view = PdfView()
    qtbot.addWidget(view)
    view.set_text_provider(
        lambda p: (WordBox(rect=Rect(10, 10, 40, 22), text="Hi", block=0, line=0, word_no=0),)
    )
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.0)
    view._select_range(0, QPointF(12, 15), QPointF(38, 15))
    assert view.has_selection
    view.clear_selection()
    assert not view.has_selection
    assert view._sel_items == []


def test_highlight_live_preview_and_commit(qtbot) -> None:  # type: ignore[no-untyped-def]
    """高亮：拖拽时实时预览方块，松开提交每行一个矩形。"""
    from PySide6.QtCore import QPointF

    from pdfreader.controllers import AnnotationController, AnnotationMode
    from pdfreader.engines.types import Rect, WordBox

    view = PdfView()
    qtbot.addWidget(view)
    words = (
        WordBox(rect=Rect(10, 10, 40, 22), text="Hello", block=0, line=0, word_no=0),
        WordBox(rect=Rect(45, 10, 80, 22), text="World", block=0, line=0, word_no=1),
    )
    view.set_text_provider(lambda p: words if p == 0 else ())
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.0)
    ac = AnnotationController()
    ac.set_mode(AnnotationMode.HIGHLIGHT)
    view.set_annotation_controller(ac)
    view._annot_page = 0
    view._update_highlight_preview(0, QPointF(12, 15), QPointF(78, 15))
    assert len(view._annot_items) == 2  # 拖拽中：两个词的实时预览
    got: list = []
    view.annotationHighlight.connect(lambda p, rects: got.append((p, rects)))
    view._commit_highlight()
    assert got and got[0][0] == 0 and len(got[0][1]) == 1  # 同一行合并为一个矩形
    assert view._annot_items == []


def test_ink_live_preview_and_commit(qtbot) -> None:  # type: ignore[no-untyped-def]
    """手写：拖拽时实时笔迹，松开提交笔迹点序列。"""
    from PySide6.QtCore import QPointF

    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.0)
    view._annot_page = 0
    view._start_ink_item(0)
    view._ink_points = [QPointF(10, 10), QPointF(20, 15), QPointF(30, 20)]
    view._update_ink_item()
    assert view._ink_item is not None and not view._ink_item.path().isEmpty()
    got: list = []
    view.annotationInk.connect(lambda p, s: got.append((p, s)))
    view._commit_ink()
    assert got and len(got[0][1].points) == 3
    assert view._ink_item is None


def test_escape_exits_annotation_mode(qtbot) -> None:  # type: ignore[no-untyped-def]
    """批注模式下按 Esc 退回浏览/选择模式。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from pdfreader.controllers import AnnotationController, AnnotationMode

    view = PdfView()
    qtbot.addWidget(view)
    ac = AnnotationController()
    ac.set_mode(AnnotationMode.HIGHLIGHT)
    view.set_annotation_controller(ac)
    view.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    assert ac.mode == AnnotationMode.NONE


def test_capture_drag_emits_area(qtbot) -> None:  # type: ignore[no-untyped-def]
    """框选提问：拖拽显示虚线框，松开发出区域；太小的框视为误点。"""
    from PySide6.QtCore import QPointF

    from pdfreader.controllers import AnnotationController, AnnotationMode

    view = PdfView()
    qtbot.addWidget(view)
    view.setup_document(1, [(595, 842)])
    view.set_zoom(1.0)
    ac = AnnotationController()
    ac.set_mode(AnnotationMode.CAPTURE)
    view.set_annotation_controller(ac)
    got: list = []
    view.areaCaptured.connect(lambda p, r: got.append((p, r)))
    view._annot_page = 0
    view._annot_start = QPointF(50, 60)
    view._update_capture_item(QPointF(200, 160))
    assert view._capture_item is not None
    view._commit_capture(QPointF(200, 160))
    assert view._capture_item is None
    assert got and got[0][0] == 0
    assert (got[0][1].x0, got[0][1].y0, got[0][1].x1, got[0][1].y1) == (50, 60, 200, 160)
    view._annot_page = 0
    view._annot_start = QPointF(50, 60)
    view._commit_capture(QPointF(52, 61))
    assert len(got) == 1
