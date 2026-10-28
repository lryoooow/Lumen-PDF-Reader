"""伴随笔记：真实主窗下的来源身份、焦点、草稿和退出安全。"""

import pymupdf
import pytest
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from pdfreader.engines.types import Point
from pdfreader.gui.main_window import MainWindow
from pdfreader.services.notebook import source_anchors


@pytest.fixture
def workspace(qtbot, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow(restore_session=False)
    qtbot.addWidget(w)
    paths = []
    for name in ("alpha", "beta"):
        p = tmp_path / (name + ".pdf")
        with pymupdf.open() as d:
            d.new_page().insert_text((72, 72), name + " source")
            d.new_page()
            d.save(p)
        paths.append(p)
    w.resize(1024, 768)
    w.show()
    assert w.open_path(paths[0])
    a = w.current_tab
    assert a.controller.add_text_annot(1, Point(70, 70), "原文评论")
    a.controller.document.mark_saved()
    QTimer.singleShot(
        50,
        lambda: (
            QApplication.activeModalWidget().close()
            if QApplication.activeModalWidget() is not None
            else None
        ),
    )
    w._on_notebook()
    panel = w._notebook
    assert panel is not None
    qtbot.waitUntil(lambda: not panel._timer.isActive())
    yield w, panel, a, paths
    if w._notebook is not None:
        w._notebook.editor.document().setModified(False)
    for tab in w.tabs:
        if tab.controller.document is not None:
            tab.controller.document.mark_saved()
    w.close()


def test_nonmodal_readable_source_and_saved_roundtrip(
    workspace, qtbot, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, _ = workspace
    assert QApplication.activeModalWidget() is None
    assert panel.isVisibleTo(w)
    assert a.path.name in panel.source_label.text()
    panel._insert_source(panel.sources.item(0))
    assert "source:" not in panel.editor.toPlainText()
    assert "原文评论" in panel.editor.toPlainText()
    before = panel.editor.textCursor().position()
    anchors = source_anchors(panel._markdown())
    assert len(anchors) == 1
    target = tmp_path / "note.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert panel.save()
    panel.load_path(target)
    assert source_anchors(panel._markdown()) == anchors
    panel._follow(QUrl("source:" + anchors[0].fingerprint))
    assert w.current_tab is a
    assert w.pdf_view.current_page_num == 1
    assert "原文评论" in panel.editor.toPlainText()
    # 回源没有重建或覆盖笔记文档。
    assert before > 0
    panel.editor.setFocus()
    assert panel.editor.hasFocus()


def test_source_from_background_activates_original_tab(workspace) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, paths = workspace
    panel._insert_source(panel.sources.item(0))
    anchor = source_anchors(panel._markdown())[0]
    cursor = panel.editor.textCursor().position()
    assert w.open_path(paths[1])
    b = w.current_tab
    w._on_notebook()
    assert w._notebook is panel and a.path.name in panel.source_label.text()
    panel._follow(QUrl("source:" + anchor.fingerprint))
    assert w.current_tab is a and b.view.current_page_num == 0
    assert panel.editor.textCursor().position() == cursor


def test_closed_source_keeps_draft_and_never_rebinds_same_path(workspace) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, paths = workspace
    panel._insert_source(panel.sources.item(0))
    body = panel._markdown()
    anchor = source_anchors(body)[0]
    assert w._close_tab(a)
    assert panel._markdown() == body
    assert "已关闭" in panel.source_label.text()
    assert w.library.return_button.isVisibleTo(w.library)
    assert "笔记" in w.library.return_button.text()
    w._return_to_reader()
    assert panel.isVisibleTo(w)
    assert w.open_path(paths[0])
    b = w.current_tab
    panel._follow(QUrl("source:" + anchor.fingerprint))
    assert w.current_tab is b and b.view.current_page_num == 0
    assert "失效" in panel.status.text()


def test_unsaved_quit_cancel_keeps_documents_and_recovery(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, _ = workspace
    panel.editor.insertPlainText("不能丢失的草稿")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not w.close()
    assert a.has_document and w._autosave_timer.isActive()
    assert "不能丢失" in panel.editor.toPlainText()
    assert panel.isEnabled()


def test_notebook_shortcuts_do_not_save_or_undo_pdf(workspace, qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, _ = workspace
    called = []
    monkeypatch.setattr(panel, "save", lambda: called.append("note-save") or True)
    panel.editor.setFocus()
    qtbot.keyClicks(panel.editor, "draft")
    qtbot.keyClick(panel.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert called == ["note-save"]
    qtbot.keyClick(panel.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not a.controller.is_modified
    qtbot.keyClick(panel.editor, Qt.Key.Key_Escape)
    assert w._notebook is panel and a.has_document


def test_narrow_layout_preserves_header_and_reader(workspace, qtbot) -> None:  # type: ignore[no-untyped-def]
    w, panel, _, _ = workspace
    qtbot.waitUntil(lambda: w._notebook_dock.width() >= 300)
    assert w.width() == 1024
    assert w.ribbon.width() == w._reader.width()
    assert w._stack.width() >= 360
    assert panel.width() >= 290


def test_save_failure_keeps_dirty_draft(workspace, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    panel.editor.insertPlainText("保留草稿")
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "missing" / "note.md"), "")
    )
    assert not panel.save()
    assert panel.editor.document().isModified()
    assert "失败" in panel.status.text()


def test_drag_source_is_readable_and_preserves_anchor(workspace) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    item = panel.sources.item(0)
    data = panel.sources.mimeData([item])
    assert "source:" not in data.text()
    panel.visual_editor.insertFromMimeData(data)
    assert "source:" not in panel.editor.toPlainText()
    assert len(source_anchors(panel._markdown())) == 1


def test_legacy_markdown_remains_exact_and_preview_does_not_modify(workspace, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    text = "# 原笔记\n\n<!-- 私有扩展 -->\n\n[[双链]]\n\n<table><tr><td>x</td></tr></table>\n"
    p = tmp_path / "legacy.md"
    p.write_text(text, encoding="utf-8")
    panel.load_path(p)
    assert panel.mode.currentIndex() == 2
    assert panel._body() == text
    panel.mode.setCurrentIndex(1)
    panel.mode.setCurrentIndex(0)
    assert panel.mode.currentIndex() == 2
    assert panel._body() == text
    assert not panel.editor.document().isModified()


def test_encrypted_source_requires_confirmation_after_source_closed(
    workspace, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, _ = workspace
    # 模拟已确认的加密来源标志；关闭控制器后 is_encrypted 会变 False。
    panel._encrypted_source = True
    assert w._close_tab(a)
    prompts = []
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "clear.md"), "")
    )

    def decline(_parent, title, *args, **kwargs):
        prompts.append(title)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    assert not panel.save()
    assert prompts == ["导出加密文档的笔记"]
    assert not (tmp_path / "clear.md").exists()


def test_pending_backlinks_keeps_window_owned_until_finished(
    workspace, qtbot, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import threading

    from pdfreader.gui.dialogs import notebook_dialog
    from pdfreader.services.notebook import Backlinks
    from pdfreader.workers.notebook_worker import BacklinksWorker

    w, panel, a, _ = workspace
    entered, release = threading.Event(), threading.Event()

    class Delayed(BacklinksWorker):
        def run(self) -> None:
            entered.set()
            release.wait(5)
            self.result.emit(Backlinks((), ()))

    monkeypatch.setattr(notebook_dialog, "BacklinksWorker", Delayed)
    panel._path = tmp_path / "note.md"
    panel.backlinks()
    worker = panel._links_worker
    try:
        qtbot.waitUntil(entered.is_set)
        assert not w.close()
        assert a.has_document and w._autosave_timer.isActive()
        assert panel._links_worker is worker and not panel.isEnabled()
    finally:
        release.set()
        assert worker.wait(5000)
    qtbot.waitUntil(lambda: not w.isVisible())
    assert not a.has_document


def test_click_visible_source_in_editor_returns_to_bound_document(workspace, qtbot) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, paths = workspace
    panel._insert_source(panel.sources.item(0))
    cursor = panel.visual_editor.document().find("alpha.pdf · 第 2 页")
    assert not cursor.isNull()
    cursor.setPosition(cursor.selectionStart() + 2)
    point = panel.visual_editor.cursorRect(cursor).center()
    assert w.open_path(paths[1])
    w._on_notebook()
    qtbot.mouseClick(panel.visual_editor.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert w.current_tab is a and a.view.current_page_num == 1


def test_visual_wikilink_survives_preview_save_and_reopen(
    workspace, qtbot, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    panel.editor.insertPlainText("\n[[target]]\n")
    panel.mode.setCurrentIndex(1)
    assert 'href="note:target"' in panel.preview.toHtml()
    note = tmp_path / "main.md"
    target = tmp_path / "target.md"
    target.write_text("# Target\n\n目标笔记正文\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(note), ""))
    assert panel.save()
    assert "[[target]]" in note.read_text("utf-8")
    panel.load_path(note)
    panel.mode.setCurrentIndex(1)
    assert 'href="note:target"' in panel.preview.toHtml()
    panel.preview.anchorClicked.emit(QUrl("note:target"))
    assert panel._path == target and "目标笔记正文" in panel.editor.toPlainText()


def test_escaped_wikilink_stays_literal_after_edit_and_preview(workspace, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    note = tmp_path / "literal.md"
    note.write_text("\\[[target]]\n", encoding="utf-8")
    panel.load_path(note)
    panel.editor.insertPlainText("保留字面链接 ")
    panel.mode.setCurrentIndex(1)
    assert 'href="note:target"' not in panel.preview.toHtml()
    assert "\\[[target]]" in panel._body()


def test_visual_valid_and_escaped_wikilinks_with_same_title_stay_distinct(workspace) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    panel.editor.insertPlainText("\n[[same]] 与 \\[[same]]\n")
    panel.mode.setCurrentIndex(1)
    assert panel.preview.toHtml().count('href="note:same"') == 1


@pytest.mark.parametrize("drag", [False, True])
@pytest.mark.parametrize(
    "filename", ["paper] [draft.pdf", "_paper_.pdf", "`paper`.pdf", "~paper~.pdf"]
)
def test_source_filename_brackets_remain_clickable(
    workspace, qtbot, tmp_path, drag, filename
) -> None:  # type: ignore[no-untyped-def]
    w, old, _, paths = workspace
    old.close()
    file = tmp_path / filename
    with pymupdf.open() as d:
        d.new_page()
        d.save(file)
    assert w.open_path(file)
    source = w.current_tab
    source.controller.add_text_annot(0, Point(70, 70), "带括号的来源")
    source.controller.document.mark_saved()
    w._on_notebook()
    panel = w._notebook
    qtbot.waitUntil(lambda: not panel._timer.isActive())
    item = panel.sources.item(0)
    if drag:
        panel.visual_editor.insertFromMimeData(panel.sources.mimeData([item]))
    else:
        panel._insert_source(item)
    assert 'href="source:' in panel.visual_editor.toHtml()
    for index, view in ((0, panel.visual_editor), (1, panel.preview)):
        panel.mode.setCurrentIndex(index)
        assert 'href="source:' in view.toHtml(), (panel._body(), panel.visual_editor.toMarkdown())
        assert w.open_path(paths[1])
        w._on_notebook()
        cursor = view.document().find(filename + " · 第 1 页")
        assert not cursor.isNull()
        cursor.setPosition(cursor.selectionStart() + 2)
        qtbot.mouseClick(
            view.viewport(), Qt.MouseButton.LeftButton, pos=view.cursorRect(cursor).center()
        )
        assert w.current_tab is source
    panel.editor.document().setModified(False)


def test_source_mode_drag_preserves_anchor_through_save_reopen(
    workspace, qtbot, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    w, panel, a, paths = workspace
    legacy = tmp_path / "source-mode.md"
    legacy.write_text("<!-- 保留扩展 -->\n\n# 旧笔记\n", encoding="utf-8")
    panel.load_path(legacy)
    assert panel.mode.currentIndex() == 2
    panel.source_editor.insertFromMimeData(panel.sources.mimeData([panel.sources.item(0)]))
    assert "source:" in panel.source_editor.toPlainText()
    assert len(source_anchors(panel._markdown())) == 1
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(legacy), ""))
    assert panel.save()
    panel.load_path(legacy)
    assert len(source_anchors(panel._markdown())) == 1
    panel.mode.setCurrentIndex(1)
    assert 'href="source:' in panel.preview.toHtml()
    anchor = source_anchors(panel._markdown())[0]
    assert w.open_path(paths[1])
    panel.preview.anchorClicked.emit(QUrl("source:" + anchor.fingerprint))
    assert w.current_tab is a and a.view.current_page_num == 1


def test_repeated_source_in_same_paragraph_keeps_each_link(workspace) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    panel._insert_source(panel.sources.item(0))
    anchor = source_anchors(panel._markdown())[0]
    href = "source:" + anchor.fingerprint
    panel.visual_editor.setHtml(
        f'<p><a href="{href}">first] paper.pdf</a> 普通正文 <a href="{href}">second] paper.pdf</a></p>'
    )
    panel.mode.setCurrentIndex(1)
    assert panel.preview.toHtml().count('href="source:') == 2
    assert len(source_anchors(panel._markdown())) == 1


@pytest.mark.parametrize("container", ["paragraph", "heading", "list", "quote"])
def test_source_with_inline_formatting_keeps_source_links(workspace, container) -> None:  # type: ignore[no-untyped-def]
    _, panel, _, _ = workspace
    panel._insert_source(panel.sources.item(0))
    anchor = source_anchors(panel._markdown())[0]
    href = "source:" + anchor.fingerprint
    anchor_html = f'<a href="{href}">paper]<b> bold</b>.pdf</a>'
    wrappers = {
        "paragraph": ("<p>", "</p>"),
        "heading": ("<h2>", "</h2>"),
        "list": ("<ul><li>", "</li></ul>"),
        "quote": ("<blockquote><p>", "</p></blockquote>"),
    }
    before, after = wrappers[container]
    panel.visual_editor.setHtml(before + anchor_html + after)
    panel.mode.setCurrentIndex(1)
    assert f'href="{href}"' in panel.preview.toHtml()
    assert panel.preview.toPlainText().strip() == "paper] bold.pdf"
