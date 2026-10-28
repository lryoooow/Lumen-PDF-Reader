"""搜索浮层（Lumen 设计稿：右上角 340px 圆角卡片，Ctrl+F 唤出）。

┌──────────────────────────────────────────┐
│ 🔍 [在文档中搜索      ]  3/12   ˄  ˅  ✕ │
├──────────────────────────────────────────┤
│ 第 2 页                                   │
│ …the quadratic cost of **attention**…     │  ← 命中词以琥珀色底加粗
├──────────────────────────────────────────┤
│ F3 下一个 · Shift+F3 上一个 · Esc 关闭    │
└──────────────────────────────────────────┘

* 边输边搜（停止输入 300ms 后发起，回车立即发起）；搜索在后台线程执行。
* 出现时自上落下淡入，结果逐条淡入。

公共 API：``searchRequested`` / ``resultClicked`` / ``closed`` 信号，
``show_results`` / ``show_error`` / ``select_relative`` / ``clear`` / ``focus_input``。
"""

from __future__ import annotations

import html

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.engines.types import SearchHit
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["SearchPanel"]

PANEL_WIDTH = 340
# 停止输入多久后自动搜索（毫秒）
TYPE_DEBOUNCE_MS = 300
# 结果列表最多展示的条数（命中上万条时逐条建控件会卡）
MAX_LISTED = 500


