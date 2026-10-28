"""文献库首页：纯函数（标题 / 时间 / 过滤）与 LibraryHome 的分区、文件夹、搜索、视图与断点。"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QPushButton, QScrollArea, QWidget

from pdfreader.gui.theme.manager import get_theme
from pdfreader.gui.widgets import motion
from pdfreader.gui.widgets.library import (
    ALL_FOLDERS,
    LibraryEntry,
    LibraryHome,
    cover_variant,
    display_title,
    filter_entries,
    relative_time,
)


def _entries() -> list[LibraryEntry]:
    return [
        LibraryEntry("/lib/ml/attention.pdf", "Sparse Attention", "Lin", 12, 3, 0.5, 100.0),
        LibraryEntry("/lib/ml/bert.pdf", "BERT", "Devlin", 8, 0, 0.0, 50.0, favorite=True),
        LibraryEntry("/lib/report/annual.pdf", "年度综述", "王", 30, 0, 0.2, 10.0, is_open=True),
    ]


# ---------------------------------------------------------------- 纯函数
def test_display_title_falls_back_to_file_name() -> None:
    assert display_title("  Hierarchical   Sparse Attention ", "/a/x.pdf") == (
        "Hierarchical Sparse Attention"
    )
    assert display_title("Microsoft Word - 年度报告", "/a/x.pdf") == "年度报告"
    for junk in ("", "ab", "untitled", "Title", "report.docx", "Microsoft Word - a.doc"):
        assert display_title(junk, "/a/论文.pdf") == "论文"
    assert len(display_title("x" * 300, "/a/b.pdf")) == 120


def test_relative_time_buckets() -> None:
    now = 1_000_000_000.0
    assert relative_time(0) == ""
    assert relative_time(now - 10, now) == "刚刚阅读"
    assert relative_time(now + 50, now) == "刚刚阅读"  # 时钟回拨不出负数
    assert relative_time(now - 125, now) == "2 分钟前"
    assert relative_time(now - 3 * 3600, now) == "3 小时前"
    assert relative_time(now - 4 * 86400, now) == "4 天前"
    old = now - 90 * 86400
    assert relative_time(old, now) == time.strftime("%Y-%m-%d", time.localtime(old))
    assert relative_time(time.time() - 5) == "刚刚阅读"


def test_cover_variant_is_stable_and_bounded() -> None:
    values = {cover_variant(f"/p/{i}.pdf") for i in range(40)}
    assert values <= {0, 1, 2, 3} and len(values) > 1
    assert cover_variant("/p/a.pdf") == cover_variant("/p/a.pdf")


def test_filter_entries_sections_folders_query_and_sort() -> None:
    es = _entries()
    names = lambda items: [e.name for e in items]  # noqa: E731
    assert names(filter_entries(es)) == ["attention", "bert", "annual"]
    assert names(filter_entries(es, section="最近阅读")) == ["attention", "annual"]
    assert names(filter_entries(es, section="我的收藏")) == ["bert"]
    assert names(filter_entries(es, section="含批注文档")) == ["attention"]
    assert names(filter_entries(es, folder="report")) == ["annual"]
    assert names(filter_entries(es, query=" devlin ")) == ["bert"]
    assert names(filter_entries(es, query="综述")) == ["annual"]
    assert names(filter_entries(es, query="ML")) == ["attention", "bert"]
    assert names(filter_entries(es, sort="名称排序")) == ["annual", "attention", "bert"]
    e = es[0]
    assert e.name == "attention" and e.folder == "ml" and e.read
    assert not es[1].read


# ---------------------------------------------------------------- LibraryHome
@pytest.fixture
def home(qtbot):  # type: ignore[no-untyped-def]
    w = LibraryHome(get_theme())
    qtbot.addWidget(w)
    w.resize(1280, 820)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_empty_library_offers_import(home: LibraryHome) -> None:
    assert home.entry_paths() == []
    assert home._empty.isVisibleTo(home)
    assert home.empty_title.text() == "文献库还是空的"
    got: list[bool] = []
    home.openRequested.connect(lambda: got.append(True))
    home.empty_button.click()
    home.continue_card.button.click()  # 没有文档时「继续」也是导入
    home.quiet_import.click()
    home.import_button.click()
    assert got == [True] * 4
    assert home.continue_card.title.text() == "打开第一篇文献"


def test_sections_folders_and_search(home: LibraryHome) -> None:
    home.set_entries(_entries())
    assert home.folders() == ["ml", "report"]
    assert home.nav_items["我的收藏"].count.text() == "01"
    assert len(home._collections) == 2 and len(home._chips) == 3
    assert not home._empty.isVisibleTo(home)

    home.set_section("我的收藏")
    assert home.entry_paths() == ["/lib/ml/bert.pdf"]
    assert home.heading.text() == "我的收藏" and home.heading_count.text() == "01"
    home.set_section("含批注文档")
    assert "PDF 批注" in home.heading_desc.text()
    home.set_section("不存在")  # 无效分区忽略
    assert home.section == "含批注文档"

    home.reset_filters()
    home._collections[1].click()  # 合集 → 全部文档 + 该文件夹
    assert home.section == "全部文档" and home.folder == "report"
    assert home.entry_paths() == ["/lib/report/annual.pdf"]
    assert [c.isChecked() for c in home._chips] == [False, False, True]
    home._chips[0].click()
    assert home.folder == ALL_FOLDERS
    home.set_folder("nope")
    assert home.folder == ALL_FOLDERS

    home.search.setText("zzz")
    assert home.entry_paths() == []
    assert home.empty_title.text() == "这里还没有文档"
    home.empty_button.click()  # 有文档时空态按钮 = 清除筛选
    assert home.search.text() == "" and len(home.entry_paths()) == 3

    home.sort.setCurrentText("名称排序")
    assert home.entry_paths()[0] == "/lib/report/annual.pdf"

    home.set_folder("report")
    home.set_entries(_entries()[:2])  # 文件夹没了 → 回到全部
    assert home.folder == ALL_FOLDERS


def test_annotation_documents_section_matches_name_scope_and_counts(home: LibraryHome) -> None:
    home.set_entries(_entries())
    assert "含批注文档" in home.nav_items
    assert "阅读笔记" not in home.nav_items
    item = home.nav_items["含批注文档"]
    assert item.count.text() == "01"
    assert item.accessibleName() == "含批注文档"
    assert item.toolTip() == "含批注文档"

    item.click()
    assert home.section == "含批注文档" and item.isChecked()
    assert home.heading.text() == home.crumb.text() == "含批注文档"
    assert home.heading_count.text() == "01"
    assert "PDF 批注" in home.heading_desc.text()
    assert "Markdown" in home.heading_desc.text()
    assert home.heading_desc.wordWrap()  # 范围说明不能挤掉窄窗口中的搜索与视图入口
    assert home.entry_paths() == ["/lib/ml/attention.pdf"]

    home.search.setText("Lin")
    assert home.entry_paths() == ["/lib/ml/attention.pdf"]
    home.search.setText("Devlin")
    assert home.entry_paths() == []  # 非批注文档即使匹配作者也不混入
    assert home.heading_count.text() == "00" and item.count.text() == "01"
    assert home.empty_title.text() == "没有匹配的批注文档"
    home.empty_button.click()
    assert home.section == "全部文档" and home.search.text() == ""
    assert len(home.entry_paths()) == 3


def test_annotation_section_empty_state_remains_accurate_after_refresh(home: LibraryHome) -> None:
    home.set_entries(_entries()[1:])
    home.set_section("含批注文档")
    assert home.entry_paths() == []
    assert home.empty_title.text() == "还没有含批注的文档"
    assert "独立 Markdown 笔记" in home.empty_body.text()
    assert home.empty_button.text() == "查看全部文档"

    home.set_entries(_entries())
    assert home.section == "含批注文档"
    assert home.entry_paths() == ["/lib/ml/attention.pdf"]
    assert home.nav_items["含批注文档"].isChecked()
    assert not home._empty.isVisibleTo(home)
    home.apply_width(700)  # 只留图标时仍由名称与提示表达真实范围
    assert home.nav_items["含批注文档"].toolTip() == "含批注文档"


def test_short_window_keeps_navigation_search_recovery_and_return_reachable(
    home: LibraryHome, qtbot
) -> None:  # type: ignore[no-untyped-def]
    home._theme.apply()  # 使用真实字体/QSS尺寸，而非未应用主题的控件默认值
    home.set_entries(_entries())
    home.set_can_return(True)
    home.set_recovery_notice(["待恢复文献.pdf"])
    home.resize(1024, 768)
    home.activateWindow()
    qtbot.waitUntil(lambda: home.size() == QSize(1024, 768))

    def fully_inside(widget: QWidget, container: QWidget) -> bool:
        bounds = QRect(widget.mapTo(container, QPoint()), widget.size())
        return container.rect().contains(bounds)

    assert isinstance(home._nav, QScrollArea)
    qtbot.waitUntil(lambda: home._nav.horizontalScrollBar().maximum() == 0)
    assert home._nav.verticalScrollBar().maximum() > 0
    assert home._nav.horizontalScrollBar().maximum() == 0, [
        (child.objectName(), child.minimumSizeHint().width(), child.width())
        for child in home._nav.findChildren(QWidget)
        if child.minimumSizeHint().width() > 140
    ]
    assert fully_inside(home.return_button, home)
    assert fully_inside(home._recovery_banner, home)
    returned: list[bool] = []
    restored: list[bool] = []
    home.returnRequested.connect(lambda: returned.append(True))
    home.recoveryRestoreRequested.connect(lambda: restored.append(True))
    qtbot.mouseClick(home.return_button, Qt.MouseButton.LeftButton)
    restore = home.findChild(QPushButton, "RecoveryRestore")
    assert restore is not None and fully_inside(restore, home)
    qtbot.mouseClick(restore, Qt.MouseButton.LeftButton)
    assert returned == [True] and restored == [True]

    # 即使主体滚到底部，Ctrl+K 的焦点入口也应将搜索真正带回可见视口。
    home.scroll_area.verticalScrollBar().setValue(home.scroll_area.verticalScrollBar().maximum())
    qtbot.keyClick(home, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(home.search.hasFocus)
    qtbot.waitUntil(lambda: fully_inside(home.search, home.scroll_area.viewport()))
    qtbot.keyClicks(home.search, "Devlin")
    assert home.entry_paths() == ["/lib/ml/bert.pdf"]
    assert fully_inside(home.return_button, home)  # 主体滚动不带走返回与恢复操作

    # Tab 到低处的导航按钮时，滚动容器应自动展示焦点，而非仅保留被裁剪的按钮。
    home.nav_items["含批注文档"].setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(12):
        if home.profile.hasFocus():
            break
        qtbot.keyClick(home.focusWidget(), Qt.Key.Key_Tab)
    assert home.profile.hasFocus()
    qtbot.waitUntil(lambda: fully_inside(home.profile, home._nav.viewport()))
    assert home._nav.verticalScrollBar().value() > 0
    toggled: list[bool] = []
    home.themeToggleRequested.connect(lambda: toggled.append(True))
    qtbot.keyClick(home.profile, Qt.Key.Key_Space)
    assert toggled == [True]

    home.resize(1360, 860)
    qtbot.waitUntil(lambda: home._nav.verticalScrollBar().maximum() == 0)
    assert fully_inside(home.profile, home._nav.viewport())
    home.resize(700, 768)
    qtbot.waitUntil(lambda: home._nav.width() == 68)
    qtbot.waitUntil(lambda: home._nav.horizontalScrollBar().maximum() == 0)
    assert home.nav_items["含批注文档"].toolTip() == "含批注文档"


def test_cards_and_continue_emit_paths(home: LibraryHome) -> None:
    home.set_entries(_entries())
    opened: list[str] = []
    starred: list[str] = []
    home.openRecentRequested.connect(opened.append)
    home.favoriteToggled.connect(starred.append)
    card = home._cards[0]
    card.title_btn.click()
    card.star.click()
    home.continue_card.button.click()
    assert opened == ["/lib/ml/attention.pdf", "/lib/ml/attention.pdf"]
    assert starred == ["/lib/ml/attention.pdf"]
    assert "3 条批注" in card.info.text()
    assert home._cards[2].when.text() == "正在阅读"
    explore: list[bool] = []
    home.exploreAiRequested.connect(lambda: explore.append(True))
    home.insight_card.button.click()
    assert explore == [True]


def test_view_mode_return_and_dragging(home: LibraryHome) -> None:
    home.set_entries(_entries())
    modes: list[str] = []
    home.viewModeChanged.connect(modes.append)
    home.view_buttons["list"].click()
    assert home.view_mode == "list" and modes == ["list"]
    assert all(card.compact for card in home._cards)
    home.set_view_mode("list", notify=True)  # 没变不重复通知
    home.set_view_mode("bogus")
    home.set_view_mode("grid")  # 程序设置默认不通知
    assert modes == ["list"] and home.view_mode == "grid"

    home.set_can_return(True, "回到 a")
    assert home.return_button.isVisible() and home.return_button.toolTip() == "回到 a"
    home.set_can_return(False)
    assert not home.return_button.isVisible()
    home.set_dragging(True)
    assert home.quiet_import.property("dragging") == "true"
    home.set_dragging(False)
    assert home.quiet_import.property("dragging") == "false"
    home.focus_search()
    themes: list[bool] = []
    home.themeToggleRequested.connect(lambda: themes.append(True))
    home.theme_button.click()
    assert themes == [True]


@pytest.mark.parametrize(
    ("width", "cls", "nav"),
    [(1600, "xl", 228), (1200, "lg", 228), (1000, "md", 195), (700, "sm", 68), (400, "xs", 68)],
)
def test_apply_width_breakpoints(home: LibraryHome, width: int, cls: str, nav: int) -> None:
    home.set_entries(_entries())
    home.apply_width(width)
    assert home.width_class == cls
    assert home._nav.width() == nav or home._nav.minimumWidth() == nav
    assert home._columns == (4 if cls in ("xl", "lg", "md") else 2)
    assert home._aside.isVisibleTo(home) == (cls in ("xl", "lg"))
    assert home._col_box.isVisibleTo(home) == (cls not in ("sm", "xs"))


def test_animate_in_and_theme_switch(home: LibraryHome, qtbot) -> None:  # type: ignore[no-untyped-def]
    home.set_entries(_entries())
    theme = get_theme()
    mode = theme.mode
    motion.set_enabled(True)
    try:
        home.animate_in()
        qtbot.wait(50)
    finally:
        motion.set_enabled(False)
    theme.set_mode("dark")
    assert theme.palette.accent in home._title.text()
    theme.set_mode(mode)
    home.grab()  # 封面 / 星标 / 进度条 / 场景都能绘制
