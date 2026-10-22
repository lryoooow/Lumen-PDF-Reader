"""主窗口 ⇄ 文献库首页：首页 / 阅读器切换、快捷键停用、收藏、视图、AI 入口、拖放高亮与文档摘要。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox

from pdfreader.config.settings import MAX_LIBRARY_FILES, DocMeta
from pdfreader.gui.main_window import MainWindow
from pdfreader.services.bibliography import Reference


def _make_pdf(path: Path, pages: int = 3, title: str = "") -> Path:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"{path.stem} page {i + 1}")
    if title:
        d.set_metadata({"title": title, "author": "Lin et al."})
    d.save(str(path))
    d.close()
    return path


def test_adopted_bibliography_survives_reload_and_open_record(
    window: MainWindow, tmp_path: Path
) -> None:
    source = _make_pdf(tmp_path / "bibliography.pdf", title="Embedded title")
    window.open_path(source)
    reference = Reference("paper", "Adopted title", ("Smith, John",), "2024", "10.1000/test")
    window._apply_reference(reference)
    from pdfreader.config.settings import SettingsStore

    loaded = SettingsStore().load()
    meta = loaded.meta_for(source)
    assert meta is not None and meta.bibliography and meta.doi == "10.1000/test"
    window._settings = loaded
    window._record_meta(window.current_tab, opened=True)
    assert window._settings.meta_for(source).title == "Adopted title"


@pytest.fixture
def window(qtbot, monkeypatch) -> MainWindow:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    yield w
    for tab in w.tabs:
        if tab.controller.document is not None:
            tab.controller.document.mark_saved()
    w.close()


def test_home_and_return_keep_tabs_and_park_shortcuts(window: MainWindow, tmp_path: Path) -> None:
    assert window._library_visible() and window.windowTitle() == "Lumen"
    assert window.close_doc_action.shortcuts() == []  # 首页时阅读器快捷键停用
    assert window.open_action.shortcuts()  # 「打开」照常可用
    a = _make_pdf(tmp_path / "a.pdf", 4, "Sparse Attention Networks")
    window.open_path(a)
    assert window._shell.currentWidget() is window._reader
    assert window.close_doc_action.shortcuts()
    assert window.ribbon.home_tooltip() == "a.pdf"

    window.pdf_view.restore_position(2, 0.0)
    window.ribbon.homeRequested.emit()
    assert window._library_visible() and window.windowTitle() == "Lumen"
    assert window.close_doc_action.shortcuts() == []
    assert window.library.return_button.isVisibleTo(window.library)
    assert window.library.return_button.toolTip() == "回到 a.pdf"
    (entry,) = window.library.entries()
    assert entry.title == "Sparse Attention Networks" and entry.author == "Lin et al."
    assert entry.is_open and entry.pages == 4 and entry.progress == pytest.approx(0.75)
    window._go_home()  # 已在首页：无事发生
    assert window._library_visible()

    window.library.returnRequested.emit()
    assert window._shell.currentWidget() is window._reader
    assert window.close_doc_action.shortcuts()
    assert len(window.tabs) == 1 and window.windowTitle() != "Lumen"
    window._show_reader()  # 已在阅读器：无事发生

    # 首页点已打开的文档 → 切回它的标签，不重复打开
    window._go_home()
    window.library.openRecentRequested.emit(str(a))
    assert window._shell.currentWidget() is window._reader and len(window.tabs) == 1


def test_closing_last_doc_returns_to_library_with_progress(
    window: MainWindow, tmp_path: Path
) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 5)
    window.open_path(a)
    window.pdf_view.restore_position(1, 0.0)
    window.close_doc_action.trigger()
    assert window._library_visible()
    assert not window.library.return_button.isVisibleTo(window.library)
    (entry,) = window.library.entries()
    assert not entry.is_open and entry.pages == 5
    assert entry.progress == pytest.approx(0.4)  # 来自保存的阅读位置
    assert entry.opened_at > 0 and entry.title == "a"  # 无元数据标题 → 文件名


def test_missing_files_are_hidden_and_favorites_persist(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf")
    window._settings.add_recent_file(tmp_path / "gone.pdf")
    window._settings.add_recent_file(a)
    window._refresh_library()
    assert window.library.entry_paths() == [str(a)]

    window.library.favoriteToggled.emit(str(a))
    assert window._settings.is_favorite(a)
    assert window.library.entries()[0].favorite
    window.library.favoriteToggled.emit(str(a))
    assert not window._settings.is_favorite(a)

    window.library.view_buttons["list"].click()
    assert window._settings.library_view == "list"
    assert window._settings_store.load().library_view == "list"


def test_explore_ai_opens_latest_doc_then_panel(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", lambda *a, **k: (calls.append(True), ([], ""))[1]
    )
    window.library.exploreAiRequested.emit()  # 文献库是空的 → 弹「打开」对话框
    assert calls == [True] and window._library_visible()

    a = _make_pdf(tmp_path / "a.pdf")
    window._settings.add_recent_file(a)
    window._refresh_library()
    window.library.exploreAiRequested.emit()
    assert window._tab.has_document and window._shell.currentWidget() is window._reader
    assert window.ai_panel.isVisible()


def test_drag_highlights_import_zone(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(a)), QUrl.fromLocalFile(str(tmp_path / "x.txt"))])
    buttons = Qt.MouseButton.LeftButton
    mods = Qt.KeyboardModifier.NoModifier
    window.dragEnterEvent(
        QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, buttons, mods)
    )
    assert window.library.quiet_import.property("dragging") == "true"
    window.dragLeaveEvent(QDragLeaveEvent())
    assert window.library.quiet_import.property("dragging") == "false"
    window.dragEnterEvent(
        QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, buttons, mods)
    )
    window.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, buttons, mods))
    assert window.library.quiet_import.property("dragging") == "false"
    assert window._tab.has_document and window._controller.document.path == a  # type: ignore[union-attr]


def test_meta_recorded_on_open_and_save(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 3, "Hierarchical Sparse Attention")
    window._settings.remember_meta(a, DocMeta(annotations=7, opened_at=0.0))
    window.open_path(a)
    meta = window._settings.meta_for(a)
    count = window._tab.annotation_count  # 侧栏批注页扫描过就用新数，否则沿用旧值
    assert meta is not None and meta.pages == 3
    assert meta.annotations == (7 if count is None else count)
    assert meta.title == "Hierarchical Sparse Attention" and meta.opened_at > 0
    opened_at = meta.opened_at

    window._tab.annotation_count = 2
    window._controller.rotate_page(0, 90)
    window.save_action.trigger()
    meta = window._settings.meta_for(a)
    assert meta is not None and meta.annotations == 2 and meta.opened_at == opened_at
    assert window._settings_store.load().meta_for(a) == meta
    window._tab.annotation_count = None  # 没重新数过 → 沿用记下的数
    window._record_meta(window._tab, opened=False)
    assert window._settings.meta_for(a).annotations == 2  # type: ignore[union-attr]


def test_recent_list_holds_library_capacity(window: MainWindow, tmp_path: Path) -> None:
    for i in range(3):
        window.open_path(_make_pdf(tmp_path / f"{i}.pdf", 1))
    assert len(window._settings.recent_files) == 3
    for i in range(MAX_LIBRARY_FILES + 5):
        window._settings.add_recent_file(f"/x/{i}.pdf", max_count=MAX_LIBRARY_FILES)
    assert len(window._settings.recent_files) == MAX_LIBRARY_FILES


def test_doc_tabs_size_and_icons(window: MainWindow, tmp_path: Path) -> None:
    window.open_path(_make_pdf(tmp_path / "a.pdf", 1))
    window.open_path(_make_pdf(tmp_path / ("very_long_name_" * 8 + ".pdf"), 1))
    bar = window.tab_bar
    assert window._tab_strip.isVisible()
    short, long = bar.tabSizeHint(0), bar.tabSizeHint(1)
    assert short.height() == long.height() == 32
    assert 80 <= short.width() < 220 and long.width() == 220
    assert bar.minimumTabSizeHint(0).width() == 80
    assert not bar.tabIcon(0).isNull() and not bar.tabIcon(1).isNull()
    window._theme.set_mode("dark" if not window._theme.is_dark else "light")
    assert not bar.tabIcon(1).isNull()
    assert window._tab_add_button.toolTip().startswith("打开 PDF")
