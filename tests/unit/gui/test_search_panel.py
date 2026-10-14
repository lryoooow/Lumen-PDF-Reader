"""搜索面板单元测试。"""

from __future__ import annotations

from pdfreader.engines.types import Rect, SearchHit
from pdfreader.gui.panels import SearchPanel


def test_panel_instantiates(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    assert panel is not None


def test_show_results(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    hits = (
        SearchHit(page_num=0, rect=Rect(0, 0, 10, 10), text="test"),
        SearchHit(page_num=2, rect=Rect(0, 0, 10, 10), text="test"),
    )
    panel.show_results("test", hits)
    assert panel._result_list.count() == 2


def test_result_clicked_signal(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    received: list[tuple] = []
    panel.resultClicked.connect(lambda page, rect: received.append((page, rect)))
    hits = (SearchHit(page_num=5, rect=Rect(0, 0, 1, 1)),)
    panel.show_results("x", hits)
    # 点击第一项
    panel._result_list.itemClicked.emit(panel._result_list.item(0))
    assert received == [(5, Rect(0, 0, 1, 1))]  # 同时带上命中矩形（供视图高亮）


def test_search_requested_signal(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    received: list[str] = []
    panel.searchRequested.connect(received.append)
    panel._input.setText("hello")
    panel._on_search()
    assert received == ["hello"]


def test_empty_query_no_search(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    received: list[str] = []
    panel.searchRequested.connect(received.append)
    panel._input.setText("   ")
    panel._on_search()
    assert received == []


def test_show_error(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    panel.show_error("出错了")
    assert "出错了" in panel._status_label.text()


def test_clear(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    panel.show_results("x", (SearchHit(page_num=0, rect=Rect(0, 0, 1, 1)),))
    panel.clear()
    assert panel._result_list.count() == 0


def test_focus_input(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = SearchPanel()
    qtbot.addWidget(panel)
    panel.focus_input()
    assert panel.isVisible()
