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
    dialog.case.setChecked(False)
    dialog.whole.setChecked(False)
    dialog.search()
    assert dialog.results.count() == 4
    dialog.results.setCurrentRow(2)
    dialog.locate()
    assert opened == [(str(path.resolve()), 2)]
    dialog.close()
    controller.close_document()
