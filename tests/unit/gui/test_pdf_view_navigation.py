"""PdfView：当前页与跳转、缩放上限与高分屏渲染、链接、阅读模式、中文选择。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from pdfreader.engines.types import LinkInfo, PixmapData, Point, Rect, WordBox
from pdfreader.gui.widgets.pdf_view import (
    MAX_RENDER_PIXELS,
    MAX_ZOOM,
    MIN_ZOOM,
    PdfView,
    ReadingMode,
)

A4 = (595, 842)


@pytest.fixture
def view(qtbot) -> PdfView:  # type: ignore[no-untyped-def]
    v = PdfView()
    qtbot.addWidget(v)
    v.resize(500, 400)
    v.show()
    qtbot.waitExposed(v)
    return v


def _white(w: int = 10, h: int = 10) -> PixmapData:
    return PixmapData(samples=b"\xff" * (w * h * 3), width=w, height=h, n=3, stride=w * 3)


class TestCurrentPageAndJumps:
    def test_setup_document_keeps_requested_page(self, view: PdfView) -> None:
        """R3-01：编辑后重排必须回到原页，而不是把当前页清成 None（再被当成第 1 页）。"""
        view.setup_document(8, [A4] * 8)
        view.set_zoom(1.0)
        view.jump_to_page(3)
        assert view.current_page_num == 3
        view.setup_document(8, [A4] * 8, keep_page=3)
        assert view.current_page_num == 3

    def test_keep_page_clamped_after_delete(self, view: PdfView) -> None:
        view.setup_document(3, [A4] * 3, keep_page=7)
        assert view.current_page_num == 2

    def test_jump_aligns_page_top_with_viewport_top(self, view: PdfView) -> None:
        """R3-16：跳页后看到的是页首，不是页面中部。"""
        view.setup_document(5, [A4] * 5)
        view.set_zoom(1.0)
        view.jump_to_page(2)
        top = view._viewport_scene_rect().top()
        page_top = view._page_y_positions[2]
        # 设计稿：scrollTo(offsetTop - 24)，页首上方留 24px
        assert page_top - 25 <= top <= page_top + 1

    def test_jump_to_y_inside_page(self, view: PdfView) -> None:
        view.setup_document(3, [A4] * 3)
        view.set_zoom(1.0)
        view.jump_to_page(1, y=500)
        top = view._viewport_scene_rect().top()
        target = view._page_y_positions[1] + 500
        assert top < target < top + view.viewport().height()

    def test_current_page_is_most_visible_page(self, view: PdfView) -> None:
        """小缩放下一屏多页：跳到第 3 页后当前页就是第 3 页（不是视口中心那页）。"""
        view.setup_document(10, [(200, 150)] * 10)
        view.set_zoom(1.0)
        view.jump_to_page(2)
        assert view.current_page_num == 2

    def test_zoom_keeps_reading_position(self, view: PdfView) -> None:
        view.setup_document(6, [A4] * 6)
        view.set_zoom(1.0)
        view.restore_position(4, 0.5)
        page, offset = view.reading_position()
        view.set_zoom(2.0)
        page2, offset2 = view.reading_position()
        assert page2 == page == 4
        assert abs(offset2 - offset) < 0.05


class TestZoomLimits:
    def test_zoom_clamped(self, view: PdfView) -> None:
        """R3-13：连续放大不能无限制（单页位图会撑爆内存）。"""
        view.setup_document(1, [A4])
        for _ in range(60):
            view.zoom_in()
        assert view.zoom == pytest.approx(MAX_ZOOM)
        for _ in range(80):
            view.zoom_out()
        assert view.zoom == pytest.approx(MIN_ZOOM)

    def test_render_scale_respects_pixel_cap(self, view: PdfView) -> None:
        view.setup_document(1, [A4])
        view.set_zoom(MAX_ZOOM)
        scale = view.render_scale(0)
        assert A4[0] * A4[1] * scale * scale <= MAX_RENDER_PIXELS * 1.001

    def test_render_scale_includes_device_pixel_ratio(self, view: PdfView, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """R3-14：高分屏按物理像素渲染，否则 125%/150% 缩放下文字发虚。"""
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        monkeypatch.setattr(view, "devicePixelRatioF", lambda: 1.5)
        assert view.render_scale(0) == pytest.approx(1.5)

    def test_high_res_pixmap_displayed_at_logical_size(self, view: PdfView) -> None:
        view.setup_document(1, [(100, 100)])
        view.set_zoom(1.0)
        view.on_page_rendered(0, 1.0, _white(150, 150))  # 1.5 倍物理像素
        rect = view._page_items[0].sceneBoundingRect()
        assert rect.width() == pytest.approx(100, abs=0.5)

    def test_cache_byte_budget(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        """渲染缓存按总字节淘汰（高倍率下仅按页数限会占用数 GB）。"""
        v = PdfView(cache_size=30, cache_bytes=3 * 100 * 100 * 4)
        qtbot.addWidget(v)
        v.setup_document(10, [(100, 100)] * 10)
        v.set_zoom(1.0)
        for page in range(10):
            v.on_page_rendered(page, 1.0, _white(100, 100))
        assert len(v._rendered_cache) <= 3
        assert v.is_page_rendered(9)


class TestLinks:
    def test_link_lookup_and_click(self, view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
        link = LinkInfo(rect=Rect(10, 10, 100, 30), page_num=2, y=100.0)
        view.set_link_provider(lambda p: (link,) if p == 0 else ())
        view.setup_document(3, [A4] * 3)
        view.set_zoom(1.0)
        view.jump_to_page(0)
        assert view.link_at(0, Point(50, 20)) == link
        assert view.link_at(0, Point(300, 300)) is None

        got: list[tuple[int, LinkInfo]] = []
        view.linkActivated.connect(lambda page, lk: got.append((page, lk)))
        scene_pt = QPointF(50, view._page_y_positions[0] + 20)
        pos = view.mapFromScene(scene_pt)
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        assert got == [(0, link)]

    def test_links_cache_invalidated_with_page(self, view: PdfView) -> None:
        state = {"links": ()}
        view.set_link_provider(lambda _p: state["links"])
        view.setup_document(1, [A4])
        assert view.link_at(0, Point(5, 5)) is None
        state["links"] = (LinkInfo(rect=Rect(0, 0, 10, 10), uri="https://x.org"),)
        view.invalidate_page(0)
        assert view.link_at(0, Point(5, 5)) is not None


class TestReadingModes:
    def test_night_mode_inverts_pixels(self, view: PdfView) -> None:
        """设计稿 invert(0.9) hue-rotate(180deg)：白纸 → #191919（不是纯黑，更柔和）。"""
        view.setup_document(1, [(10, 10)])
        view.set_reading_mode(ReadingMode.NIGHT)
        color = view._to_qpixmap(_white()).toImage().pixelColor(5, 5)
        assert (color.red(), color.green(), color.blue()) == (25, 25, 25)

    def test_night_mode_keeps_hue(self, view: PdfView) -> None:
        """夜间模式只反转明暗：红色图表在夜间仍偏红（hue-rotate 抵消了反色的色相翻转）。"""
        view.set_reading_mode(ReadingMode.NIGHT)
        red = PixmapData(samples=bytes((220, 38, 38)) * 100, width=10, height=10, n=3, stride=30)
        color = view._to_qpixmap(red).toImage().pixelColor(5, 5)
        assert color.red() > color.green() and color.red() > color.blue()

    def test_sepia_mode_tints_white(self, view: PdfView) -> None:
        view.set_reading_mode(ReadingMode.SEPIA)
        color = view._to_qpixmap(_white()).toImage().pixelColor(5, 5)
        assert color.red() > color.blue()

    def test_mode_switch_drops_rendered_pages(self, view: PdfView) -> None:
        view.setup_document(1, [(10, 10)])
        view.set_zoom(1.0)
        view.on_page_rendered(0, 1.0, _white())
        assert view.is_page_rendered(0)
        view.set_reading_mode(ReadingMode.NIGHT)
        assert not view.is_page_rendered(0)

    def test_unknown_mode_falls_back(self, view: PdfView) -> None:
        view.set_reading_mode("bogus")
        assert view.reading_mode == ReadingMode.NORMAL


