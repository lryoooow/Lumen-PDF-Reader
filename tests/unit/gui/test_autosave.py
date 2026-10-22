"""自动保存（崩溃恢复快照）与文献库恢复横幅。

快照本身是整份 PDF，所以测试里直接生成小文档；恢复路径覆盖「原文件未变」与
「原文件在崩溃后已变化」两种情况。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtWidgets import QMessageBox

from pdfreader.engines.types import Rect
from pdfreader.gui.main_window import MainWindow
from pdfreader.services.recovery import RecoveryEntry, RecoveryStore


def _make_pdf(path: Path, text: str = "hello") -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _text_of(path: Path) -> str:
    doc = pymupdf.open(str(path))
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _snapshot(store: RecoveryStore, original: Path, text: str) -> RecoveryEntry:
    def writer(target: Path) -> bool:
        _make_pdf(target, text)
        return True

    return store.write_snapshot(
        original, page_count=1, title=original.name, encrypted=False, writer=writer
    )


def _window(qtbot, monkeypatch, store: RecoveryStore) -> MainWindow:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    window = MainWindow(recovery_store=store)
    qtbot.addWidget(window)
    window.resize(1000, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _close(window: MainWindow) -> None:
    if window.isHidden():
        return
    for tab in window.tabs:
        if tab.controller.document is not None:
            tab.controller.document.mark_saved()
    window.close()


def _entry_for(store: RecoveryStore, path: Path) -> RecoveryEntry:
    entries = store.entries()
    assert len(entries) == 1
    assert entries[0].original == path.resolve()
    return entries[0]


def test_autosave_writes_snapshot_only_when_dirty_then_clears_after_save(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf")
    window = _window(qtbot, monkeypatch, store)
    try:
        assert window.open_path(path)
        window._autosave_tick()
        assert store.entries() == []

        assert window._controller.add_highlight(0, Rect(72, 60, 220, 90))
        assert window._controller.is_modified
        window._autosave_tick()

        entry = _entry_for(store, path)
        assert entry.snapshot.is_file()
        assert "hello" in _text_of(entry.snapshot)

        monkeypatch.setattr(window, "_record_meta", lambda *a, **k: None)
        monkeypatch.setattr(window, "_save_settings", lambda: None)
        assert window._do_save()
        assert not window._controller.is_modified
        assert store.entries() == []
    finally:
        _close(window)


def test_autosave_removes_stale_snapshot_after_changes_are_reverted(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf")
    window = _window(qtbot, monkeypatch, store)
    try:
        assert window.open_path(path)
        assert window._controller.add_highlight(0, Rect(72, 60, 220, 90))
        window._autosave_tick()
        assert store.entries()

        assert window._controller.undo()
        assert not window._controller.is_modified
        window._autosave_tick()

        assert store.entries() == []
    finally:
        _close(window)


def test_recovery_notice_and_restore_replaces_original(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf", "original")
    _snapshot(store, path, "restored")
    window = _window(qtbot, monkeypatch, store)
    try:
        window._check_recovery()
        assert window.library.recovery_notice_visible
        assert "paper.pdf" in window.library._recovery_text.text()

        window._on_recovery_restore()

        assert store.entries() == []
        assert "restored" in _text_of(path)
        backups = list((store.directory / "backups").iterdir())
        assert len(backups) == 1
        assert "original" in _text_of(backups[0])
        assert any(tab.path == path.resolve() for tab in window.tabs if tab.has_document)
        assert not window.library.recovery_notice_visible
    finally:
        _close(window)


def test_restore_after_original_changed_writes_recovery_copy(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf", "original")
    _snapshot(store, path, "restored")
    _make_pdf(path, "changed after crash")
    window = _window(qtbot, monkeypatch, store)
    try:
        window._check_recovery()
        assert window.library.recovery_notice_visible

        window._on_recovery_restore()

        assert store.entries() == []
        assert "changed after crash" in _text_of(path)
        copies = list(tmp_path.glob("paper (恢复 *).pdf"))
        assert len(copies) == 1
        assert "restored" in _text_of(copies[0])
        assert any(tab.path == copies[0] for tab in window.tabs if tab.has_document)
        assert not window.library.recovery_notice_visible
    finally:
        _close(window)


def test_preexisting_snapshot_survives_clean_tick_and_close(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf")
    _snapshot(store, path, "crashed")
    window = _window(qtbot, monkeypatch, store)
    try:
        assert window.open_path(path)
        window._autosave_tick()
        assert len(store.entries()) == 1
        _close(window)
        assert len(store.entries()) == 1
    finally:
        _close(window)


def test_restore_does_not_overwrite_file_open_in_a_tab(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf", "original")
    window = _window(qtbot, monkeypatch, store)
    try:
        assert window.open_path(path)
        _snapshot(store, path, "restored")
        window._check_recovery()

        window._on_recovery_restore()

        assert "original" in _text_of(path)
        copies = list(tmp_path.glob("paper (恢复 *).pdf"))
        assert len(copies) == 1
        assert "restored" in _text_of(copies[0])
        assert store.entries() == []
    finally:
        _close(window)


def test_discard_deletes_snapshots(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf")
    _snapshot(store, path, "restored")
    window = _window(qtbot, monkeypatch, store)
    try:
        window._check_recovery()
        assert window.library.recovery_notice_visible

        window._on_recovery_discard()

        assert store.entries() == []
        assert not window.library.recovery_notice_visible
    finally:
        _close(window)


def test_later_hides_notice_but_keeps_snapshots(
    qtbot,
    monkeypatch,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    path = _make_pdf(tmp_path / "paper.pdf")
    _snapshot(store, path, "restored")
    window = _window(qtbot, monkeypatch, store)
    try:
        window._check_recovery()
        assert window.library.recovery_notice_visible

        window._on_recovery_later()

        assert not window.library.recovery_notice_visible
        assert len(store.entries()) == 1
    finally:
        _close(window)
