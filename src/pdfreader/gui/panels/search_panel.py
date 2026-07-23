"""搜索面板。

提供搜索输入框与结果列表。点击结果跳转到对应页并高亮。
搜索在后台线程执行，不阻塞 UI。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdfreader.engines.types import SearchHit

__all__ = ["SearchPanel"]


class SearchPanel(QDockWidget):
    """搜索面板：输入框 + 结果列表。

    Signals:
        resultClicked(int): 点击某结果，发送页码（请求跳转）
        searchRequested(str): 请求搜索某关键词
    """

    resultClicked = Signal(int)  # page_num
    searchRequested = Signal(str)  # query

    def __init__(self, title: str = "搜索", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(title, parent)
        self._setup_ui()
        self._hits: list[SearchHit] = []

    def _setup_ui(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("输入搜索内容...")
        self._input.returnPressed.connect(self._on_search)
        layout.addWidget(self._input)

        self._status_label = QLabel("就绪")
        layout.addWidget(self._status_label)

        self._result_list = QListWidget()
        self._result_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._result_list, stretch=1)

        self.setWidget(container)
        self.setVisible(False)

    def _on_search(self) -> None:
        query = self._input.text().strip()
        if query:
            self._status_label.setText("搜索中...")
            self._result_list.clear()
            self._hits.clear()
            self.searchRequested.emit(query)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        row = self._result_list.row(item)
        if 0 <= row < len(self._hits):
            self.resultClicked.emit(self._hits[row].page_num)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def show_results(self, query: str, hits: tuple[SearchHit, ...]) -> None:
        """显示搜索结果。"""
        self._hits = list(hits)
        self._result_list.clear()
        for hit in hits:
            text = f"第 {hit.page_num + 1} 页"
            item = QListWidgetItem(text)
            self._result_list.addItem(item)
        self._status_label.setText(f"找到 {len(hits)} 处结果")

    def show_error(self, message: str) -> None:
        self._status_label.setText(f"错误：{message}")

    def focus_input(self) -> None:
        """聚焦搜索框并选中文本。"""
        self.setVisible(True)
        self.raise_()
        self._input.setFocus()
        self._input.selectAll()

    def clear(self) -> None:
        self._input.clear()
        self._result_list.clear()
        self._hits.clear()
        self._status_label.setText("就绪")