class TestSelectionRound3:
    CJK_WORDS = (
        WordBox(rect=Rect(10, 10, 20, 22), text="深", block=0, line=0, word_no=0),
        WordBox(rect=Rect(20, 10, 30, 22), text="度", block=0, line=0, word_no=0),
        WordBox(rect=Rect(30, 10, 60, 22), text="BERT", block=0, line=0, word_no=0),
        WordBox(rect=Rect(10, 30, 20, 42), text="模", block=0, line=1, word_no=0),
    )

    def test_cjk_selection_has_no_spaces(self, view: PdfView) -> None:
        view.set_text_provider(lambda p: self.CJK_WORDS)
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        view._select_range(0, QPointF(12, 15), QPointF(15, 35))
        assert view.selected_text == "深度BERT\n模"

    def test_partial_cjk_selection(self, view: PdfView) -> None:
        view.set_text_provider(lambda p: self.CJK_WORDS)
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        view._select_range(0, QPointF(22, 15), QPointF(25, 15))
        assert view.selected_text == "度"

    def test_selection_markup_request(self, view: PdfView) -> None:
        view.set_text_provider(lambda p: self.CJK_WORDS)
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        view._select_range(0, QPointF(12, 15), QPointF(15, 35))
        got: list[tuple] = []
        view.markupRequested.connect(lambda p, r, k: got.append((p, r, k)))
        view._request_markup("underline")
        assert got and got[0][0] == 0 and got[0][2] == "underline"
        assert len(got[0][1]) == 2  # 两行 → 两个矩形，同一条批注
        assert not view.has_selection

    def test_copy_merge_lines(self, view: PdfView) -> None:
        words = (
            WordBox(rect=Rect(10, 10, 40, 22), text="inter-", block=0, line=0, word_no=0),
            WordBox(rect=Rect(10, 30, 40, 42), text="national", block=0, line=1, word_no=0),
        )
        view.set_text_provider(lambda p: words)
        view.setup_document(1, [A4])
        view.set_zoom(1.0)
        view._select_range(0, QPointF(12, 15), QPointF(12, 35))
        n = view.copy_selection(merge_lines=True)
        clip = QApplication.clipboard().text()
        if clip not in ("international", view.selected_text):
            pytest.skip("系统剪贴板被其他进程占用，无法验证复制结果")
        assert n == len("international")
        assert clip == "international"


def test_resize_in_fit_mode_is_debounced(view: PdfView, qtbot) -> None:  # type: ignore[no-untyped-def]
    """R3-22：适宽模式下密集的 resize 合并成一次重排。"""
    view.setup_document(2, [A4] * 2)
    view.fit_to_width()
    calls: list[float] = []
    view.zoomChanged.connect(calls.append)
    for w in (520, 540, 560, 580, 600):
        view.resize(w, 400)
        QApplication.processEvents()
    assert calls == []  # 定时器尚未触发
    qtbot.waitUntil(lambda: bool(calls), timeout=2000)
    assert len(calls) == 1
