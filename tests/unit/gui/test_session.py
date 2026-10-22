"""会话恢复：退出时的标签、活动文档与第二个实例转交的打开请求。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from pdfreader.gui.main_window import MainWindow


def _make_pdf(path: Path, label: str = "") -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), f"{label or path.stem} hello world")
    doc.save(str(path))
    doc.close()
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


def _doc_tabs(window: MainWindow):  # type: ignore[no-untyped-def]
    return [tab for tab in window.tabs if tab.has_document]


def test_open_documents_are_remembered(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    b = _make_pdf(tmp_path / "b.pdf", "B")
    assert window.open_path(a)
    assert window.open_path(b)
    assert window._settings.session_files == [str(a), str(b)]
    assert window._settings.session_active == str(b)


def test_restore_opens_saved_tabs(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    b = _make_pdf(tmp_path / "b.pdf", "B")
    window._settings.session_files = [str(a), str(b)]
    window._settings.session_active = str(b)
    window._restore_session()
    tabs = _doc_tabs(window)
    assert [tab.path for tab in tabs] == [a, b]
    assert window.current_tab.path == b


def test_restore_skips_missing_files(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    missing = tmp_path / "gone.pdf"
    window._settings.session_files = [str(missing), str(a)]
    window._restore_session()
    assert [tab.path for tab in _doc_tabs(window)] == [a]


def test_restore_does_nothing_when_a_document_is_already_open(
    window: MainWindow, tmp_path: Path
) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    b = _make_pdf(tmp_path / "b.pdf", "B")
    assert window.open_path(a)
    window._settings.session_files = [str(b)]
    window._restore_session()
    assert [tab.path for tab in _doc_tabs(window)] == [a]


def test_closing_a_tab_updates_the_saved_session(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    b = _make_pdf(tmp_path / "b.pdf", "B")
    assert window.open_path(a)
    assert window.open_path(b)
    tab_b = window.current_tab
    assert window._close_tab(tab_b)
    assert window._settings.session_files == [str(a)]
    assert window._settings.session_active == str(a)


def test_switching_tabs_updates_the_saved_active_document(
    window: MainWindow, tmp_path: Path
) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    b = _make_pdf(tmp_path / "b.pdf", "B")
    assert window.open_path(a)
    assert window.open_path(b)
    window._activate(_doc_tabs(window)[0])
    assert window._settings.session_active == str(a)


def test_reordering_tabs_updates_the_saved_order(window: MainWindow, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", "A")
    b = _make_pdf(tmp_path / "b.pdf", "B")
    assert window.open_path(a)
    assert window.open_path(b)
    window.tab_bar.moveTab(0, 1)
    assert window._settings.session_files == [str(b), str(a)]


def test_save_as_updates_the_saved_session(window: MainWindow, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    original = _make_pdf(tmp_path / "original.pdf")
    target = tmp_path / "saved.pdf"
    assert window.open_path(original)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert window._on_save_as()
    assert window._settings.session_files == [str(target)]
    assert window._settings.session_active == str(target)


def test_second_instance_opens_relative_path(window: MainWindow, tmp_path: Path) -> None:
    _make_pdf(tmp_path / "rel.pdf", "Rel")
    window.handle_second_instance(["rel.pdf"], str(tmp_path))
    assert [tab.path for tab in _doc_tabs(window)] == [tmp_path / "rel.pdf"]


def test_second_instance_raises_window(window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    raised: list[bool] = []
    monkeypatch.setattr(window, "raise_", lambda: raised.append(True))
    window.handle_second_instance([], str(Path.cwd()))
    assert raised == [True]
