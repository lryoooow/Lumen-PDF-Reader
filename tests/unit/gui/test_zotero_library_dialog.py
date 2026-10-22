from pathlib import Path

from pdfreader.gui.dialogs.zotero_library_dialog import ZoteroLibraryDialog
from tests.unit.services.test_zotero import _make_library


def test_zotero_browser_load_filters_and_opens_pdf(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    opened: list[tuple[Path, str]] = []
    directory = _make_library(tmp_path / "Zotero")
    dialog = ZoteroLibraryDialog(directory, lambda path, ref: opened.append((path, ref.title)))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
    assert dialog.items.count() == 1
    dialog.filter.setText("missing")
    assert dialog.items.count() == 0
    dialog.filter.clear()
    dialog.collection.setCurrentIndex(1)
    assert dialog.items.count() == 1
    dialog.open_selected()
    assert opened[0][0].name == "paper.pdf" and "Attention" in opened[0][1]
    dialog.close()