class SearchPanel(QWidget):
    """悬浮搜索卡片。

    Signals:
        resultClicked(int, object): 选中某结果 → (页码, 命中矩形)，请求跳转并高亮
        searchRequested(str): 请求搜索某关键词
        closed: 面板被关闭
    """

    resultClicked = Signal(int, object)  # page_num, Rect
    searchRequested = Signal(str)  # query
    queryEdited = Signal()  # 输入一变即失效旧任务，不等防抖定时器
    closed = Signal()

    def __init__(self, title: str = "搜索", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = get_theme()
        self._hits: list[SearchHit] = []
        self._labels: list[tuple[QLabel, int, str]] = []
        self._cursor = -1
        self._query = ""
        self._title = title
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(TYPE_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_search)
        self._setup_ui()
        self._apply_shadow()
        self._theme.changed.connect(self._on_theme_changed)

    def _setup_ui(self) -> None:
        self.setFixedWidth(PANEL_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("SearchHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 10, 10, 10)
        row.setSpacing(6)
        self._search_icon = QLabel()
        row.addWidget(self._search_icon)
        self._input = QLineEdit()
        self._input.setObjectName("SearchInput")
        self._input.setPlaceholderText("在文档中搜索")
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._on_return)
        row.addWidget(self._input, stretch=1)
        self._count = QLabel("0")
        self._count.setObjectName("SearchCount")
        row.addWidget(self._count)
        self._prev_btn = self._icon_button(
            "chevron_up", "上一个 (Shift+F3)", lambda: self.select_relative(-1)
        )
        self._next_btn = self._icon_button(
            "chevron_down", "下一个 (F3)", lambda: self.select_relative(1)
        )
        self._close_btn = self._icon_button("close", "关闭 (Esc)", self._on_close)
        for btn in (self._prev_btn, self._next_btn, self._close_btn):
            row.addWidget(btn)
        layout.addWidget(header)

        self._scope_label = QLabel("范围：当前文档 · 全部页面 · 正文")
        self._scope_label.setWordWrap(True)
        self._scope_label.setContentsMargins(12, 4, 12, 0)
        self._scope_label.setProperty("variant", "muted")
        layout.addWidget(self._scope_label)

        body = QVBoxLayout()
        body.setContentsMargins(6, 6, 6, 6)
        body.setSpacing(4)
        self._status_label = QLabel("输入关键词开始搜索")
        self._status_label.setProperty("variant", "muted")
        self._status_label.setContentsMargins(6, 4, 6, 4)
        self._status_label.setWordWrap(True)
        body.addWidget(self._status_label)
        self._result_list = QListWidget()
        self._result_list.setObjectName("SearchResults")
        self._result_list.setMaximumHeight(300)
        self._result_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._result_list.itemClicked.connect(self._on_item_clicked)
        body.addWidget(self._result_list)
        layout.addLayout(body)

        self._footer = QLabel("F3 下一个 · Shift+F3 上一个 · Esc 关闭")
        self._footer.setObjectName("SearchFooter")
        layout.addWidget(self._footer)

        self._refresh_icons()
        self.setVisible(False)

    def _icon_button(self, name: str, tip: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("FlatButton")
        btn.setToolTip(tip)
        btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("iconName", name)
        btn.clicked.connect(slot)
        return btn

    def _apply_shadow(self) -> None:
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(40)
        effect.setOffset(0, 12)
        effect.setColor(QColor(0, 0, 0, 110 if self._theme.is_dark else 45))
        self.setGraphicsEffect(effect)

    # ------------------------------------------------------------------ 输入
    def _on_text_changed(self, text: str) -> None:
        self._query = ""
        self._set_results("", ())
        self.queryEdited.emit()
        if text.strip():
            self._set_status("等待搜索当前文档正文…")
            self._debounce.start()
        else:
            self._debounce.stop()
            self._set_results("", ())
            self._set_status("输入关键词开始搜索")

    def _on_return(self) -> None:
        self._debounce.stop()
        if self._hits and self._input.text().strip() == self._query:
            self.select_relative(1)  # 同一关键词再按回车 = 下一个
        else:
            self._on_search()

    def _on_search(self) -> None:
        query = self._input.text().strip()
        if not query:
            return
        self._debounce.stop()
        self._query = query
        self._set_status("搜索中…")
        self._result_list.clear()
        self._hits.clear()
        self._count.setText("…")
        self.searchRequested.emit(query)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        row = self._result_list.row(item)
        if 0 <= row < len(self._hits):
            self._select_row(row)

    def _on_close(self) -> None:
        self._debounce.stop()
        self.setVisible(False)
        self.closed.emit()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ 结果
    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))

    def _set_results(self, query: str, hits: tuple[SearchHit, ...]) -> None:
        self._hits = list(hits)
        self._cursor = -1
        self._result_list.clear()
        self._labels = []
        mark = self._theme.palette.mark
        for i, hit in enumerate(hits[:MAX_LISTED]):
            snippet = (hit.text or "").strip().replace("\n", " ")
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setContentsMargins(10, 8, 10, 8)
            label.setProperty("variant", "serif")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._labels.append((label, hit.page_num, _highlight(snippet[:120], query, mark)))
            self._render_label(i, current=False)
            item = QListWidgetItem()
            item.setSizeHint(QSize(PANEL_WIDTH - 24, label.sizeHint().height() + 4))
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._result_list.addItem(item)
            self._result_list.setItemWidget(item, label)
            if i < 12:
                motion.fade_up(label, delay=i * 30, duration=TOKENS.anim_drop)
        self._count.setText(f"0/{len(hits)}" if hits else "0")

    def _render_label(self, row: int, *, current: bool) -> None:
        """结果项：「第 N 页」（当前项用强调色）+ 片段（命中词加粗铺底）。"""
        label, page_num, body = self._labels[row]
        p = self._theme.palette
        color = p.accent_text if current else p.text_muted
        label.setText(
            f'<div style="font-size:11px;color:{color};">第 {page_num + 1} 页</div>'
            f'<div style="font-size:12px;">…{body}…</div>'
        )

    def _select_row(self, row: int) -> None:
        previous = self._result_list.currentRow()
        self._result_list.setCurrentRow(row)
        if 0 <= previous < len(self._labels):
            self._render_label(previous, current=False)
        if row < len(self._labels):
            self._render_label(row, current=True)
        self._cursor = row
        self._count.setText(f"{row + 1}/{len(self._hits)}")
        hit = self._hits[row]
        self.resultClicked.emit(hit.page_num, hit.rect)

    # ------------------------------------------------------------------ 公开 API
    def show_results(self, query: str, hits: tuple[SearchHit, ...]) -> None:
        """显示搜索结果。"""
        self._query = query
        self._set_results(query, hits)
        if not hits:
            self._set_status("未找到匹配内容")
        elif len(hits) > MAX_LISTED:
            self._set_status(f"找到 {len(hits)} 处，列表显示前 {MAX_LISTED} 处（F3 可遍历全部）")
        else:
            self._set_status("")

    def show_error(self, message: str) -> None:
        self._query = ""
        self._set_results("", ())
        self._set_status(f"错误：{message}")
        self._count.setText("0")

    def set_document_label(self, name: str | None) -> None:
        """显示真正的搜索对象；错误和空结果不隐藏搜索范围。"""
        self._scope_label.setText(f"范围：{name} · 全部页面 · 正文" if name else "范围：未打开文档")

    def select_relative(self, step: int) -> bool:
        """选中上一个 / 下一个结果并发出 ``resultClicked``（F3 / Shift+F3）；无结果返回 False。

        到达末尾后循环回到开头（反向同理）。超出列表展示上限的命中同样可以遍历。
        """
        if not self._hits:
            return False
        row = self._cursor
        row = (
            (0 if step > 0 else len(self._hits) - 1) if row < 0 else (row + step) % len(self._hits)
        )
        self._cursor = row
        if row < self._result_list.count():
            self._select_row(row)
        else:  # 超出列表展示上限的命中：不在列表里，但照样定位
            self._cursor = row
            self._count.setText(f"{row + 1}/{len(self._hits)}")
            hit = self._hits[row]
            self.resultClicked.emit(hit.page_num, hit.rect)
        return True

    def focus_input(self) -> None:
        """显示（自上落下淡入）并聚焦搜索框。"""
        was_visible = self.isVisible()
        self.setVisible(True)
        self.raise_()
        # 先让父容器把卡片摆到右上角，再以该位置为终点播放动画（否则会落回旧坐标）
        place = getattr(self.parentWidget(), "reposition_overlay", None)
        if callable(place):
            place()
        self._input.setFocus()
        self._input.selectAll()
        if not was_visible:
            # 只做「自上落下」位移：面板自带投影效果，透明度动画会把它替换掉
            end = self.pos()
            motion.animate_property(
                self, b"pos", end - QPoint(0, 10), end, TOKENS.anim_drop, owner=self
            )

    def clear(self) -> None:
        self._debounce.stop()
        self._input.blockSignals(True)
        self._input.clear()
        self._input.blockSignals(False)
        self._query = ""
        self._cursor = -1
        self._set_results("", ())
        self._set_status("输入关键词开始搜索")

    # ------------------------------------------------------------------
    def _refresh_icons(self) -> None:
        p = self._theme.palette
        self._search_icon.setPixmap(
            self._theme.colored_icon("search", p.text_muted, size=16).pixmap(16, 16)
        )
        for btn in (self._prev_btn, self._next_btn, self._close_btn):
            btn.setIcon(self._theme.icon(str(btn.property("iconName")), size=14))

    def _on_theme_changed(self) -> None:
        self._refresh_icons()
        self._apply_shadow()
        self._set_results(self._query, tuple(self._hits))


def _highlight(text: str, query: str, mark: str) -> str:
    """HTML 转义后把命中词（不区分大小写）加粗并铺琥珀色底。"""
    if not query:
        return html.escape(text)
    lower, q = text.lower(), query.lower()
    out: list[str] = []
    i = 0
    while True:
        j = lower.find(q, i)
        if j < 0:
            out.append(html.escape(text[i:]))
            break
        out.append(html.escape(text[i:j]))
        out.append(
            f'<b style="background-color:{mark};">{html.escape(text[j : j + len(query)])}</b>'
        )
        i = j + len(query)
    return "".join(out)
