"""多标签页：每个标签的文档 / 视图 / 历史 / OCR / AI 互不串扰，切换与关闭在各种时序下都稳定。"""

from __future__ import annotations

import random
import threading
from pathlib import Path

import httpx
import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from pdfreader.engines.types import Point, Rect
from pdfreader.gui import main_window as main_window_module
from pdfreader.gui.document_tab import DocumentTab
from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.widgets import motion
from pdfreader.services.ai.client import ChatClient
from pdfreader.services.ocr import OcrResult
from pdfreader.workers import OcrWorker
from tests.unit.gui.test_ai_assistant import FakeServer, _configured, _sse


def _make_pdf(path: Path, pages: int, label: str = "") -> Path:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"{label or path.stem} page {i + 1} hello world")
    d.set_toc([[1, "Intro", 1]])
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
    for tab in w.tabs:
        if tab.controller.document is not None:
            tab.controller.document.mark_saved()
    w.close()


@pytest.fixture
def docs(tmp_path: Path) -> list[Path]:
    return [_make_pdf(tmp_path / f"doc{i}.pdf", pages) for i, pages in enumerate((3, 5, 7, 2))]


def _doc_tabs(window: MainWindow) -> list[DocumentTab]:
    return [t for t in window.tabs if t.has_document]


def _check_invariants(window: MainWindow) -> None:
    """共用界面必须始终反映当前标签。"""
    tab = window.current_tab
    shown = _doc_tabs(window)
    assert window._controller is tab.controller
    assert window.pdf_view is tab.view
    assert window.ai_panel is tab.panel
    assert window._ai_dock.currentWidget() is tab.panel
    assert window.tab_bar.count() == len(shown)
    assert window.tab_bar.isVisibleTo(window) == bool(shown)
    if not tab.has_document:
        assert shown == [] and len(window.tabs) == 1
        assert window._stack.currentWidget() is window.welcome
        return
    assert window._stack.currentWidget() is tab.view
    assert window.tab_bar.currentIndex() == shown.index(tab)
    for i, t in enumerate(shown):
        assert window.tab_bar.tabText(i) == t.label()
    count = tab.controller.page_count
    assert tab.view.page_count == count
    assert window.status_bar._total == count  # type: ignore[attr-defined]
    assert window.sidebar.thumb_list.count() == count
    # 后台标签不持有渲染位图
    for other in shown:
        if other is not tab:
            assert len(other.view._rendered_cache) == 0


# ---------------------------------------------------------------- 打开与切换
def test_open_second_file_in_new_tab(window: MainWindow, docs: list[Path]) -> None:
    assert not window.tab_bar.isVisibleTo(window)
    assert window.open_path(docs[0])
    first = window.current_tab
    assert window.tab_bar.isVisibleTo(window) and window.tab_bar.count() == 1
    assert window.open_path(docs[1])
    second = window.current_tab
    assert second is not first
    assert [t.path for t in window.tabs] == [docs[0], docs[1]]
    assert first.controller.page_count == 3 and second.controller.page_count == 5
    _check_invariants(window)

    window.tab_bar.setCurrentIndex(0)
    assert window.current_tab is first
    _check_invariants(window)


def test_reopening_same_file_switches_to_its_tab(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[0])
    window.open_path(docs[1])
    assert window.open_path(docs[0].parent / "." / docs[0].name)
    assert len(window.tabs) == 2 and window.current_tab.path == docs[0]
    _check_invariants(window)


def test_new_tab_opens_next_to_current(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[0])
    window.open_path(docs[1])
    window.tab_bar.setCurrentIndex(0)
    window.open_path(docs[2])
    assert [t.path for t in window.tabs] == [docs[0], docs[2], docs[1]]
    _check_invariants(window)


def test_failed_open_keeps_current_tab(
    window: MainWindow, docs: list[Path], tmp_path: Path
) -> None:
    window.open_path(docs[0])
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert not window.open_path(bad)
    assert len(window.tabs) == 1 and window.current_tab.path == docs[0]
    _check_invariants(window)


def test_tab_limit(window: MainWindow, docs: list[Path], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(main_window_module, "MAX_TABS", 2)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, title, *_a, **_k: told.append(title))
    window.open_path(docs[0])
    window.open_path(docs[1])
    assert not window.open_path(docs[2])
    assert told == ["标签太多"] and len(window.tabs) == 2


