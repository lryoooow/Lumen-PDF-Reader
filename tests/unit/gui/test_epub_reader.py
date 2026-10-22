from pathlib import Path

from pdfreader.gui.main_window import MainWindow
from tests.unit.engines.test_epub import make_epub


def test_epub_command_line_path(tmp_path: Path) -> None:
    from pdfreader.main import file_from_argv

    source = make_epub(tmp_path / "book.EPUB")
    assert file_from_argv(["PDFReader.exe", str(source)]) == source
    assert file_from_argv(["PDFReader.exe", str(tmp_path / "missing.epub")]) is None


def test_epub_opens_as_read_only_with_search_and_ai(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(make_epub(tmp_path / "book.epub"))
    assert window._controller.has_document
    assert not window.save_action.isEnabled()
    assert not window.rotate_action.isEnabled()
    assert not window.highlight_action.isEnabled()
    assert not window.delete_page_action.isEnabled()
    assert window.pdf_view._read_only
    emitted: list[object] = []
    window.pdf_view.markupRequested.connect(lambda *args: emitted.append(args))
    window.pdf_view._request_markup("highlight")
    assert emitted == []
    assert window.search_action.isEnabled() and window.parallel_translation_action.isEnabled()
    window._controller.close_document()
    window.close()
