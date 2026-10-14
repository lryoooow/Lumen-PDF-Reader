"""左侧边栏：目录 / 批注 / 缩略图（Lumen 设计稿，宽 280）。

* 顶部**分段切换**：滑块随页签滑动（400ms OutExpo），「批注」带数量角标。
* **目录**：扁平缩进列表（每级 16px），右侧页码；当前页所在的顶级章节以强调色标出。
* **批注**：卡片——左侧色条、「第 N 页 · 类型」、衬线引文、📝笔记；单击跳转、双击编辑、
  右键更多；底部虚线按钮「导出笔记 (.md)」。
* **缩略图**：两列网格，当前页带强调色描边，只为可见条目请求渲染（大文档不卡）。

本面板只负责展示与发信号，不直接访问文档；数据由主窗口经控制器提供。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdfreader.engines.types import Annotation, TocEntry
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.manager import serif_font
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["SidePanel"]

SIDEBAR_WIDTH = 280
_THUMB_SIZE = QSize(92, 120)  # 约 1:1.3（设计稿 aspect-[1/1.3]）
_ROLE = Qt.ItemDataRole.UserRole
_ANNOT_ROLE = Qt.ItemDataRole.UserRole + 1  # 卡片绘制所需的结构化数据


class _Segmented(QWidget):
    """分段切换控件：按钮组 + 一块随选中项滑动的滑块。

    对外提供 ``currentIndex / setCurrentIndex / currentChanged``（与 QTabWidget 同名，
    主窗口与测试按此使用）。
    """

    currentChanged = Signal(int)

    def __init__(self, labels: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = get_theme()
        self._thumb = QWidget(self)
        self._thumb.setObjectName("SegmentedThumb")
        self._thumb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QToolButton] = []
        self._icons = [icon for _label, icon in labels]
        for i, (label, _icon) in enumerate(labels):
            btn = QToolButton()
            btn.setObjectName("SegmentButton")
            btn.setText(label)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setIconSize(QSize(13, 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._group.addButton(btn, i)
            lay.addWidget(btn, stretch=1)
            self._buttons.append(btn)
        self._badge = QLabel("0", self)
        self._badge.setObjectName("CountBadge")
        self._badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._group.idClicked.connect(self.setCurrentIndex)
        self._index = 0
        self._buttons[0].setChecked(True)
        self._refresh_icons()
        self._theme.changed.connect(self._refresh_icons)

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._buttons):
            return
        self._buttons[index].setChecked(True)
        if index == self._index:
            return
        self._index = index
        self._move_thumb(animate=True)
        self.currentChanged.emit(index)

    def set_badge(self, button_index: int, text: str) -> None:
        self._badge.setText(text)
        self._badge.adjustSize()
        self._badge_index = button_index
        self._place_badge()

    def _thumb_rect(self) -> QRect:
        rect: QRect = self._buttons[self._index].geometry()
        return rect

    def _move_thumb(self, *, animate: bool) -> None:
        target = self._thumb_rect()
        self._thumb.lower()
        if animate:
            motion.animate_property(
                self._thumb, b"geometry", self._thumb.geometry(), target, 400, owner=self._thumb
            )
        else:
            self._thumb.setGeometry(target)

    def _place_badge(self) -> None:
        index = getattr(self, "_badge_index", None)
        if index is None:
            return
        btn = self._buttons[index]
        geo = btn.geometry()
        self._badge.move(
            geo.right() - self._badge.width() - 6, geo.center().y() - self._badge.height() // 2
        )
        self._badge.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._move_thumb(animate=False)
        self._place_badge()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._move_thumb(animate=False)
        self._place_badge()

    def _refresh_icons(self) -> None:
        p = self._theme.palette
        for btn, name in zip(self._buttons, self._icons, strict=True):
            btn.setIcon(self._theme.colored_icon(name, p.text_secondary, size=13))


class _AnnotationCardDelegate(QStyledItemDelegate):
    """批注卡片：色条 + 「第 N 页 · 类型」+ 衬线引文（最多两行）+ 笔记。"""

    PAD = 10
    GAP = 6

    def _fonts(self, base: QFont) -> tuple[QFont, QFont, QFont]:
        meta = QFont(base)
        meta.setPixelSize(11)
        quote = serif_font(13)
        note = QFont(base)
        note.setPixelSize(12)
        return meta, quote, note

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        data = index.data(_ANNOT_ROLE) or {}
        width = max(120, option.rect.width() or 240)
        meta, quote, note = self._fonts(option.font)
        text_w = width - 2 * self.PAD - 14
        from PySide6.QtGui import QFontMetrics

        h = self.PAD * 2 + QFontMetrics(meta).height()
        if data.get("quote"):
            fm = QFontMetrics(quote)
            lines = min(2, max(1, fm.horizontalAdvance(data["quote"]) // max(1, text_w) + 1))
            h += 4 + lines * fm.lineSpacing()
        if data.get("note"):
            fm = QFontMetrics(note)
            lines = min(3, max(1, fm.horizontalAdvance(data["note"]) // max(1, text_w - 16) + 1))
            h += self.GAP + lines * fm.lineSpacing()
        return QSize(width, h + 6)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        data = index.data(_ANNOT_ROLE) or {}
        p = get_theme().palette
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = QRectF(option.rect).adjusted(2, 2, -2, -4)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if hovered:  # 悬停：轻微上浮 + 加深阴影（设计稿 hover:-translate-y-px hover:shadow-md）
            card.translate(0, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 40 if p.is_dark else 18))
            painter.drawRoundedRect(card.translated(0, 3), 9, 9)
        painter.setPen(QPen(QColor(p.border), 1))
        painter.setBrush(QColor(p.card))
        painter.drawRoundedRect(card, 8, 8)

        color = QColor(data.get("color") or "#FACC15")
        bar = QRectF(card.left() + self.PAD, card.top() + self.PAD, 4, card.height() - 2 * self.PAD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(bar, 2, 2)

        meta, quote, note = self._fonts(option.font)
        x = int(bar.right() + 10)
        w = int(card.right() - self.PAD - x)
        y = int(card.top() + self.PAD)
        from PySide6.QtGui import QFontMetrics

        painter.setFont(meta)
        painter.setPen(QColor(p.text_muted))
        fm = QFontMetrics(meta)
        painter.drawText(
            QRect(x, y, w, fm.height()), Qt.AlignmentFlag.AlignLeft, data.get("meta", "")
        )
        y += fm.height() + 4
        if data.get("quote"):
            painter.setFont(quote)
            painter.setPen(QColor(p.text))
            fm = QFontMetrics(quote)
            box = QRect(x, y, w, 2 * fm.lineSpacing())
            text = f"“{data['quote']}”"
            elided = _elide_lines(text, fm, w, 2)
            painter.drawText(
                box, int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), elided
            )
            used = fm.boundingRect(box, int(Qt.TextFlag.TextWordWrap), elided).height()
            y += min(used, box.height())
        if data.get("note"):
            y += self.GAP
            painter.setFont(note)
            painter.setPen(QColor(p.text_secondary))
            fm = QFontMetrics(note)
            icon = get_theme().colored_icon("note", p.text_secondary, size=12)
            icon.paint(painter, QRect(x, y + 2, 12, 12))
            box = QRect(x + 16, y, w - 16, 3 * fm.lineSpacing())
            painter.drawText(
                box,
                int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
                _elide_lines(data["note"], fm, w - 16, 3),
            )
        painter.restore()


class _ThumbDelegate(QStyledItemDelegate):
    """缩略图：白色页面卡片 + 阴影，当前页加强调色描边，下方页码。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p = get_theme().palette
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        page_rect = QRectF(
            rect.center().x() - _THUMB_SIZE.width() / 2,
            rect.top() + 6 - (2 if hovered else 0),
            _THUMB_SIZE.width(),
            _THUMB_SIZE.height(),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 34 if hovered else 20))
        painter.drawRoundedRect(page_rect.translated(0, 2), 5, 5)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(page_rect, 5, 5)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            pix = icon.pixmap(_THUMB_SIZE)
            target = page_rect.adjusted(3, 3, -3, -3)
            scaled = pix.size().scaled(target.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            dx = (target.width() - scaled.width()) / 2
            dy = (target.height() - scaled.height()) / 2
            painter.drawPixmap(
                QRectF(
                    target.left() + dx, target.top() + dy, scaled.width(), scaled.height()
                ).toRect(),
                pix,
            )
        if selected:
            pen = QPen(QColor(p.accent), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(page_rect.adjusted(-4, -4, 4, 4), 7, 7)
        painter.setPen(QColor(p.accent_text if selected else p.text_muted))
        font = QFont(option.font)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left(), int(page_rect.bottom()) + 6, rect.width(), 16),
            Qt.AlignmentFlag.AlignHCenter,
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(_THUMB_SIZE.width() + 24, _THUMB_SIZE.height() + 36)


def _elide_lines(text: str, fm, width: int, max_lines: int) -> str:  # type: ignore[no-untyped-def]
    """按宽度折行后最多保留 max_lines 行，超出部分以省略号结束。"""
    lines: list[str] = []
    current = ""
    for ch in text.replace("\n", " "):
        if fm.horizontalAdvance(current + ch) > width and current:
            lines.append(current)
            current = ch
            if len(lines) == max_lines:
                break
        else:
            current += ch
    else:
        if current:
            lines.append(current)
        return "\n".join(lines[:max_lines])
    last = lines[-1]
    while last and fm.horizontalAdvance(last + "…") > width:
        last = last[:-1]
    lines[-1] = last + "…"
    return "\n".join(lines)


class SidePanel(QWidget):
    """左侧边栏。

    Signals:
        tocActivated(int, object): 点击目录项 → (页码, 页内 y 或 None)
        annotationActivated(object): 点击批注 → Annotation
        annotationEditRequested(object): 编辑批注笔记 → Annotation
        annotationDeleteRequested(object): 删除批注 → Annotation
        exportNotesRequested(): 导出笔记为 Markdown
        thumbnailActivated(int): 点击缩略图 → 页码
        thumbnailsNeeded(list): 这些页的缩略图需要渲染（只含当前可见、尚未渲染的）
    """

    tocActivated = Signal(int, object)
    annotationActivated = Signal(object)
    annotationEditRequested = Signal(object)
    annotationDeleteRequested = Signal(object)
    exportNotesRequested = Signal()
    thumbnailActivated = Signal(int)
    thumbnailsNeeded = Signal(list)

    TAB_TOC = 0
    TAB_ANNOTATIONS = 1
    TAB_THUMBNAILS = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = get_theme()
        self._annotations: list[Annotation] = []
        self._thumb_done: set[int] = set()
        self._current_page = 0
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(30)
        self._thumb_timer.timeout.connect(self._emit_visible_thumbnails)
        self._build()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        inner = QWidget()
        inner.setFixedWidth(SIDEBAR_WIDTH)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 外层宽度做 0 ↔ 280 的显隐动画，内层固定 280 被裁剪（设计稿 overflow-hidden）
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        outer.addWidget(inner, alignment=Qt.AlignmentFlag.AlignLeft)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.tabs = _Segmented([("目录", "list"), ("批注", "pen"), ("缩略图", "grid")])
        layout.addWidget(self.tabs)
        self._pages = QStackedWidget()
        layout.addWidget(self._pages, stretch=1)

        # 目录
        toc_page = QWidget()
        toc_layout = QVBoxLayout(toc_page)
        toc_layout.setContentsMargins(0, 0, 0, 0)
        self.toc_tree = QTreeWidget()
        self.toc_tree.setObjectName("TocTree")
        self.toc_tree.setColumnCount(2)
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.setRootIsDecorated(False)
        self.toc_tree.setItemsExpandable(False)
        self.toc_tree.setIndentation(16)
        self.toc_tree.setUniformRowHeights(True)
        self.toc_tree.setMouseTracking(True)
        header = self.toc_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.toc_tree.itemClicked.connect(self._on_toc_clicked)
        self.toc_tree.itemActivated.connect(self._on_toc_clicked)
        self.toc_empty = QLabel("本文档没有目录（书签）")
        self.toc_empty.setProperty("variant", "muted")
        self.toc_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toc_empty.setWordWrap(True)
        toc_layout.addWidget(self.toc_tree)
        toc_layout.addWidget(self.toc_empty)
        self._pages.addWidget(toc_page)

        # 批注
        annot_page = QWidget()
        annot_layout = QVBoxLayout(annot_page)
        annot_layout.setContentsMargins(0, 0, 0, 0)
        annot_layout.setSpacing(8)
        self.annot_list = QListWidget()
        self.annot_list.setObjectName("AnnotList")
        self.annot_list.setItemDelegate(_AnnotationCardDelegate(self.annot_list))
        self.annot_list.setMouseTracking(True)
        self.annot_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.annot_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.annot_list.customContextMenuRequested.connect(self._on_annot_menu)
        self.annot_list.itemClicked.connect(self._on_annot_clicked)
        self.annot_list.itemDoubleClicked.connect(self._on_annot_double_clicked)
        self.annot_list.setToolTip("单击跳转 · 双击编辑笔记 · 右键更多")
        annot_layout.addWidget(self.annot_list, stretch=1)
        self.annot_empty = QLabel("还没有批注。\n用「高亮」或「下划线」标记文字后会出现在这里。")
        self.annot_empty.setProperty("variant", "muted")
        self.annot_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.annot_empty.setWordWrap(True)
        annot_layout.addWidget(self.annot_empty)
        self.export_btn = QPushButton("导出笔记 (.md)")
        self.export_btn.setObjectName("ExportNotesButton")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setToolTip("把全部批注导出为 Markdown 笔记")
        self.export_btn.clicked.connect(self.exportNotesRequested.emit)
        annot_layout.addWidget(self.export_btn)
        # 数量显示在分段控件的角标上（设计稿）；保留文本属性供无障碍 / 测试读取
        self.annot_count = self.tabs._badge
        self._pages.addWidget(annot_page)

        # 缩略图（两列网格）
        self.thumb_list = QListWidget()
        self.thumb_list.setObjectName("ThumbList")
        self.thumb_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumb_list.setFlow(QListWidget.Flow.LeftToRight)
        self.thumb_list.setWrapping(True)
        self.thumb_list.setMovement(QListWidget.Movement.Static)
        self.thumb_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumb_list.setIconSize(_THUMB_SIZE)
        self.thumb_list.setGridSize(QSize(_THUMB_SIZE.width() + 24, _THUMB_SIZE.height() + 36))
        self.thumb_list.setUniformItemSizes(True)
        self.thumb_list.setMouseTracking(True)
        self.thumb_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.thumb_list.setItemDelegate(_ThumbDelegate(self.thumb_list))
        self.thumb_list.itemClicked.connect(
            lambda item: self.thumbnailActivated.emit(int(item.data(_ROLE)))
        )
        self.thumb_list.verticalScrollBar().valueChanged.connect(
            lambda _v: self._thumb_timer.start()
        )
        self._pages.addWidget(self.thumb_list)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.set_annotations(())

    def _on_tab_changed(self, index: int) -> None:
        self._pages.setCurrentIndex(index)
        motion.fade_up(self._pages.currentWidget(), duration=TOKENS.anim_drop)
        self._thumb_timer.start()

    # ------------------------------------------------------------------ 目录
    def set_toc(self, entries: Sequence[TocEntry]) -> None:
        """按层级构建目录（全部展开、扁平缩进，右侧页码）。"""
        self.toc_tree.clear()
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for entry in entries:
            page_text = str(entry.page_num + 1) if entry.page_num >= 0 else ""
            item = QTreeWidgetItem([entry.title or "（无标题）", page_text])
            item.setData(0, _ROLE, (entry.page_num, entry.y))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if entry.page_num >= 0:
                item.setToolTip(0, f"{entry.title}（第 {entry.page_num + 1} 页）")
            while stack and stack[-1][0] >= entry.level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.toc_tree.addTopLevelItem(item)
            stack.append((entry.level, item))
        self.toc_tree.expandAll()
        has_toc = bool(entries)
        self.toc_tree.setVisible(has_toc)
        self.toc_empty.setVisible(not has_toc)
        self._style_toc_items()

    def set_current_page(self, page_num: int) -> None:
        """当前页变化：目录标出所在顶级章节，缩略图同步选中。"""
        self._current_page = page_num
        self._style_toc_items()
        self.select_thumbnail(page_num)

    def _style_toc_items(self) -> None:
        """顶级章节中「起始页 ≤ 当前页」的最后一个以强调色 + 加粗显示（设计稿当前章节）。"""
        p = self._theme.palette
        muted = QColor(p.text_muted)
        normal = QColor(p.text)
        accent = QColor(p.accent_text)
        current_top: QTreeWidgetItem | None = None
        for i in range(self.toc_tree.topLevelItemCount()):
            item = self.toc_tree.topLevelItem(i)
            data = item.data(0, _ROLE) if item is not None else None
            if isinstance(data, tuple) and 0 <= data[0] <= self._current_page:
                current_top = item
        it: list[QTreeWidgetItem] = [
            top
            for top in (
                self.toc_tree.topLevelItem(i) for i in range(self.toc_tree.topLevelItemCount())
            )
            if top is not None
        ]
        while it:
            item = it.pop()
            it.extend(c for c in (item.child(j) for j in range(item.childCount())) if c is not None)
            is_current = item is current_top
            font = QFont(self.toc_tree.font())
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.Medium if is_current else QFont.Weight.Normal)
            item.setFont(0, font)
            item.setForeground(0, accent if is_current else normal)
            page_font = QFont(font)
            page_font.setPixelSize(11)
            page_font.setWeight(QFont.Weight.Normal)
            item.setFont(1, page_font)
            item.setForeground(1, muted)

    def _on_toc_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        data = item.data(0, _ROLE)
        if isinstance(data, tuple) and data[0] >= 0:
            self.tocActivated.emit(int(data[0]), data[1])

    # ------------------------------------------------------------------ 批注
    def set_annotations(self, annotations: Sequence[Annotation]) -> None:
        """刷新批注卡片（按页、自上而下排列）。"""
        self._annotations = sorted(annotations, key=lambda a: (a.page_num, a.rect.y0, a.rect.x0))
        self.annot_list.clear()
        for annot in self._annotations:
            quote = annot.marked_text.replace("\n", " ").strip()
            note = annot.content.strip()
            if not quote and annot.annot_type == 0 and note:  # 便签：正文作为主内容
                quote, note = note, ""
            lines = [f"第 {annot.page_num + 1} 页 · {annot.type_name}"]
            if quote:
                lines.append(quote)
            if note:
                lines.append(f"📝 {note}")
            item = QListWidgetItem("\n".join(lines))
            item.setData(_ROLE, self.annot_list.count())  # 在 _annotations 中的序号
            item.setData(
                _ANNOT_ROLE,
                {
                    "meta": lines[0],
                    "quote": quote,
                    "note": note,
                    "color": _hex(annot.color),
                },
            )
            self.annot_list.addItem(item)
        count = len(self._annotations)
        self.tabs.set_badge(self.TAB_ANNOTATIONS, str(count))
        self.export_btn.setEnabled(count > 0)
        self.annot_empty.setVisible(count == 0)
        self.annot_list.setVisible(count > 0)

    def _annotation_for(self, item: QListWidgetItem | None) -> Annotation | None:
        if item is None:
            return None
        idx = item.data(_ROLE)
        if isinstance(idx, int) and 0 <= idx < len(self._annotations):
            return self._annotations[idx]
        return None

    def _on_annot_clicked(self, item: QListWidgetItem) -> None:
        annot = self._annotation_for(item)
        if annot is not None:
            self.annotationActivated.emit(annot)

    def _on_annot_double_clicked(self, item: QListWidgetItem) -> None:
        annot = self._annotation_for(item)
        if annot is not None:
            self.annotationEditRequested.emit(annot)

    def _on_annot_menu(self, pos: QPoint) -> None:
        annot = self._annotation_for(self.annot_list.itemAt(pos))
        if annot is None:
            return
        menu = QMenu(self)
        menu.addAction("跳转到批注", lambda: self.annotationActivated.emit(annot))
        menu.addAction("编辑笔记…", lambda: self.annotationEditRequested.emit(annot))
        menu.addSeparator()
        menu.addAction("删除批注", lambda: self.annotationDeleteRequested.emit(annot))
        menu.exec(self.annot_list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ 缩略图
    def set_page_count(self, count: int) -> None:
        """重建缩略图占位（换文档 / 增删页后调用），缩略图随后按可见性按需渲染。"""
        self.thumb_list.clear()
        self._thumb_done.clear()
        for page in range(count):
            item = QListWidgetItem(QIcon(), str(page + 1))
            item.setData(_ROLE, page)
            item.setSizeHint(self.thumb_list.gridSize())
            self.thumb_list.addItem(item)
        self._thumb_timer.start()

    def set_thumbnail(self, page_num: int, pixmap: QPixmap) -> None:
        item = self.thumb_list.item(page_num)
        if item is None:
            return
        item.setIcon(QIcon(pixmap))
        self._thumb_done.add(page_num)

    def invalidate_thumbnail(self, page_num: int) -> None:
        """该页内容变了（批注 / 编辑）：下次可见时重新渲染。"""
        self._thumb_done.discard(page_num)
        self._thumb_timer.start()

    def select_thumbnail(self, page_num: int) -> None:
        """同步当前页的选中态（不触发跳转）。"""
        item = self.thumb_list.item(page_num)
        if item is not None and not item.isSelected():
            self.thumb_list.blockSignals(True)
            self.thumb_list.setCurrentItem(item)
            self.thumb_list.blockSignals(False)
            if self.tabs.currentIndex() == self.TAB_THUMBNAILS:
                self.thumb_list.scrollToItem(item)

    def visible_thumbnail_pages(self) -> list[int]:
        """当前在缩略图列表视口内的页码。"""
        viewport = self.thumb_list.viewport().rect()
        pages: list[int] = []
        for row in range(self.thumb_list.count()):
            item = self.thumb_list.item(row)
            if self.thumb_list.visualItemRect(item).intersects(viewport):
                pages.append(row)
            elif pages:
                break  # 按行排列：可见区之后的都不可见
        return pages

    def _emit_visible_thumbnails(self) -> None:
        if not self.isVisible() or self.tabs.currentIndex() != self.TAB_THUMBNAILS:
            return
        needed = [p for p in self.visible_thumbnail_pages() if p not in self._thumb_done]
        if needed:
            self.thumbnailsNeeded.emit(needed)

    # ------------------------------------------------------------------ 通用
    def clear(self) -> None:
        self.set_toc(())
        self.set_annotations(())
        self.thumb_list.clear()
        self._thumb_done.clear()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._thumb_timer.start()


def _hex(color: tuple[float, float, float] | None) -> str:
    if color is None:
        return "#FACC15"
    return str(QColor.fromRgbF(*color).name())
