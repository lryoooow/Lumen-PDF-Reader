"""侧边栏（目录 / 批注 / 缩略图）测试。"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QPixmap

from pdfreader.engines.types import Annotation, AnnotationType, Rect, TocEntry
from pdfreader.gui.panels import SidePanel


@pytest.fixture
def panel(qtbot) -> SidePanel:  # type: ignore[no-untyped-def]
    p = SidePanel()
    qtbot.addWidget(p)
    p.resize(260, 600)
    p.show()
    qtbot.waitExposed(p)
    return p


def test_toc_builds_hierarchy_and_emits(panel: SidePanel) -> None:
    panel.set_toc(
        [
            TocEntry(1, "1 Introduction", 0),
            TocEntry(2, "1.1 Background", 1, 300.0),
            TocEntry(2, "1.2 Contributions", 2),
            TocEntry(1, "References", 9),
        ]
    )
    tree = panel.toc_tree
    assert tree.topLevelItemCount() == 2
    intro = tree.topLevelItem(0)
    assert intro.childCount() == 2
    got: list[tuple[int, object]] = []
    panel.tocActivated.connect(lambda page, y: got.append((page, y)))
    panel._on_toc_clicked(intro.child(0))
    assert got == [(1, 300.0)]
    assert panel.toc_empty.isHidden()


def test_empty_toc_shows_hint(panel: SidePanel) -> None:
    panel.set_toc(())
    assert not panel.toc_empty.isHidden()
    assert panel.toc_tree.isHidden()


def test_annotations_sorted_and_signals(panel: SidePanel) -> None:
    a = Annotation(1, AnnotationType.HIGHLIGHT, Rect(0, 200, 10, 210), marked_text="后面", index=0)
    b = Annotation(
        1,
        AnnotationType.UNDERLINE,
        Rect(0, 50, 10, 60),
        marked_text="前面",
        content="笔记",
        index=1,
    )
    panel.set_annotations([a, b])
    assert panel.annot_list.count() == 2
    assert "前面" in panel.annot_list.item(0).text()
    assert "笔记" in panel.annot_list.item(0).text()
    assert panel.annot_count.text() == "2"  # 分段控件「批注」上的数量角标
    activated: list[Annotation] = []
    edited: list[Annotation] = []
    panel.annotationActivated.connect(activated.append)
    panel.annotationEditRequested.connect(edited.append)
    panel._on_annot_clicked(panel.annot_list.item(1))
    panel._on_annot_double_clicked(panel.annot_list.item(0))
    assert activated == [a] and edited == [b]


def test_export_button_disabled_without_annotations(panel: SidePanel) -> None:
    panel.set_annotations([])
    assert panel.export_btn.isEnabled() is False


def test_thumbnails_requested_only_for_visible_pages(panel: SidePanel, qtbot) -> None:  # type: ignore[no-untyped-def]
    panel.tabs.setCurrentIndex(SidePanel.TAB_THUMBNAILS)
    requests: list[list[int]] = []
    panel.thumbnailsNeeded.connect(requests.append)
    panel.set_page_count(200)
    qtbot.waitUntil(lambda: bool(requests), timeout=2000)
    first = requests[-1]
    assert first[0] == 0
    assert len(first) < 20, "只应请求可见的那几页"
    panel.set_thumbnail(0, QPixmap(10, 10))
    requests.clear()
    panel._emit_visible_thumbnails()
    assert requests and 0 not in requests[-1]
