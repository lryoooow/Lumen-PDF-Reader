from pathlib import Path

from pdfreader.gui.main_window import MainWindow
from tests.unit.engines.test_epub import make_epub

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"


def test_command_palette_shortcut_is_available_on_home(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(restore_session=False)
    qtbot.addWidget(window)
    assert window._library_visible()
    assert window.command_palette_action.shortcut().toString() == "Ctrl+Shift+P"
    assert "打开文档" in window._command_unavailable_reason(window.save_action)
    window.close()


def test_command_reasons_distinguish_selection_configuration_and_history(
    qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(restore_session=False)
    qtbot.addWidget(window)
    assert window.open_path(SAMPLES / "simple_text.pdf")
    monkeypatch.setattr(window._ai, "is_ready", lambda: False)
    assert "选中文字" in window._command_unavailable_reason(window.copy_merge_action)
    assert "配置模型" in window._command_unavailable_reason(window.ai_summary_action)
    assert "撤销" in window._command_unavailable_reason(window.undo_action)
    assert "搜索" in window._command_unavailable_reason(window.find_next_action)
    window._go_home()
    assert "返回阅读" in window._command_unavailable_reason(window.zoom_in_action)
    window.close()


def test_epub_does_not_reenable_ocr_and_explains_pdf_only_action(
    qtbot, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.gui import main_window

    monkeypatch.setattr(main_window, "OCR_AVAILABLE", True)
    window = MainWindow(restore_session=False)
    qtbot.addWidget(window)
    assert window.open_path(make_epub(tmp_path / "book.epub"))
    assert not window.ocr_action.isEnabled()
    assert "只读" in window._command_unavailable_reason(window.ocr_action)
    assert "只读" in window._command_unavailable_reason(window.highlight_action)
    window.close()


def test_failed_new_search_retires_old_state_and_disables_result_actions(
    qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.engines.types import Rect, SearchHit, SearchResult

    window = MainWindow(restore_session=False)
    qtbot.addWidget(window)
    assert window.open_path(SAMPLES / "simple_text.pdf")
    window._active_query = "old"
    old = SearchResult(query="old", hits=(SearchHit(0, Rect(0, 0, 1, 1)),))
    window._on_search_completed("old", old)
    assert window.find_next_action.isEnabled()
    monkeypatch.setattr(window, "_worker_source", lambda: None)
    window._on_search_requested("new")
    window._on_search_completed("old", old)
    assert not window.find_next_action.isEnabled()
    assert not window.search_panel.select_relative(1)
    assert "快照" in window.search_panel._status_label.text()
    window.close()


def test_cleared_query_rejects_already_queued_results(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    from pdfreader.engines.types import Rect, SearchHit, SearchResult
    from pdfreader.workers.search_worker import SearchWorker

    window = MainWindow(restore_session=False)
    qtbot.addWidget(window)
    assert window.open_path(SAMPLES / "simple_text.pdf")
    window.search_panel._input.setText("old")
    window._active_query = "old"
    worker = SearchWorker(window)
    window._search_worker = worker
    worker.searchCompleted.connect(window._on_search_completed, Qt.ConnectionType.QueuedConnection)
    worker.searchFailed.connect(window._on_search_failed, Qt.ConnectionType.QueuedConnection)
    worker.searchCompleted.emit("old", SearchResult("old", (SearchHit(0, Rect(0, 0, 1, 1)),)))
    worker.searchFailed.emit("旧查询失败")
    window.search_panel._input.clear()
    qtbot.wait(20)
    assert window.search_panel._result_list.count() == 0
    assert not window.find_next_action.isEnabled()
    assert window.search_panel._input.text() == ""
    assert "输入关键词" in window.search_panel._status_label.text()
    window.close()