def test_open_dialog_accepts_several_files(
    window: MainWindow, docs: list[Path], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(docs[0]), str(docs[1])], "")
    )
    window.open_action.trigger()
    assert [t.path for t in window.tabs] == [docs[0], docs[1]]


def test_ctrl_tab_cycles(window: MainWindow, docs: list[Path]) -> None:
    for p in docs[:3]:
        window.open_path(p)
    window.next_tab_action.trigger()
    assert window.current_tab.path == docs[0]
    window.prev_tab_action.trigger()
    assert window.current_tab.path == docs[2]
    window.prev_tab_action.trigger()
    assert window.current_tab.path == docs[1]
    _check_invariants(window)


def test_drag_reorder_keeps_mapping(window: MainWindow, docs: list[Path]) -> None:
    for p in docs[:3]:
        window.open_path(p)
    window.tab_bar.moveTab(0, 2)
    assert [t.path for t in window.tabs] == [docs[1], docs[2], docs[0]]
    window.tab_bar.setCurrentIndex(0)
    assert window.current_tab.path == docs[1]
    _check_invariants(window)


# ---------------------------------------------------------------- 状态隔离
def test_edits_and_undo_stay_in_their_tab(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[0])
    a = window.current_tab
    window.open_path(docs[1])
    b = window.current_tab
    window._on_rotate_page()
    assert b.controller.is_modified and not a.controller.is_modified
    assert window.undo_action.isEnabled()
    assert window.tab_bar.tabText(1).endswith("•") and not window.tab_bar.tabText(0).endswith("•")

    window._activate(a)
    assert not window.undo_action.isEnabled()  # 撤销按钮反映当前标签
    window._activate(b)
    assert window.undo_action.isEnabled()
    window.undo_action.trigger()
    assert not b.controller.is_modified
    _check_invariants(window)


def test_background_view_signals_are_ignored(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[0])
    a = window.current_tab
    window.open_path(docs[1])
    b = window.current_tab
    # 后台标签 A 的视图发出的「添加高亮 / 添加便签 / 渲染」统统不该落到任何文档上
    a.view.annotationHighlight.emit(0, (Rect(72, 60, 200, 80),))
    a.view.annotationNote.emit(0, Point(80, 80))
    a.view.renderRequested.emit(0, a.view.zoom)
    assert a.controller.list_all_annotations() == []
    assert b.controller.list_all_annotations() == []
    assert len(a.view._rendered_cache) == 0
    # 当前标签的同一信号照常生效
    b.view.annotationHighlight.emit(0, (Rect(72, 60, 200, 80),))
    assert len(b.controller.list_all_annotations()) == 1
    assert a.controller.list_all_annotations() == []


def test_history_is_per_tab(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[1])
    a = window.current_tab
    window._navigate_to(3, None)
    assert window.back_action.isEnabled()
    window.open_path(docs[2])
    assert not window.back_action.isEnabled()  # 新标签没有历史
    window._activate(a)
    assert window.back_action.isEnabled() and a.back_stack


def test_switch_cancels_search_and_resets_tool(window: MainWindow, docs: list[Path], qtbot) -> None:  # type: ignore[no-untyped-def]
    window.open_path(docs[0])
    a = window.current_tab
    window.open_path(docs[1])
    window._on_find()
    window._on_search_requested("hello")
    window.highlight_action.trigger()
    window._activate(a)
    assert window._search_worker is None and not window.search_panel.isVisible()
    assert window.select_action.isChecked()
    _check_invariants(window)


def test_render_cache_released_in_background_and_restored(
    window: MainWindow, docs: list[Path], qtbot
) -> None:  # type: ignore[no-untyped-def]
    window.open_path(docs[0])
    a = window.current_tab
    qtbot.waitUntil(lambda: len(a.view._rendered_cache) > 0, timeout=3000)
    window.open_path(docs[1])
    assert len(a.view._rendered_cache) == 0
    window._activate(a)
    qtbot.waitUntil(lambda: len(a.view._rendered_cache) > 0, timeout=3000)
    _check_invariants(window)


