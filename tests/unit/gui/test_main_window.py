"""主窗口测试。

阶段 3：验证主窗口能实例化、菜单结构正确、控制器接入正常。
端到端的"打开 PDF 渲染"测试见 tests/integration/。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from pdfreader.engines.types import Rect
from pdfreader.gui.main_window import MainWindow

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"


def test_main_window_instantiates(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window is not None


def test_main_window_title(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert "Lumen" in window.windowTitle()


def test_main_window_default_size(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.width() >= 640
    assert window.height() >= 480


def test_open_action_enabled(qtbot) -> None:  # type: ignore[no-untyped-def]
    """阶段 3：打开动作默认启用（阶段 0 是禁用的）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_action.isEnabled() is True


def test_has_controller_and_view(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._controller is not None
    assert window.pdf_view is not None


def test_no_document_initially(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._controller.has_document is False
    # 未打开文档时，编辑/视图相关动作应禁用
    assert window.zoom_in_action.isEnabled() is False
    assert window.close_doc_action.isEnabled() is False


def test_close_document_action_disabled_initially(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.close_doc_action.isEnabled() is False


def test_ribbon_has_expected_tabs(qtbot) -> None:  # type: ignore[no-untyped-def]
    """选项卡式功能区：开始 / 批注 / 页面 / 视图 / 工具 / AI 助读。"""
    window = MainWindow()
    qtbot.addWidget(window)
    ribbon = window.ribbon
    tabs = [ribbon._tab_group.button(i).text() for i in range(ribbon._stack.count())]
    assert tabs == ["开始", "批注", "页面", "视图", "工具", "AI 助读"]


def test_ribbon_button_for_returns_widget(qtbot) -> None:  # type: ignore[no-untyped-def]
    """button_for 能跨选项卡取到工具按钮（供 OCR 附加下拉菜单）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.ribbon.button_for(window.ocr_action) is not None
    assert window.ribbon.button_for(window.highlight_action) is not None


def test_ribbon_tab_switch_changes_page(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    ribbon = window.ribbon
    assert ribbon._stack.currentIndex() == 0
    ribbon._tab_group.button(1).click()
    assert ribbon._stack.currentIndex() == 1


def test_header_theme_button_toggles_light_dark(qtbot) -> None:  # type: ignore[no-untyped-def]
    """设计稿：主题切换在标题栏（浅 ⇄ 深），不再放在状态栏菜单里。"""
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_theme_mode("light")
    window.ribbon._theme_btn.click()
    assert window._theme.mode == "dark"
    window.ribbon._theme_btn.click()
    assert window._theme.mode == "light"


def test_theme_mode_signal_switches_theme(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_theme_mode("dark")
    assert window._theme.mode == "dark"
    assert window._theme.is_dark is True
    window._on_theme_mode("light")  # 复位，避免污染后续测试的全局样式
    assert window._theme.mode == "light"


def test_save_action_disabled_initially(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.save_action.isEnabled() is False


def test_maybe_save_true_when_clean(qtbot) -> None:  # type: ignore[no-untyped-def]
    """无文档/无改动时不弹窗，直接可继续。"""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._maybe_save() is True


def test_close_prompt_respects_cancel_and_discard(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))  # 变脏
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert window._maybe_save() is False  # 取消 → 不可继续（不关闭/不换文件）
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    assert window._maybe_save() is True  # 放弃改动 → 可继续
    window._controller.close_document()


def test_title_shows_dirty_marker(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))
    window._update_title()
    assert window.windowTitle().startswith("* ")
    window._controller.close_document()


def test_close_event_cancelled_keeps_document(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """脏文档时关闭窗口选“取消”→ 窗口不关闭、文档仍在（不丢数据）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert window.close() is False  # 关闭被取消
    assert window._controller.has_document is True
    window._controller.close_document()  # 清理，避免 teardown 再弹窗


def test_worker_source_uses_unique_snapshot_paths(qtbot) -> None:  # type: ignore[no-untyped-def]
    """原 P0-2：上一次快照仍被后台线程打开时，新快照必须换名而不是覆盖失败。"""
    from pdfreader.engines import PyMuPdfEngine

    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))
    first = window._worker_source()
    assert first is not None and first.exists()

    engine = PyMuPdfEngine()
    holder = engine.open(first)  # 模拟搜索/OCR 线程仍持有旧快照
    window._controller.add_highlight(0, Rect(40, 100, 300, 130))
    second = window._worker_source()

    assert second is not None
    assert second != first
    assert second.exists()
    check = engine.open(second)
    assert len(engine.list_annotations(check, 0)) == 2  # 新快照含最新改动
    check.close()
    holder.close()
    window._controller.close_document()


def test_worker_source_failure_returns_none(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """快照失败时不得退回磁盘旧文件（会让搜索/OCR 看到过时内容）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))
    monkeypatch.setattr(window._controller, "save_copy", lambda _path: False)
    assert window._worker_source() is None
    window._controller.close_document()


def test_stale_search_result_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    """慢文档上"先发后到"的旧查询结果不得覆盖新结果（R2-P1-2）。"""
    from pdfreader.engines.types import SearchHit, SearchResult

    window = MainWindow()
    qtbot.addWidget(window)
    window._active_query = "new-query"
    stale = SearchResult(
        query="old-query", hits=(SearchHit(page_num=0, rect=Rect(0, 0, 10, 10), text="x"),)
    )

    window._on_search_completed("old-query", stale)  # 旧查询的结果：忽略
    assert window.search_panel._hits == []

    window._on_search_completed("new-query", stale)  # 与当前查询一致：采纳
    assert len(window.search_panel._hits) == 1


def test_print_document_renders_and_can_be_cancelled(qtbot, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """打印：带进度可取消（返回 False），未取消则逐页渲染进打印机。"""
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QProgressDialog

    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")

    def make_printer(name: str) -> QPrinter:
        printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path / name))
        return printer

    # 正常打印：3 页全部渲染，输出文件非空
    out = tmp_path / "print.pdf"
    assert window._print_document(make_printer("print.pdf")) is True
    assert out.exists() and out.stat().st_size > 0

    # 中途取消：返回 False（调用方据此提示"打印已取消"而不是"已发送到打印机"）
    calls = {"n": 0}
    original = QProgressDialog.wasCanceled

    def fake_was_canceled(self):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return calls["n"] > 1  # 画完第 1 页后用户点了取消

    monkeypatch.setattr(QProgressDialog, "wasCanceled", fake_was_canceled)
    try:
        assert window._print_document(make_printer("cancelled.pdf")) is False
    finally:
        monkeypatch.setattr(QProgressDialog, "wasCanceled", original)
    window._controller.close_document()


def test_merge_keeps_undo_action_in_sync(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """原 R2-P1-4：合并后撤销历史失效，按钮必须跟着变灰（而不是点不动）。"""
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))
    window._update_undo_actions()
    assert window.undo_action.isEnabled() is True  # 有批注改动可撤销

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **k: (str(SAMPLES / "with_annotations.pdf"), ""),
    )
    window._on_merge()

    assert window._controller.page_count == 4
    assert window._controller.can_undo() is False  # 合并重建底层文档，撤销历史失效
    assert window.undo_action.isEnabled() == window._controller.can_undo()
    window._controller.close_document()


def test_ocr_second_start_ignored_while_running(qtbot) -> None:  # type: ignore[no-untyped-def]
    """OCR 运行中再次点击不能开第二个线程（旧 worker 引用会被覆盖，之后 wait 不到）。"""
    from pdfreader.workers import OcrWorker

    class _RunningWorker:
        def isRunning(self) -> bool:
            return True

    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    fake = _RunningWorker()
    window._ocr_worker = fake  # type: ignore[assignment]
    opened: list = []
    original_init = OcrWorker.__init__

    def spy_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(1)
        original_init(self, *args, **kwargs)

    OcrWorker.__init__ = spy_init  # type: ignore[method-assign]
    try:
        window._start_ocr([0])
    finally:
        OcrWorker.__init__ = original_init  # type: ignore[method-assign]

    assert opened == [], "运行中不应创建新的 OCR 线程"
    assert window._ocr_worker is fake
    assert window._ocr_progress is None
    window._ocr_worker = None  # 假 worker 不是 OcrWorker，关闭时别交给回收逻辑
    window._controller.close_document()


def test_ocr_results_cleared_between_runs(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """每轮 OCR 独立：上一轮的各页文本不能混进这一轮的结果。"""
    from pdfreader.workers import OcrWorker

    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._ocr_results[0] = "上一轮的文本"
    monkeypatch.setattr(OcrWorker, "start", lambda self: None)  # 不真的跑线程

    window._start_ocr([0])

    assert window._ocr_results == {}
    window._controller.close_document()


def test_ocr_cancel_message_differs_from_done(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """取消不能弹「OCR 完成」：两者文案必须不同。"""
    titles: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, text, *a, **k: titles.append(title),
    )
    window = MainWindow()
    qtbot.addWidget(window)

    window._on_ocr_all_done(3)
    window._on_ocr_cancelled(2)

    assert titles == ["OCR 完成", "OCR 已取消"]


def test_retired_ocr_worker_clears_reference(qtbot) -> None:  # type: ignore[no-untyped-def]
    """回收 OCR 线程：取消并清引用，未在运行的线程直接交给 Qt 删除。"""
    from pdfreader.workers import OcrWorker

    window = MainWindow()
    qtbot.addWidget(window)
    worker = OcrWorker(window)
    worker.setup(SAMPLES / "simple_text.pdf")
    window._ocr_worker = worker

    window._retire_ocr_worker(worker)

    assert worker._running is False
    assert window._retired_ocr_workers == []  # 已停止的线程不进回收列表


def test_retired_search_worker_signals_disconnected(qtbot) -> None:  # type: ignore[no-untyped-def]
    """被回收的搜索线程即使再发结果也不会进界面（回收时断开信号 + 取消）。"""
    from pdfreader.engines.types import SearchResult
    from pdfreader.workers import SearchWorker

    window = MainWindow()
    qtbot.addWidget(window)
    worker = SearchWorker(window)
    received: list = []
    worker.searchCompleted.connect(lambda q, r: received.append(q))

    window._retire_search_worker(worker)
    assert worker._cancelled is True

    worker.searchCompleted.emit("late", SearchResult(query="late", hits=()))
    assert received == []


def test_cleanup_snapshots_removes_files(qtbot) -> None:  # type: ignore[no-untyped-def]
    """退出时清理快照，避免 %TEMP% 残留。"""
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.open_file(SAMPLES / "simple_text.pdf")
    window._controller.add_highlight(0, Rect(40, 40, 200, 60))
    first = window._worker_source()
    assert first is not None
    window._cleanup_snapshots()
    assert not first.exists()
    assert not window._snapshot_dir.exists()
    window._controller.close_document()
