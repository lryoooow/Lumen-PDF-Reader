from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from pdfreader.controllers import DocumentController
from pdfreader.engines.types import Point
from pdfreader.gui.dialogs.notebook_dialog import NotebookDialog


@pytest.fixture
def notes(qtbot, tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path)
    controller = DocumentController()
    assert controller.open_file(path)
    assert controller.add_text_annot(0, Point(50, 50), "需要追踪的批注")
    navigated: list[int] = []
    dialog = NotebookDialog(controller, lambda p, y: navigated.append(p))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    yield dialog, controller, navigated
    dialog.editor.document().setModified(False)
    dialog.close()
    controller.close_document()


def test_insert_save_source_and_deleted_source(notes, qtbot, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dialog, controller, _ = notes
    dialog._insert_source(dialog.sources.item(0))
    assert "需要追踪的批注" in dialog.editor.toPlainText()
    assert "lumen-source" not in dialog.editor.toPlainText()
    target = tmp_path / "note.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert dialog.save()
    assert "lumen-source" in target.read_text("utf-8")
    controller.delete_annotation(0, 0)
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    assert "失效或存在歧义 1" in dialog.status.text()
    assert "需要追踪的批注" in dialog.editor.toPlainText()


def test_export_pdf_and_unsaved_close_cancel(notes, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dialog, _, _ = notes
    dialog.editor.insertPlainText("中文笔记与数字 42")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    dialog.show()
    assert not dialog.close()
    target = tmp_path / "note.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    dialog.pdf()
    with pymupdf.open(target) as doc:
        assert "中文笔记" in "".join(p.get_text() for p in doc)


def test_modal_dialog_can_close(notes) -> None:  # type: ignore[no-untyped-def]
    dialog, _, _ = notes
    QTimer.singleShot(10, dialog.close)
    assert dialog.exec() == 0