def test_pulse_then_switch_immediately(window: MainWindow, docs: list[Path], qtbot) -> None:  # type: ignore[no-untyped-def]
    """在 A 上触发跳页动画后立刻切到 B：动画只作用在 A 自己的场景里，不影响 B。"""
    motion.set_enabled(True)
    window.open_path(docs[2])
    a = window.current_tab
    window.open_path(docs[1])
    b = window.current_tab
    before = len(b.view.scene().items())
    window._activate(a)
    window._navigate_to(5, None)
    window._activate(b)
    qtbot.wait(900)
    assert len(b.view.scene().items()) == before
    assert b.view.current_page_num == 0
    window._activate(a)
    assert a.view.current_page_num == 5
    _check_invariants(window)


# ---------------------------------------------------------------- 关闭
def test_close_current_activates_neighbor_then_welcome(
    window: MainWindow, docs: list[Path]
) -> None:
    for p in docs[:3]:
        window.open_path(p)
    window.tab_bar.setCurrentIndex(1)
    window.close_doc_action.trigger()
    assert [t.path for t in window.tabs] == [docs[0], docs[2]]
    assert window.current_tab.path == docs[2]
    _check_invariants(window)
    window.close_doc_action.trigger()
    window.close_doc_action.trigger()
    assert len(window.tabs) == 1 and not window.current_tab.has_document
    _check_invariants(window)
    assert not window.save_action.isEnabled()


def test_close_background_tab_keeps_current(window: MainWindow, docs: list[Path]) -> None:
    for p in docs[:3]:
        window.open_path(p)
    current = window.current_tab
    window.tab_bar.tabCloseRequested.emit(0)
    assert window.current_tab is current
    assert [t.path for t in window.tabs] == [docs[1], docs[2]]
    _check_invariants(window)


def test_close_dirty_tab_asks_and_cancel_keeps_it(
    window: MainWindow, docs: list[Path], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    window.open_path(docs[0])
    a = window.current_tab
    window._on_rotate_page()
    window.open_path(docs[1])
    monkeypatch.setattr(QMessageBox, "question", lambda *a_, **k: QMessageBox.StandardButton.Cancel)
    window.tab_bar.tabCloseRequested.emit(0)
    assert a in window.tabs and window.current_tab is a  # 切过去问，取消后仍在
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a_, **k: QMessageBox.StandardButton.Discard
    )
    window.tab_bar.tabCloseRequested.emit(0)
    assert a not in window.tabs and window.current_tab.path == docs[1]
    _check_invariants(window)


def test_close_others(window: MainWindow, docs: list[Path]) -> None:
    for p in docs[:3]:
        window.open_path(p)
    window.tab_bar.closeOthersRequested.emit(1)
    assert [t.path for t in window.tabs] == [docs[1]]
    _check_invariants(window)


