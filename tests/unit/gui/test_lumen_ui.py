"""Lumen 设计稿对接测试：逐项核对设计稿中的界面元素都接到了真实功能上。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfreader.controllers import AnnotationMode
from pdfreader.engines.types import Rect, WordBox
from pdfreader.gui.main_window import _HIGHLIGHT_COLORS, MainWindow
from pdfreader.gui.panels.sidebar import SIDEBAR_WIDTH
from pdfreader.gui.theme.palette import DARK, LIGHT
from pdfreader.gui.widgets import motion
from pdfreader.gui.widgets.pdf_view import PdfView, ReadingMode


def _make_paper(path: Path, pages: int = 4) -> Path:
    """带目录与参考文献链接的小论文。"""
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 80), f"{i + 1} Section", fontsize=16)
        p.insert_text((72, 120), "Transformers are strong [1].", fontsize=11)
    d[pages - 1].insert_text((72, 300), "[1] Vaswani A. Attention is all you need.", fontsize=11)
    words = [w for w in d[0].get_text("words") if w[4] == "[1]."]
    if words:
        x0, y0, x1, y1 = words[0][:4]
        d[0].insert_link(
            {
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(x0, y0, x1 - 3, y1),
                "page": pages - 1,
                "to": pymupdf.Point(72, 290),
            }
        )
    d.set_toc([[1, "1 Intro", 1], [2, "1.1 Background", 2], [1, "References", pages]])
    d.save(str(path))
    d.close()
    return path


@pytest.fixture
def window(qtbot, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 820)
    w.show()
    qtbot.waitExposed(w)
    yield w
    if w._controller.document is not None:
        w._controller.document.mark_saved()
        w._controller.close_document()


# ---------------------------------------------------------------- 标题栏 / 功能区
class TestHeaderAndRibbon:
    def test_brand_and_document_title(self, window: MainWindow, tmp_path: Path) -> None:
        assert window.ribbon._brand.text() == "Lumen"
        assert window.ribbon._doc_label.text() == ""
        window.open_path(_make_paper(tmp_path / "paper.pdf"))
        assert window.ribbon._doc_label.text() == " / paper.pdf"
        window._controller.add_highlight(0, Rect(72, 100, 200, 125))
        window._update_title()
        assert window.ribbon._doc_label.text() == " / paper.pdf *"
        assert window.windowTitle() == "* paper.pdf — Lumen"

    def test_ribbon_groups_follow_design(self, window: MainWindow) -> None:
        titles = [
            [lbl.text() for lbl in window.ribbon.group_titles(i)]
            for i in range(window.ribbon._stack.count())
        ]
        assert titles == [
            ["文件", "编辑", "工具", "查找与复制"],
            ["文字标记", "绘制", "插入"],
            ["当前页 · 第 1 页"],
            ["布局", "缩放", "阅读"],
            ["识别", "输出", "文档"],
            ["全文", "划词与框选", "模型"],
        ]

    def test_page_group_title_tracks_current_page(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "p.pdf", 5))
        window.pdf_view.jump_to_page(3)
        assert window._page_group_title.text() == "当前页 · 第 4 页"

    def test_dropdown_buttons_open_menus(self, window: MainWindow) -> None:
        for act in (window.ocr_action, window.reading_mode_action, window.color_action):
            btn = window.ribbon.button_for(act)
            assert btn is not None and btn.menu() is not None
            assert btn.property("dropdown") == "true"

    def test_tab_indicator_sits_under_checked_tab(self, window: MainWindow) -> None:
        window.ribbon._tab_group.button(3).click()
        QApplication.processEvents()
        tab = window.ribbon._tab_group.button(3)
        ind = window.ribbon._indicator.geometry()
        assert tab.geometry().left() <= ind.left() <= tab.geometry().right()


# ---------------------------------------------------------------- 批注工具 / 提示胶囊 / 颜色
class TestTools:
    def test_tool_hint_follows_mode(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "t.pdf"))
        window.highlight_action.trigger()
        assert window.tool_hint.isVisible()
        assert "拖拽选字以高亮" in window.tool_hint.text()
        window.ink_action.trigger()
        assert "按住拖动手写" in window.tool_hint.text()
        window.select_action.trigger()
        assert not window.tool_hint.isVisible()
        assert window.status_bar.message() == "已退出批注工具"

    def test_swatch_sets_highlight_color(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "c.pdf"))
        name, color = _HIGHLIGHT_COLORS[2]  # 蓝
        window._swatches[color].click()
        assert window._settings.highlight_color == color
        assert window.pdf_view.highlight_color == color
        assert window._annotation_controller.mode == AnnotationMode.HIGHLIGHT
        assert window._swatches[color].property("current") == "true"
        assert window.status_bar.message() == f"高亮颜色：{name}"

    def test_underline_tool_drag_creates_underline(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        window.open_path(_make_paper(tmp_path / "u.pdf"))
        window.underline_action.trigger()
        view = window.pdf_view
        view._annot_page = 0
        view._update_highlight_preview(0, QPointF(75, 115), QPointF(150, 115), underline=True)
        view._commit_highlight(kind="underline")
        annots = window._controller.list_annotations(0)
        assert [a.type_name for a in annots] == ["下划线"]
        assert window.status_bar.message().startswith("已添加下划线")

    def test_timestamp_is_exclusive_tool_and_reverts(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        window.open_path(_make_paper(tmp_path / "s.pdf"))
        window.timestamp_action.trigger()
        assert window._annotation_controller.mode == AnnotationMode.TIMESTAMP
        assert window.timestamp_action.isChecked()
        assert "点击页面放置时间" in window.tool_hint.text()
        window.pdf_view.clear_pending()  # Esc / 放置完成
        assert window._annotation_controller.mode == AnnotationMode.NONE
        assert window.select_action.isChecked()

    def test_copy_merged_button(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "m.pdf"))
        window.copy_merge_action.trigger()
        assert window.status_bar.message() == "请先用「选择」工具选中文字"

    def test_translate_without_selection_hints(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "tr.pdf"))
        window.translate_action.trigger()
        assert window.status_bar.message() == "请先用「选择」工具选中文字"


# ---------------------------------------------------------------- 阅读模式 / 状态栏
class TestReadingModeAndStatusBar:
    def test_reading_mode_menu_chip_and_checks(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "r.pdf"))
        window._mode_actions[ReadingMode.SEPIA].trigger()
        assert window.pdf_view.reading_mode == ReadingMode.SEPIA
        assert window.reading_mode_action.isChecked()
        assert window._mode_actions[ReadingMode.SEPIA].isChecked()
        assert window.status_bar._mode_chip.text() == "护眼"
        window._mode_actions[ReadingMode.NORMAL].trigger()
        assert not window.status_bar._mode_chip.isVisible()
        assert not window.reading_mode_action.isChecked()

    def test_zoom_slider_drives_view(self, window: MainWindow, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_paper(tmp_path / "z.pdf"))
        window.status_bar._zoom_slider.setValue(150)
        qtbot.waitUntil(lambda: window.pdf_view.zoom_percent == 150, timeout=2000)
        assert window.status_bar._zoom_lbl.text() == "150%"

    def test_progress_bar_tracks_page(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "pg.pdf", 4))
        window.pdf_view.jump_to_page(3)
        assert window.status_bar._progress.value() == 1000

    def test_message_auto_hides(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        from pdfreader.gui.theme import get_theme
        from pdfreader.gui.widgets import StatusBar

        bar = StatusBar(get_theme())
        qtbot.addWidget(bar)
        bar.show()
        bar._hide_timer.setInterval(50)
        bar.show_message("已保存")
        assert bar.message() == "已保存"
        qtbot.waitUntil(lambda: bar.message() == "", timeout=2000)


# ---------------------------------------------------------------- 侧边栏 / 引用预览 / 欢迎页
class TestSidebarCiteWelcome:
    def test_sidebar_width_animation(self, window: MainWindow, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_paper(tmp_path / "sb.pdf"))
        motion.set_enabled(True)
        window._set_sidebar_visible(False)
        qtbot.waitUntil(lambda: not window.sidebar.isVisible(), timeout=3000)
        window._set_sidebar_visible(True)
        qtbot.waitUntil(lambda: window.sidebar.width() == SIDEBAR_WIDTH, timeout=3000)
        assert window._settings.sidebar_visible is True

    def test_toc_marks_current_section(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "toc.pdf", 4))
        window.pdf_view.jump_to_page(3)
        refs = window.sidebar.toc_tree.topLevelItem(1)
        intro = window.sidebar.toc_tree.topLevelItem(0)
        assert refs.text(0) == "References" and refs.text(1) == "4"
        assert refs.font(0).weight() > intro.font(0).weight()

    def test_citation_link_preview_shows_reference_text(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        window.open_path(_make_paper(tmp_path / "cite.pdf", 4))
        links = window._controller.get_links(0)
        assert links, "样例应含参考文献链接"
        window._on_link_activated(0, links[0])
        assert window.cite_popover.isVisible()
        assert window.cite_popover.title.text() == "参考文献 [1]"
        assert "Vaswani" in window.cite_popover.body.text()
        window.cite_popover.jump_btn.click()
        assert window.pdf_view.current_page_num == 3

    def test_welcome_title_and_recent(self, window: MainWindow, tmp_path: Path) -> None:
        assert "安静" in window.welcome._title.text()
        path = _make_paper(tmp_path / "recent.pdf")
        window.welcome.set_recent([str(path), str(tmp_path / "gone.pdf")])
        assert window.welcome.recent_paths() == [str(path)]
        got: list[str] = []
        window.welcome.openRecentRequested.connect(got.append)
        window.welcome._rows[0].click()
        assert got == [str(path)]


# ---------------------------------------------------------------- 阅读区视觉与动效
class TestReadingAreaVisuals:
    def test_pages_centered_with_shadow(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        view = PdfView()
        qtbot.addWidget(view)
        view.setup_document(2, [(600, 800), (400, 300)])
        view.set_zoom(1.0)
        assert view._page_x_positions == [0.0, 100.0]
        assert len(view._frame_items[1]) == 4

    def test_click_mapping_respects_centering(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        """窄页水平居中后，点击换算到 PDF 坐标必须扣掉页左偏移。"""
        view = PdfView()
        qtbot.addWidget(view)
        view.resize(800, 600)
        view.show()
        qtbot.waitExposed(view)
        words = (WordBox(rect=Rect(10, 10, 60, 22), text="Hi", block=0, line=0, word_no=0),)
        view.set_text_provider(lambda p: words if p == 1 else ())
        view.setup_document(2, [(600, 800), (200, 300)])
        view.set_zoom(1.0)
        view.jump_to_page(1)
        x0, _y0 = view._page_origin(1)
        rects = view._add_overlay_rects(1, [Rect(10, 10, 60, 22)], view.palette().text().color())
        assert rects[0].sceneBoundingRect().left() == x0 + 10

    def test_smooth_scroll_reaches_target(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        motion.set_enabled(True)
        view = PdfView()
        qtbot.addWidget(view)
        view.resize(500, 400)
        view.show()
        qtbot.waitExposed(view)
        view.setup_document(6, [(595, 842)] * 6)
        view.set_zoom(1.0)
        view.jump_to_page(4, animate=True)
        assert view._scroll_anim is not None
        qtbot.waitUntil(lambda: view.current_page_num == 4, timeout=3000)

    def test_pulse_and_flash_clean_up(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        motion.set_enabled(True)
        view = PdfView()
        qtbot.addWidget(view)
        view.setup_document(2, [(595, 842)] * 2)
        view.set_zoom(1.0)
        before = len(view.scene().items())
        view.pulse_page(1)
        view.flash_rects(0, [Rect(10, 10, 50, 30)], view.palette().text().color())
        assert len(view.scene().items()) > before
        qtbot.waitUntil(lambda: len(view.scene().items()) == before, timeout=4000)
        assert view._effects == []

    def test_sepia_matches_design_filter(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        from pdfreader.engines.types import PixmapData

        view = PdfView()
        qtbot.addWidget(view)
        view.set_reading_mode(ReadingMode.SEPIA)
        white = PixmapData(samples=b"\xff" * 300, width=10, height=10, n=3, stride=30)
        c = view._to_qpixmap(white).toImage().pixelColor(5, 5)
        # CSS：sepia(0.35) 后裁剪 → (1, 1, 0.978)，再 brightness(0.97) → (247, 247, 242)
        assert (c.red(), c.green(), c.blue()) == (247, 247, 242)


def test_palette_matches_design_tokens() -> None:
    assert LIGHT.window == "#F4F4F1" and DARK.window == "#0F1115"
    assert LIGHT.accent == DARK.accent == "#6366F1"
    assert DARK.surface == "#16181D" and DARK.popover == "#1D2027"
    assert [c for _n, c in _HIGHLIGHT_COLORS] == [
        "#FACC15",
        "#4ADE80",
        "#60A5FA",
        "#F472B6",
        "#FB923C",
    ]


# ---------------------------------------------------------------- 视觉验收发现的问题（回归）
class TestVisualReviewRegressions:
    def test_cjk_highlight_marked_text(self, window: MainWindow, tmp_path: Path) -> None:
        """批注卡片的引文：中文部分高亮也要提取到被标记的原文。"""
        path = tmp_path / "cjk.pdf"
        d = pymupdf.open()
        d.new_page().insert_text((72, 100), "我们提出了一种层级注意力机制", fontname="china-s")
        d.save(str(path))
        d.close()
        window.open_path(path)
        words = [w for w in window._controller.get_words(0) if w.text in "层级注意力"]
        rect = Rect(words[0].rect.x0, words[0].rect.y0, words[-1].rect.x1, words[-1].rect.y1)
        window._on_annot_highlight(0, (rect,))
        assert window._controller.list_annotations(0)[0].marked_text == "层级注意力"

    def test_search_panel_opens_top_right(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "s.pdf"))
        window._on_find()
        panel = window.search_panel
        right_gap = window._content.width() - (panel.x() + panel.width())
        assert 0 <= right_gap <= 24
        assert panel.y() <= 24

    def test_fit_width_has_no_horizontal_scrollbar(
        self, window: MainWindow, tmp_path: Path, qtbot
    ) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_paper(tmp_path / "f.pdf"))
        window.pdf_view.fit_to_width()
        QApplication.processEvents()
        bar = window.pdf_view.horizontalScrollBar()
        assert bar.maximum() == bar.minimum()

    def test_badge_counts_without_opening_tab(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "b.pdf"))
        window._set_sidebar_visible(True)
        window.sidebar.tabs.setCurrentIndex(0)  # 停在目录页签
        window._on_annot_highlight(0, (Rect(72, 105, 200, 125),))
        assert window.sidebar.annot_count.text() == "1"

    def test_thumbnails_in_two_columns(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_paper(tmp_path / "t.pdf", 4))
        window._set_sidebar_visible(True)
        window.sidebar.tabs.setCurrentIndex(2)
        QApplication.processEvents()
        lst = window.sidebar.thumb_list
        first, second = lst.visualItemRect(lst.item(0)), lst.visualItemRect(lst.item(1))
        assert first.top() == second.top() and second.left() > first.left()
