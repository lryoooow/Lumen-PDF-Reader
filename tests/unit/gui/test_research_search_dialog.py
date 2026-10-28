from pathlib import Path

import pymupdf
from PySide6.QtWidgets import QMessageBox

from pdfreader.controllers import DocumentController
from pdfreader.gui.dialogs.research_search_dialog import ResearchSearchDialog


def test_incremental_search_index_and_library_navigation(
    qtbot, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        for i in range(4):
            doc.new_page().insert_text((72, 100), f"Attention attention attentions page {i + 1}")
        doc.save(path)
    controller = DocumentController()
    assert controller.open_file(path)
    opened: list[tuple[str, int]] = []
    dialog = ResearchSearchDialog(
        controller,
        lambda p, y: None,
        lambda p, n: opened.append((p, n)),
        database=tmp_path / "index.db",
    )
    qtbot.addWidget(dialog)
    dialog.query.setText("attention")
    dialog.case.setChecked(True)
    dialog.whole.setChecked(True)
    dialog.search()
    dialog._tick()
    assert len(dialog._hits) == 1 and dialog._timer.isActive()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    assert len(dialog._hits) == 4
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog.index_document()
    qtbot.waitUntil(lambda: dialog._worker is None and not dialog._indexing, timeout=5000)
    assert "已更新" in dialog.status.text()
    dialog.scope.setCurrentIndex(1)
    assert dialog.results.count() == 0
    assert not dialog._hits
    assert not dialog.first_page.isEnabled() and not dialog.regex.isEnabled()
    dialog.case.setChecked(False)
    dialog.whole.setChecked(False)
    dialog.search()
    assert dialog.results.count() == 4
    dialog.results.setCurrentRow(2)
    dialog.locate()
    assert opened == [(str(path.resolve()), 2)]
    dialog.close()
    controller.close_document()


def test_search_scope_summary_distinguishes_index_snapshot_from_page_range(
    qtbot, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        for _ in range(3):
            document.new_page()
        document.save(source)
    controller = DocumentController()
    assert controller.open_file(source)
    dialog = ResearchSearchDialog(
        controller, lambda p, y: None, lambda p, n: None, database=tmp_path / "index.db"
    )
    qtbot.addWidget(dialog)
    dialog.first_page.setValue(2)
    dialog.last_page.setValue(3)
    dialog.regex.setChecked(True)
    assert "paper.pdf" in dialog.scope_summary.text()
    assert "2–3" in dialog.scope_summary.text()
    assert "正则" in dialog.scope_summary.text()
    dialog.scope.setCurrentIndex(1)
    assert "索引快照" in dialog.scope_summary.text()
    assert "不使用" in dialog.scope_summary.text()
    dialog.scope.setCurrentIndex(0)
    assert dialog.regex.isEnabled() and dialog.regex.isChecked()
    assert dialog.first_page.value() == 2
    dialog.close()
    controller.close_document()


def test_changed_conditions_or_document_clear_old_search_locations(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        for _ in range(3):
            doc.new_page().insert_text((72, 100), "needle")
        doc.save(path)
    controller = DocumentController()
    assert controller.open_file(path)
    located = []
    dialog = ResearchSearchDialog(controller, lambda p, y: located.append(p), lambda p, n: None)
    qtbot.addWidget(dialog)
    for change in (
        lambda: dialog.first_page.setValue(2),
        lambda: dialog.regex.setChecked(True),
        lambda: controller.delete_page(0),
    ):
        dialog.query.setText("needle")
        dialog.search()
        qtbot.waitUntil(lambda: not dialog._timer.isActive())
        assert dialog.results.count()
        change()
        dialog.results.setCurrentRow(0)
        dialog.locate()
        assert not located
        assert not dialog._hits and dialog.results.count() == 0
    assert dialog.last_page.maximum() == controller.page_count
    dialog.close()
    controller.close_document()


def test_changed_document_during_index_commit_reports_snapshot_stale(
    qtbot, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import threading

    from pdfreader.gui.dialogs import research_search_dialog
    from pdfreader.workers.library_worker import LibraryIndexWorker

    path = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 100), "old snapshot")
        doc.new_page()
        doc.save(path)
    controller = DocumentController()
    assert controller.open_file(path)
    entered, release = threading.Event(), threading.Event()

    class Delayed(LibraryIndexWorker):
        def run(self) -> None:
            entered.set()
            release.wait(5)
            super().run()

    monkeypatch.setattr(research_search_dialog, "LibraryIndexWorker", Delayed)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog = ResearchSearchDialog(
        controller, lambda p, y: None, lambda p, n: None, database=tmp_path / "index.db"
    )
    qtbot.addWidget(dialog)
    try:
        dialog.index_document()
        qtbot.waitUntil(entered.is_set)
        assert controller.delete_page(0)
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
    assert "快照" in dialog.status.text() and "需更新" in dialog.status.text()
    dialog.close()
    controller.close_document()