def test_quit_with_two_dirty_tabs(window: MainWindow, docs: list[Path], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window.open_path(docs[0])
    window._on_rotate_page()
    window.open_path(docs[1])
    window._on_rotate_page()
    asked: list[str] = []

    def answer(*_a, **_k):  # type: ignore[no-untyped-def]
        asked.append(window.current_tab.path.name)  # type: ignore[union-attr]
        # 第一份放弃，第二份取消 → 不退出
        return (
            QMessageBox.StandardButton.Discard
            if len(asked) == 1
            else QMessageBox.StandardButton.Cancel
        )

    monkeypatch.setattr(QMessageBox, "question", answer)
    assert not window.close()
    assert asked == ["doc0.pdf", "doc1.pdf"]
    assert len(_doc_tabs(window)) == 2 and window.isVisible()


def test_quit_remembers_every_tab_position(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[1])
    window.pdf_view.jump_to_page(3)
    window.open_path(docs[2])
    window.pdf_view.jump_to_page(5)
    assert window.close()
    assert window._settings.position_for(docs[1]).page == 3  # type: ignore[union-attr]
    assert window._settings.position_for(docs[2]).page == 5  # type: ignore[union-attr]


# ---------------------------------------------------------------- 后台任务归属
def test_ocr_results_go_to_the_tab_that_started_it(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[0])
    a = window.current_tab
    window.open_path(docs[1])
    b = window.current_tab
    worker = OcrWorker(window)
    a.ocr_worker = worker
    worker.pageOcrCompleted.connect(window._on_ocr_page_done)
    worker.pageOcrCompleted.emit(OcrResult(page_num=1, text="扫描页文字", blocks=()))
    assert a.ocr_results == {1: "扫描页文字"} and b.ocr_results == {}
    assert a.ai._ocr == {1: "扫描页文字"} and b.ai._ocr == {}
    # 发起 OCR 的标签关掉：线程被回收，结果不会再送到任何地方
    window.tab_bar.tabCloseRequested.emit(0)
    assert a.ocr_worker is None
    assert window.current_tab is b and b.ocr_results == {}
    worker.deleteLater()


def test_only_one_ocr_at_a_time_across_tabs(window: MainWindow, docs: list[Path]) -> None:
    window.open_path(docs[0])
    a = window.current_tab

    class Busy:
        def isRunning(self) -> bool:
            return True

    a.ocr_worker = Busy()  # type: ignore[assignment]
    window.open_path(docs[1])
    window._on_ocr_current_page()
    assert window._ocr_worker is None  # B 没有启动新线程
    a.ocr_worker = None


def test_ai_conversations_are_per_tab_and_settings_shared(
    window: MainWindow, docs: list[Path], qtbot
) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("A 的回答")
    window._ai._client_factory = server.factory
    window.open_path(docs[0])
    a = window.current_tab
    a.ai.apply_settings(_configured())
    a.ai.confirm = lambda *x, **k: (True, False)  # type: ignore[method-assign]
    window.open_path(docs[1])
    b = window.current_tab
    assert b.ai is not a.ai and b.panel is not a.panel
    assert b.ai.settings is a.ai.settings and b.ai.client_factory is a.ai.client_factory

    window._activate(a)
    a.ai.ask("问题")
    qtbot.waitUntil(lambda: not a.ai.busy, timeout=5000)
    assert [t.answer for t in a.panel.turns] == ["A 的回答"]
    assert b.panel.turns == []
    window._activate(b)
    assert window.ai_panel is b.panel and window.ai_panel.turns == []


def test_close_tab_while_ai_request_in_flight(window: MainWindow, docs: list[Path], qtbot) -> None:  # type: ignore[no-untyped-def]
    release = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse("晚到的回答")
        )

    window._ai._client_factory = lambda base, key: ChatClient(
        base, key, transport=httpx.MockTransport(slow)
    )
    window.open_path(docs[0])
    a = window.current_tab
    a.ai.apply_settings(_configured())
    a.ai.confirm = lambda *x, **k: (True, False)  # type: ignore[method-assign]
    window.open_path(docs[1])
    b = window.current_tab
    window._activate(a)
    a.ai.ask("问题")
    assert a.ai.busy
    window.close_doc_action.trigger()  # 请求还在路上就关掉 A
    assert window.current_tab is b and a not in window.tabs
    assert a in window._parked_tabs  # 线程没停前不销毁
    release.set()
    qtbot.waitUntil(lambda: not any(w.isRunning() for w in a.ai.workers()), timeout=5000)
    qtbot.wait(50)
    window._release_parked_tabs()
    assert window._parked_tabs == []
    assert b.panel.turns == []  # 晚到的回答不会出现在 B 里
    _check_invariants(window)


# ---------------------------------------------------------------- 随机压测
@pytest.mark.parametrize("seed", [20261015, 7, 424])
def test_random_switch_close_reopen(
    window: MainWindow, docs: list[Path], monkeypatch, seed: int
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    rng = random.Random(seed)
    pages = dict(zip(docs, (3, 5, 7, 2), strict=True))
    for _ in range(80):
        action = rng.choice(("open", "open", "switch", "switch", "close", "edit", "nav"))
        open_tabs = _doc_tabs(window)
        if action == "open":
            window.open_path(rng.choice(docs))
        elif action == "switch" and open_tabs:
            window.tab_bar.setCurrentIndex(rng.randrange(len(open_tabs)))
        elif action == "close" and open_tabs:
            window.tab_bar.tabCloseRequested.emit(rng.randrange(len(open_tabs)))
        elif action == "edit" and window._controller.has_document:
            window._on_rotate_page()
        elif action == "nav" and window._controller.has_document:
            window._navigate_to(rng.randrange(window._controller.page_count), None)
        _check_invariants(window)
        # 每个标签的页数始终是它自己文件的页数
        for tab in _doc_tabs(window):
            assert tab.path is not None
            assert tab.controller.page_count == pages[tab.path]
        assert len({id(t.view) for t in window.tabs}) == len(window.tabs)
