"""排队的 OCR 事件不能跨文档或修订回填。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from pdfreader.services.ocr import OcrResult
from pdfreader.workers import OcrWorker
from tests.unit.gui.test_main_window_tabs import docs as docs
from tests.unit.gui.test_main_window_tabs import window as window


def _queued(window, worker):  # type: ignore[no-untyped-def]
    for signal, slot in (
        (worker.pageOcrCompleted, window._on_ocr_page_done),
        (worker.progress, window._on_ocr_progress),
        (worker.ocrFailed, window._on_ocr_failed),
    ):
        signal.disconnect()
        signal.connect(slot, Qt.ConnectionType.QueuedConnection)


def test_retired_queued_ocr_does_not_reach_other_document(window, docs, qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(OcrWorker, "start", lambda self: None)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[1]))
    window.open_path(docs[0])
    a = window.current_tab
    window._start_ocr([0])
    old = a.ocr_worker
    _queued(window, old)
    old.pageOcrCompleted.emit(OcrResult(0, "A 的迟到内容", ()))
    old.progress.emit(1, 1)
    old.ocrFailed.emit("A 的迟到失败")
    window.open_path(docs[1])
    assert window._close_tab(a)
    b = window.current_tab
    window._start_ocr([0])
    progress = window._ocr_progress
    qtbot.wait(20)
    assert not b.ocr_results and not b.ai._ocr
    assert window._ocr_progress is progress and window._ocr_progress_owner is b
    assert not warnings


def test_edit_invalidates_ocr_snapshot_before_queued_result(
    window, docs, qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(OcrWorker, "start", lambda self: None)
    window.open_path(docs[0])
    window._start_ocr([1])
    tab = window.current_tab
    old = tab.ocr_worker
    _queued(window, old)
    old.pageOcrCompleted.emit(OcrResult(1, "旧第2页", ()))
    assert tab.controller.delete_page(0)
    qtbot.wait(20)
    assert not tab.ocr_results and not tab.ai._ocr
    assert tab.ocr_worker is None


def test_partial_ocr_reports_failed_pages_and_retry_keeps_success(
    window, docs, qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.gui.dialogs.ocr_result_dialog import OcrResultDialog

    monkeypatch.setattr(OcrWorker, "start", lambda self: None)
    seen = []

    def choose_retry(dialog):
        seen.append((dialog.windowTitle(), dialog.text_edit.toPlainText()))
        dialog._request_retry()
        return 1

    monkeypatch.setattr(OcrResultDialog, "exec", choose_retry)
    window.open_path(docs[0])
    window._start_ocr([0, 1])
    tab = window.current_tab
    old = tab.ocr_worker
    old.pageOcrCompleted.emit(OcrResult(0, "保留成功页", ()))
    old.pageFailed.emit(1, "页面渲染失败，可重试。")
    old.ocrCompleted.emit(2)
    assert seen and "部分完成" in seen[0][0]
    assert "第 2 页" in seen[0][1] and "保留成功页" in seen[0][1]
    old.finished.emit()
    qtbot.waitUntil(lambda: tab.ocr_worker is not None and tab.ocr_worker is not old)
    assert tab.ocr_worker._page_nums == [1]
    assert tab.ocr_results == {0: "保留成功页"}
    assert tab.ocr_failures == {1: "页面渲染失败，可重试。"}
    tab.ocr_worker.pageOcrCompleted.emit(OcrResult(1, "重试完成", ()))
    assert not tab.ocr_failures


def test_all_ocr_pages_failed_can_still_be_reviewed_and_retried(window, docs, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.gui.dialogs.ocr_result_dialog import OcrResultDialog

    monkeypatch.setattr(OcrWorker, "start", lambda self: None)
    seen = []
    monkeypatch.setattr(
        OcrResultDialog,
        "exec",
        lambda dialog: seen.append((dialog.windowTitle(), dialog.text_edit.toPlainText())) or 0,
    )
    window.open_path(docs[0])
    window._start_ocr([0])
    worker = window.current_tab.ocr_worker
    worker.pageFailed.emit(0, "页面渲染失败，可重试。")
    worker.ocrCompleted.emit(1)
    assert seen and "未完成" in seen[0][0] and "第 1 页" in seen[0][1]


def test_retiring_ocr_blocks_new_model_job(window, docs) -> None:  # type: ignore[no-untyped-def]
    class Retiring:
        def isRunning(self) -> bool:
            return True

    window.open_path(docs[0])
    old = Retiring()
    window._retired_ocr_workers.append(old)
    try:
        window._start_ocr([0])
        assert window.current_tab.ocr_worker is None
    finally:
        window._retired_ocr_workers.remove(old)


def test_cancelled_retry_retains_pages_that_have_not_been_retried(
    window, docs, qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.gui.dialogs.ocr_result_dialog import OcrResultDialog

    monkeypatch.setattr(OcrWorker, "start", lambda self: None)
    seen = []

    def review(dialog):
        seen.append(dialog.text_edit.toPlainText())
        if len(seen) == 1:
            dialog._request_retry()
        return 0

    monkeypatch.setattr(OcrResultDialog, "exec", review)
    window.open_path(docs[0])
    window._start_ocr([0, 1, 2])
    tab = window.current_tab
    old = tab.ocr_worker
    old.pageOcrCompleted.emit(OcrResult(1, "原成功页", ()))
    old.pageFailed.emit(0, "坏页")
    old.pageFailed.emit(2, "坏页")
    old.ocrCompleted.emit(3)
    old.finished.emit()
    qtbot.waitUntil(lambda: tab.ocr_worker is not None and tab.ocr_worker is not old)
    retry = tab.ocr_worker
    retry.pageOcrCompleted.emit(OcrResult(0, "重试成功", ()))
    retry.ocrCancelled.emit(1)
    assert tab.ocr_failures == {2: "坏页"}
    assert "第 3 页：坏页" in seen[1]
    assert tab.ocr_results == {0: "重试成功", 1: "原成功页"}
