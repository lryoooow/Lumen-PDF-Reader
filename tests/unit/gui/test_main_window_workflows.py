"""主窗口端到端流程：编辑作用于当前页、导出路径、会话重置、加密文档、导航与阅读位置等。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from pdfreader.engines.types import LinkInfo, Rect
from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.panels import SidePanel

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
ENCRYPTED = SAMPLES / "encrypted.pdf"


def _make_pdf(path: Path, pages: int) -> Path:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"page {i + 1} hello world")
    d.set_toc([[1, "Intro", 1], [1, "End", pages]])
    d.save(str(path))
    d.close()
    return path


@pytest.fixture
def window(qtbot, monkeypatch) -> MainWindow:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1000, 800)
    w.show()
    qtbot.waitExposed(w)
    yield w
    if w._controller.document is not None:
        w._controller.document.mark_saved()
        w._controller.close_document()


def _rotations(window: MainWindow) -> list[int]:
    doc = window._controller.document
    assert doc is not None and doc._handle is not None
    return [int(doc._handle.native[i].rotation) for i in range(doc.page_count)]  # type: ignore[attr-defined]


class TestEditTargetsCurrentPage:
    def test_rotate_twice_stays_on_same_page(self, window: MainWindow, tmp_path: Path) -> None:
        """R3-01：旧实现第二次旋转转的是第 1 页。"""
        window.open_path(_make_pdf(tmp_path / "a.pdf", 8))
        window.pdf_view.jump_to_page(3)
        window._on_rotate_page()
        window._on_rotate_page()
        assert _rotations(window) == [0, 0, 0, 180, 0, 0, 0, 0]
        assert window.pdf_view.current_page_num == 3

    def test_delete_after_rotate_deletes_intended_page(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        window.open_path(_make_pdf(tmp_path / "d.pdf", 5))
        window.pdf_view.jump_to_page(2)
        window._on_rotate_page()
        window._on_delete_page()
        assert window._controller.page_count == 4
        doc = window._controller.document
        assert doc is not None
        texts = [doc.get_words(i)[1].text for i in range(4)]
        assert texts == ["1", "2", "4", "5"]  # 第 3 页被删

    def test_undo_keeps_current_page(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_pdf(tmp_path / "u.pdf", 6))
        window.pdf_view.jump_to_page(4)
        window._on_rotate_page()
        window._on_undo()
        assert window.pdf_view.current_page_num == 4

    def test_delete_disabled_for_single_page(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_pdf(tmp_path / "one.pdf", 1))
        assert window.delete_page_action.isEnabled() is False


class TestExportPaths:
    def test_export_reads_snapshot_but_defaults_to_real_dir(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """R3-02：旧实现默认输出在快照临时目录，退出时被整个删除。"""
        from pdfreader.gui.dialogs import export_dialog

        seen: dict[str, object] = {}

        def fake_exec(self) -> int:  # type: ignore[no-untyped-def]
            seen["source"] = self._source
            seen["out"] = self._path_edit.text()
            return 0

        monkeypatch.setattr(export_dialog.ExportDialog, "exec", fake_exec)
        path = _make_pdf(tmp_path / "paper.pdf", 2)
        window.open_path(path)
        window._controller.add_highlight(0, Rect(72, 60, 200, 80))
        window._on_export()
        assert str(seen["source"]).startswith(str(window._snapshot_dir))
        assert seen["out"] == str(tmp_path / "paper")

    def test_cleanup_keeps_foreign_files_in_snapshot_dir(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        window._snapshot_dir = tmp_path / "snapshots"  # 与同进程其他窗口隔离
        window.open_path(_make_pdf(tmp_path / "p.pdf", 1))
        window._controller.add_highlight(0, Rect(72, 60, 200, 80))
        snap = window._worker_source()
        assert snap is not None
        user_file = window._snapshot_dir / "我的导出.docx"
        user_file.write_bytes(b"x")
        window._cleanup_snapshots()
        assert not snap.exists()
        assert user_file.exists(), "退出清理只能删自己创建的快照"


class TestSessionReset:
    def test_switching_document_drops_old_search(self, window: MainWindow, tmp_path: Path) -> None:
        """R3-09：切换文档后旧文档的搜索结果不能出现在新文档面板。"""
        window.open_path(_make_pdf(tmp_path / "old.pdf", 8))
        window._on_search_requested("hello")
        old_worker = window._search_worker
        window.open_path(_make_pdf(tmp_path / "new.pdf", 2))
        assert window._search_worker is None
        assert window._active_query == ""
        if old_worker is not None:
            old_worker.wait(5000)
        for _ in range(20):
            QApplication.processEvents()
        assert window.search_panel._result_list.count() == 0


class TestEncryptedDocuments:
    def test_password_prompt_opens_document(self, window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """R3-03：加密文档在界面上可以输入密码打开。"""
        prompts: list[str] = []

        def fake_get_text(*args, **kwargs):  # type: ignore[no-untyped-def]
            prompts.append(args[2])
            return ("secret", True)

        monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
        assert window.open_path(ENCRYPTED) is True
        assert window._controller.password == "secret"
        assert len(prompts) == 1 and "受密码保护" in prompts[0]

    def test_wrong_password_retries_then_gives_up(self, window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        prompts: list[str] = []

        def fake_get_text(*args, **kwargs):  # type: ignore[no-untyped-def]
            prompts.append(args[2])
            return ("nope", True)

        monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
        assert window.open_path(ENCRYPTED) is False
        assert len(prompts) == 3
        assert "密码错误" in prompts[1]
        assert window._controller.has_document is False

    def test_cancel_password_prompt(self, window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))
        assert window.open_path(ENCRYPTED) is False

    def test_search_on_encrypted_document(self, window: MainWindow, monkeypatch, qtbot) -> None:  # type: ignore[no-untyped-def]
        """后台搜索线程要拿到密码，否则加密文档必然搜索失败。"""
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("secret", True))
        window.open_path(ENCRYPTED)
        window._on_search_requested("Encrypted")
        worker = window._search_worker
        assert worker is not None
        with qtbot.waitSignal(worker.searchCompleted, timeout=10000):
            pass
        qtbot.waitUntil(lambda: window.search_panel._result_list.count() == 1, timeout=5000)


class TestSaveFeedback:
    def test_failed_save_explains_before_save_as(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """R3-07：保存失败要先说明原因，而不是直接弹出另存为。"""
        warnings: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(text)
        )
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        window.open_path(_make_pdf(tmp_path / "s.pdf", 1))
        window._controller.add_highlight(0, Rect(72, 60, 200, 80))
        monkeypatch.setattr(window._controller, "save_in_place", lambda: False)
        window._controller._last_error = "磁盘已满"
        assert window._do_save() is False
        assert warnings and "磁盘已满" in warnings[0]


class TestAnnotations:
    def test_multiline_highlight_is_single_annotation(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        window.open_path(_make_pdf(tmp_path / "h.pdf", 1))
        window._on_annot_highlight(0, (Rect(72, 60, 200, 76), Rect(72, 90, 200, 106)))
        assert len(window._controller.list_annotations(0)) == 1
        window._on_undo()
        assert window._controller.list_annotations(0) == []

    def test_highlight_failure_is_reported(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """R3-12：批注失败不能静默。"""
        warnings: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(title)
        )
        window.open_path(_make_pdf(tmp_path / "f.pdf", 1))
        window._on_annot_highlight(5, (Rect(0, 0, 10, 10),))  # 越界页
        assert warnings == ["添加高亮失败"]

    def test_annotation_panel_edit_and_export(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_pdf(tmp_path / "n.pdf", 2))
        window._on_markup_requested(1, (Rect(72, 60, 200, 80),), "underline")
        window.sidebar_action.setChecked(True)
        window._set_sidebar_visible(True)
        window.sidebar.tabs.setCurrentIndex(SidePanel.TAB_ANNOTATIONS)
        assert window.sidebar.annot_list.count() == 1

        annot = window._controller.list_all_annotations()[0]
        monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("核心观点", True))
        window._on_annotation_edit(annot)
        assert window._controller.list_all_annotations()[0].content == "核心观点"
        assert "核心观点" in window.sidebar.annot_list.item(0).text()

        out = tmp_path / "notes.md"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
        window._on_export_notes()
        text = out.read_text(encoding="utf-8")
        assert "## 第 2 页" in text and "核心观点" in text


class TestNavigation:
    def test_internal_link_and_back_forward(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_pdf(tmp_path / "l.pdf", 6))
        window.pdf_view.jump_to_page(1)
        # 设计稿：单击内部链接先弹预览卡，点「跳转到条目 →」才跳
        window._on_link_activated(1, LinkInfo(rect=Rect(0, 0, 1, 1), page_num=4, y=None))
        assert window.cite_popover.isVisible()
        assert window.pdf_view.current_page_num == 1
        window.cite_popover.jump_btn.click()
        assert not window.cite_popover.isVisible()
        assert window.pdf_view.current_page_num == 4
        assert window.back_action.isEnabled()
        window._go_back()
        assert window.pdf_view.current_page_num == 1
        assert window.forward_action.isEnabled()
        window._go_forward()
        assert window.pdf_view.current_page_num == 4

    def test_unsafe_external_link_blocked(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        opened: list[object] = []
        from PySide6.QtGui import QDesktopServices

        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url))
        window.open_path(_make_pdf(tmp_path / "x.pdf", 1))
        window._on_link_activated(0, LinkInfo(rect=Rect(0, 0, 1, 1), uri="file:///C:/Windows"))
        assert opened == []
        window._on_link_activated(0, LinkInfo(rect=Rect(0, 0, 1, 1), uri="https://arxiv.org"))
        assert len(opened) == 1

    def test_toc_loaded_into_sidebar(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_pdf(tmp_path / "t.pdf", 3))
        assert window.sidebar.toc_tree.topLevelItemCount() == 2
        window.sidebar.tocActivated.emit(2, None)
        assert window.pdf_view.current_page_num == 2

    def test_goto_page_out_of_range(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_pdf(tmp_path / "g.pdf", 3))
        window._goto_page(99)
        assert window.pdf_view.current_page_num == 0


class TestReadingPositionMemory:
    def test_reopen_restores_page(self, window: MainWindow, tmp_path: Path) -> None:
        path = _make_pdf(tmp_path / "r.pdf", 8)
        window.open_path(path)
        window.pdf_view.jump_to_page(5)
        window._on_close_document()
        window.open_path(path)
        assert window.pdf_view.current_page_num == 5

    def test_missing_recent_file_removed(self, window: MainWindow, tmp_path: Path) -> None:
        missing = tmp_path / "gone.pdf"
        window._settings.add_recent_file(missing)
        assert window.open_path(missing) is False
        assert str(missing) not in window._settings.recent_files


class TestOcrResults:
    def test_results_shown_in_dialog(self, window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """R3-21：OCR 结果在可复制 / 另存的对话框里完整展示。"""
        from pdfreader.gui.dialogs import ocr_result_dialog

        shown: list[str] = []

        def fake_exec(self) -> int:  # type: ignore[no-untyped-def]
            shown.append(self.text_edit.toPlainText())
            return 0

        monkeypatch.setattr(ocr_result_dialog.OcrResultDialog, "exec", fake_exec)
        window._ocr_results = {1: "第二页文字", 0: "第一页文字"}
        # 无 sender 的事件没有来源身份，应由真实任务信号触发。
        from pdfreader.workers import OcrWorker

        assert window._controller.open_file(SAMPLES / "simple_text.pdf")
        worker = OcrWorker(window)
        window._ocr_worker = worker
        window.current_tab.ocr_document = window._controller.document
        worker.ocrCompleted.connect(window._on_ocr_all_done)
        window._ocr_results = {1: "第二页文字", 0: "第一页文字"}
        worker.ocrCompleted.emit(2)
        assert shown and shown[0].index("第一页文字") < shown[0].index("第二页文字")


def test_file_from_argv(tmp_path: Path) -> None:
    """R3-15：双击 / 「打开方式」传入的 PDF 路径会被识别。"""
    from pdfreader.main import file_from_argv

    pdf = _make_pdf(tmp_path / "cmd.pdf", 1)
    assert file_from_argv(["app.exe", str(pdf)]) == pdf
    assert file_from_argv(["app.exe", "--selftest"]) is None
    assert file_from_argv(["app.exe", str(tmp_path / "missing.pdf")]) is None
    assert file_from_argv(["app.exe", str(tmp_path)]) is None


def test_close_waits_for_running_worker(window: MainWindow, tmp_path: Path) -> None:
    """R3-10：后台线程仍在运行时关闭窗口，必须先等它结束（否则 QThread 析构崩溃）。"""
    import time

    from pdfreader.workers import OcrWorker

    class _SlowWorker(OcrWorker):
        def run(self) -> None:
            deadline = time.monotonic() + 0.8
            while self._running and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.6)  # 模拟取消后仍要跑完当前页

    window.open_path(_make_pdf(tmp_path / "c.pdf", 1))
    worker = _SlowWorker(window)
    window._ocr_worker = worker
    worker.start()
    window._controller.document.mark_saved()  # type: ignore[union-attr]
    window.close()
    assert not worker.isRunning()


def test_search_panel_steps_through_results(qtbot) -> None:  # type: ignore[no-untyped-def]
    """F3 / Shift+F3：循环切换结果并发出定位信号。"""
    from pdfreader.engines.types import SearchHit
    from pdfreader.gui.panels import SearchPanel

    panel = SearchPanel()
    qtbot.addWidget(panel)
    assert panel.select_relative(1) is False
    hits = tuple(SearchHit(page_num=i, rect=Rect(0, 0, 1, 1), text=f"t{i}") for i in range(3))
    panel.show_results("t", hits)
    pages: list[int] = []
    panel.resultClicked.connect(lambda page, _rect: pages.append(page))
    for _ in range(4):
        panel.select_relative(1)
    panel.select_relative(-1)
    assert pages == [0, 1, 2, 0, 2]


class TestFreeText:
    def test_place_free_text_with_tool(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        from pdfreader.controllers.annotation_controller import AnnotationMode
        from pdfreader.engines.types import AnnotationType, Point

        window.open_path(_make_pdf(tmp_path / "f.pdf", 2))
        window.freetext_action.trigger()
        assert window._annotation_controller.mode == AnnotationMode.FREETEXT
        monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("页边批注", True))
        window.pdf_view.annotationNote.emit(1, Point(120, 200))
        (annot,) = window._controller.list_all_annotations()
        assert annot.annot_type == AnnotationType.FREE_TEXT
        assert annot.page_num == 1 and annot.content == "页边批注"
        assert "页边批注" in window.sidebar.annot_list.item(0).text()
        # 取消输入：不添加
        monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("", False))
        window.pdf_view.annotationNote.emit(1, Point(120, 400))
        assert len(window._controller.list_all_annotations()) == 1


def test_free_text_rect_sizes_and_clamps() -> None:
    from pdfreader.engines.types import Point
    from pdfreader.gui.main_window import free_text_rect

    page = Rect(0, 0, 600, 800)
    short = free_text_rect(Point(100, 100), "hi", page)
    assert short.x0 == 100 and short.y0 == 100 and short.width >= 60
    long = free_text_rect(Point(100, 100), "中" * 80, page)
    assert long.width <= 271 and long.height > short.height  # 最宽 260 后折行
    two = free_text_rect(Point(100, 100), "a\nb", page)
    assert two.height > short.height
    edge = free_text_rect(Point(590, 795), "靠右下角的文字", page)  # 超出页边就往回挪
    assert edge.x1 <= 600 and edge.y1 <= 800


class TestBookmarks:
    def test_add_rename_jump_delete(self, window: MainWindow, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        path = _make_pdf(tmp_path / "b.pdf", 5)
        window.open_path(path)
        window.pdf_view.jump_to_page(3)
        window.bookmark_action.trigger()
        marks = window.sidebar.bookmarks()
        assert len(marks) == 1 and marks[0].page == 3 and marks[0].title
        window.bookmark_action.trigger()  # 同一位置不重复添加
        assert len(window.sidebar.bookmarks()) == 1
        assert window.sidebar.tabs.currentIndex() == SidePanel.TAB_TOC

        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("方法部分", True))
        window._rename_bookmark(0)
        assert window.sidebar.bookmarks()[0].title == "方法部分"
        assert "方法部分" in window.sidebar.bookmark_list.topLevelItem(0).text(0)

        window.pdf_view.jump_to_page(0)
        window.sidebar._on_bookmark_clicked(window.sidebar.bookmark_list.topLevelItem(0))
        assert window.back_action.isEnabled()  # 跳书签记入后退历史

        # 重新打开同一文件：书签还在（存于设置，不改 PDF）
        window.open_path(path)
        assert [b.title for b in window.sidebar.bookmarks()] == ["方法部分"]
        window._delete_bookmark(0)
        assert window.sidebar.bookmarks() == []
        assert window._settings.bookmarks_for(path) == []
