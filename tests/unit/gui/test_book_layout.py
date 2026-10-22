from PySide6.QtCore import QPointF

from pdfreader.core.layout import layout_pages
from pdfreader.gui.widgets.pdf_view import PdfView


def test_book_rows_cover_and_mixed_page_sizes() -> None:
    placements, width, height = layout_pages(
        [(100, 200), (100, 200), (150, 300), (100, 200)], gap=20, double_page=True
    )
    assert placements[0].x == 85 and placements[0].y == 0
    assert placements[1].y == placements[2].y == 220
    assert placements[2].x == 120
    assert placements[3].y == 540
    assert width == 270 and height == 760


def test_book_hit_testing_and_right_page_navigation(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = PdfView()
    qtbot.addWidget(view)
    view.resize(1000, 700)
    view.show()
    view.setup_document(5, [(300, 500)] * 5)
    view.set_page_layout(double_page=True)
    assert view._page_y_positions[1] == view._page_y_positions[2]
    x, y = view._page_origin(2)
    point = QPointF(x + 30, y + 40)
    assert view._page_at_scene(point) == 2
    assert view._scene_page_point(point)[0] == 2
    view.jump_to_page(2)
    assert view.current_page_num == 2
    view.set_page_layout(double_page=False)
    assert view.current_page_num == 2
    assert view._page_y_positions[2] > view._page_y_positions[1]
