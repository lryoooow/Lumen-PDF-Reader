from pathlib import Path

import pymupdf
import pytest

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.types import Rect
from pdfreader.services.tables import table_csv, table_markdown


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_tables_use_current_snapshot_visible_coordinates_and_keep_journal(
    tmp_path: Path, rotation: int
) -> None:
    path = tmp_path / "table.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=500, height=700)
        for x in (50, 200, 350):
            page.draw_line((x, 50), (x, 150))
        for y in (50, 100, 150):
            page.draw_line((50, y), (350, y))
        for x, y, value in ((60, 80, "Name"), (210, 80, "Value"), (60, 130, "A"), (210, 130, "42")):
            page.insert_text((x, y), value)
        page.set_rotation(rotation)
        pdf.save(path)
    engine = PyMuPdfEngine()
    document = engine.open(path)
    try:
        before = document.native.journal_position()  # type: ignore[attr-defined]
        tables = engine.extract_tables(document, 0)
        assert len(tables) == 1
        assert tables[0].rows == (("Name", "Value"), ("A", "42"))
        bounds = engine.page_bounds(document, 0)
        assert 0 <= tables[0].rect.x0 < tables[0].rect.x1 <= bounds.width
        assert 0 <= tables[0].rect.y0 < tables[0].rect.y1 <= bounds.height
        if rotation == 90:
            assert tables[0].rect == Rect(550, 50, 650, 350)
        assert document.native.journal_position() == before  # type: ignore[attr-defined]
        assert "| A | 42 |" in table_markdown(tables[0])
        assert "Name,Value" in table_csv(tables[0])
    finally:
        engine.close(document)
