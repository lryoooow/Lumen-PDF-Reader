from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from pdfreader.engines import PyMuPdfEngine


def make_epub(path: Path) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB Test Book</dc:title><dc:language>en</dc:language><dc:identifier id="book">test-book</dc:identifier></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head><body><h1>Chapter One</h1><p>EPUB sample text for reading.</p></body></html>',
        )
    return path


def test_epub_read_text_render_and_metadata(tmp_path: Path) -> None:
    engine = PyMuPdfEngine()
    document = engine.open(make_epub(tmp_path / "book.epub"))
    try:
        assert not engine.metadata(document).is_pdf
        assert engine.page_count(document) >= 1
        assert "EPUB sample" in engine.extract_text(document, 0).full_text
        assert engine.get_words(document, 0)
        assert engine.render_page(document, 0).width > 0
    finally:
        engine.close(document)
