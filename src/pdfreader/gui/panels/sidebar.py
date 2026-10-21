"""左侧边栏：目录 / 批注 / 缩略图 / 素材（Lumen 设计稿，宽 280）。

* 顶部**分段切换**：滑块随页签滑动（400ms OutExpo），「批注」带数量角标。
* **目录**：上方「我的书签」（Ctrl+D 添加，右键重命名 / 删除）；下方文档目录——扁平缩进列表
  （每级 16px），右侧页码；当前页所在的顶级章节以强调色标出。
* **批注**：顶部搜索框 + 类型 / 颜色筛选；卡片——左侧色条、「第 N 页 · 类型」、衬线引文、
  📝笔记；单击跳转、双击编辑、右键更多；底部虚线按钮「导出笔记 (.md)」。
* **缩略图**：两列网格，当前页带强调色描边，只为可见条目请求渲染（大文档不卡）。
* **素材**：跨文档的写作素材库（单词本 / 语料 / 引文 / 术语）——类型下拉 + 搜索 + 卡片
  （原文、备注、来源「文档 · 第 N 页」）；单击跳回原文、右键复制 / 编辑 / 删除，底部导出。

本面板只负责展示与发信号，不直接访问文档；数据由主窗口经控制器提供。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
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

from pdfreader.config.settings import Bookmark
from pdfreader.engines.types import Annotation, TocEntry
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.manager import serif_font
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion
from pdfreader.services.materials import KIND_LABELS, KINDS, Material

__all__ = ["SIDEBAR_MAX_WIDTH", "SIDEBAR_MIN_WIDTH", "SIDEBAR_WIDTH", "SidePanel"]

SIDEBAR_WIDTH = 280  # 默认宽度；用户可拖动调整
SIDEBAR_MIN_WIDTH = 220
SIDEBAR_MAX_WIDTH = 520
_THUMB_SIZE = QSize(92, 120)  # 约 1:1.3（设计稿 aspect-[1/1.3]）
_ROLE = Qt.ItemDataRole.UserRole
_ANNOT_ROLE = Qt.ItemDataRole.UserRole + 1  # 卡片绘制所需的结构化数据
# 分段按钮窄于这个宽度时只显示文字（四个页签挤在 280px 里，图标 + 角标会盖住「批注」二字）
_SEGMENT_ICON_MIN_WIDTH = 74
_BADGE_ROOM = 16  # 带角标的按钮右内边距比左边多出的部分（与样式表 badged 一致：20 - 4）


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
        if getattr(self, "_badge_index", None) != button_index:
            for i, btn in enumerate(self._buttons):
                btn.setProperty("badged", "true" if i == button_index else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
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
        # 紧跟在「图标 + 文字」之后（样式表给这颗按钮加了右内边距，内容整体左移 _BADGE_ROOM / 2）
        content = btn.fontMetrics().horizontalAdvance(btn.text())
        if not btn.icon().isNull():
            content += btn.iconSize().width() + 4
        center = geo.center().x() - _BADGE_ROOM // 2
        x = min(center + content // 2 + 3, geo.right() - self._badge.width() - 1)
        self._badge.move(x, geo.center().y() - self._badge.height() // 2)
        self._badge.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._refresh_icons()
        self._move_thumb(animate=False)
        self._place_badge()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._move_thumb(animate=False)
        self._place_badge()

    def _refresh_icons(self) -> None:
        p = self._theme.palette
        roomy = self.width() >= _SEGMENT_ICON_MIN_WIDTH * len(self._buttons)
        for btn, name in zip(self._buttons, self._icons, strict=True):
            icon = self._theme.colored_icon(name, p.text_secondary, size=13) if roomy else QIcon()
            btn.setIcon(icon)


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
            text = data["quote"] if data.get("plain") else f"“{data['quote']}”"
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
        bookmarkActivated(object): 点击书签 → Bookmark
        bookmarkAddRequested(): 「＋」添加当前位置为书签
        bookmarkRenameRequested(int) / bookmarkDeleteRequested(int): 右键重命名 / 删除第 N 个书签
        annotationActivated(object): 点击批注 → Annotation
        annotationEditRequested(object): 编辑批注笔记 → Annotation
        annotationDeleteRequested(object): 删除批注 → Annotation
        exportNotesRequested(): 导出笔记为 Markdown
        thumbnailActivated(int): 点击缩略图 → 页码
        thumbnailsNeeded(list): 这些页的缩略图需要渲染（只含当前可见、尚未渲染的）
        materialKindChanged(str): 素材页换了类型（主窗口据此重新 set_materials）
        materialActivated(object): 单击素材 → Material（跳回原文）
        materialEditRequested(object) / materialDeleteRequested(object): 右键编辑备注 / 删除
        materialsExportRequested(str): 导出某类素材（类型）
    """

    tocActivated = Signal(int, object)
    bookmarkActivated = Signal(object)  # Bookmark
    bookmarkAddRequested = Signal()
    bookmarkRenameRequested = Signal(int)  # 书签序号
    bookmarkDeleteRequested = Signal(int)
    annotationActivated = Signal(object)
    annotationEditRequested = Signal(object)
    annotationDeleteRequested = Signal(object)
    exportNotesRequested = Signal()
    thumbnailActivated = Signal(int)
    thumbnailsNeeded = Signal(list)
    materialKindChanged = Signal(str)
    materialActivated = Signal(object)
    materialEditRequested = Signal(object)
    materialDeleteRequested = Signal(object)
    materialsExportRequested = Signal(str)

    TAB_TOC = 0
    TAB_ANNOTATIONS = 1
    TAB_THUMBNAILS = 2
    TAB_MATERIALS = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = get_theme()
        self._annotations: list[Annotation] = []
        self._materials: list[Material] = []
        self._thumb_done: set[int] = set()
        self._current_page = 0
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(30)
        self._thumb_timer.timeout.connect(self._emit_visible_thumbnails)
        self._build()

    @property
    def panel_width(self) -> int:
        """内容宽度（显隐动画只改外层的 maximumWidth，这个值不变）。"""
        return self._inner.width()

    def set_panel_width(self, width: int) -> None:
        self._inner.setFixedWidth(width)

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        inner = QWidget()
        inner.setFixedWidth(SIDEBAR_WIDTH)
        self._inner = inner
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 外层宽度做 0 ↔ 宽度 的显隐动画，内层固定宽度被裁剪（设计稿 overflow-hidden）
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        outer.addWidget(inner, alignment=Qt.AlignmentFlag.AlignLeft)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.tabs = _Segmented(
            [("目录", "list"), ("批注", "pen"), ("缩略图", "grid"), ("素材", "bookmark")]
        )
        layout.addWidget(self.tabs)
        self._pages = QStackedWidget()
        layout.addWidget(self._pages, stretch=1)

        # 目录
        toc_page = QWidget()
        toc_layout = QVBoxLayout(toc_page)
        toc_layout.setContentsMargins(0, 0, 0, 0)
        toc_layout.setSpacing(6)
        bm_header = QHBoxLayout()
        bm_header.setContentsMargins(0, 0, 0, 0)
        self.bookmark_title = QLabel("我的书签")
        self.bookmark_title.setProperty("variant", "muted")
        self.bookmark_add_btn = QToolButton()
        self.bookmark_add_btn.setObjectName("BookmarkAddButton")
        self.bookmark_add_btn.setText("＋ 添加")
        self.bookmark_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bookmark_add_btn.setToolTip("把当前位置加为书签（Ctrl+D）")
        self.bookmark_add_btn.clicked.connect(self.bookmarkAddRequested.emit)
        bm_header.addWidget(self.bookmark_title)
        bm_header.addStretch(1)
        bm_header.addWidget(self.bookmark_add_btn)
        toc_layout.addLayout(bm_header)
        self.bookmark_list = self._flat_tree("BookmarkList")
        self.bookmark_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bookmark_list.customContextMenuRequested.connect(self._on_bookmark_menu)
        self.bookmark_list.itemClicked.connect(self._on_bookmark_clicked)
        self.bookmark_list.itemActivated.connect(self._on_bookmark_clicked)
        self.bookmark_list.setToolTip("单击跳转 · 右键重命名 / 删除")
        toc_layout.addWidget(self.bookmark_list)
        self.bookmark_empty = QLabel("按 Ctrl+D 把当前位置加为书签")
        self.bookmark_empty.setProperty("variant", "muted")
        self.bookmark_empty.setWordWrap(True)
        toc_layout.addWidget(self.bookmark_empty)
        self.toc_title = QLabel("文档目录")
        self.toc_title.setProperty("variant", "muted")
        toc_layout.addWidget(self.toc_title)
        self._bookmarks: list[Bookmark] = []
        self.toc_tree = self._flat_tree("TocTree")
        self.toc_tree.itemClicked.connect(self._on_toc_clicked)
        self.toc_tree.itemActivated.connect(self._on_toc_clicked)
        self.toc_empty = QLabel("本文档没有目录（书签）")
        self.toc_empty.setProperty("variant", "muted")
        self.toc_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toc_empty.setWordWrap(True)
        toc_layout.addWidget(self.toc_tree, stretch=1)
        toc_layout.addWidget(self.toc_empty, stretch=1)
        self._pages.addWidget(toc_page)

        # 批注
        annot_page = QWidget()
        annot_layout = QVBoxLayout(annot_page)
        annot_layout.setContentsMargins(0, 0, 0, 0)
        annot_layout.setSpacing(8)
        self.annot_search = QLineEdit()
        self.annot_search.setObjectName("AnnotSearch")
        self.annot_search.setPlaceholderText("搜索批注原文 / 笔记")
        self.annot_search.setClearButtonEnabled(True)
        self.annot_search.textChanged.connect(lambda _t: self._apply_annot_filter())
        annot_layout.addWidget(self.annot_search)
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.annot_type_filter = QComboBox()
        self.annot_type_filter.setToolTip("按批注类型筛选")
        self.annot_type_filter.currentIndexChanged.connect(lambda _i: self._apply_annot_filter())
        self.annot_color_filter = QComboBox()
        self.annot_color_filter.setToolTip("按颜色筛选")
        self.annot_color_filter.currentIndexChanged.connect(lambda _i: self._apply_annot_filter())
        filters.addWidget(self.annot_type_filter, stretch=1)
        filters.addWidget(self.annot_color_filter, stretch=1)
        annot_layout.addLayout(filters)
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
        self.annot_no_match = QLabel("没有符合筛选条件的批注")
        self.annot_no_match.setProperty("variant", "muted")
        self.annot_no_match.setAlignment(Qt.AlignmentFlag.AlignCenter)
        annot_layout.addWidget(self.annot_no_match)
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
        self._pages.addWidget(self._build_materials())

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.set_annotations(())
        self.set_bookmarks(())

    def _build_materials(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.material_kind = QComboBox()
        self.material_kind.setToolTip("素材类型")
        for kind in KINDS:
            self.material_kind.addItem(_MATERIAL_TITLES[kind], kind)
        self.material_kind.currentIndexChanged.connect(self._on_material_kind)
        self.material_search = QLineEdit()
        self.material_search.setObjectName("AnnotSearch")
        self.material_search.setPlaceholderText("搜索")
        self.material_search.setClearButtonEnabled(True)
        self.material_search.textChanged.connect(lambda _t: self._apply_material_filter())
        head.addWidget(self.material_kind)
        head.addWidget(self.material_search, stretch=1)
        lay.addLayout(head)
        self.material_list = QListWidget()
        self.material_list.setObjectName("AnnotList")
        self.material_list.setItemDelegate(_AnnotationCardDelegate(self.material_list))
        self.material_list.setMouseTracking(True)
        self.material_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.material_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.material_list.customContextMenuRequested.connect(self._on_material_menu)
        self.material_list.itemClicked.connect(self._on_material_clicked)
        self.material_list.setToolTip("单击跳回原文 · 右键更多")
        lay.addWidget(self.material_list, stretch=1)
        self.material_empty = QLabel()
        self.material_empty.setProperty("variant", "muted")
        self.material_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.material_empty.setWordWrap(True)
        lay.addWidget(self.material_empty, stretch=1)
        self.material_no_match = QLabel("没有符合搜索的素材")
        self.material_no_match.setProperty("variant", "muted")
        self.material_no_match.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.material_no_match)
        self.material_export_btn = QPushButton()
        self.material_export_btn.setObjectName("ExportNotesButton")
        self.material_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.material_export_btn.clicked.connect(
            lambda: self.materialsExportRequested.emit(self.material_kind_key)
        )
        lay.addWidget(self.material_export_btn)
        self.set_materials(())
        return page

    @staticmethod
    def _flat_tree(name: str) -> QTreeWidget:
        """目录 / 书签共用的扁平两列列表：左标题、右页码。"""
        tree = QTreeWidget()
        tree.setObjectName(name)
        tree.setColumnCount(2)
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(False)
        tree.setItemsExpandable(False)
        tree.setIndentation(16)
        tree.setUniformRowHeights(True)
        tree.setMouseTracking(True)
        header = tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        return tree

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

    # ------------------------------------------------------------------ 书签
    def set_bookmarks(self, marks: Sequence[Bookmark]) -> None:
        """刷新书签列表（顺序由调用方给出，序号即信号里的 index）。"""
        self._bookmarks = list(marks)
        self.bookmark_list.clear()
        font = QFont(self.bookmark_list.font())
        font.setPixelSize(13)
        page_font = QFont(font)
        page_font.setPixelSize(11)
        p = self._theme.palette
        for i, mark in enumerate(self._bookmarks):
            title = mark.title or f"第 {mark.page + 1} 页"
            item = QTreeWidgetItem([title, str(mark.page + 1)])
            item.setData(0, _ROLE, i)
            item.setToolTip(0, f"{title}（第 {mark.page + 1} 页）")
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setFont(0, font)
            item.setFont(1, page_font)
            item.setForeground(0, QColor(p.text))
            item.setForeground(1, QColor(p.text_muted))
            self.bookmark_list.addTopLevelItem(item)
        has = bool(self._bookmarks)
        self.bookmark_list.setVisible(has)
        self.bookmark_empty.setVisible(not has)
        if has:  # 最多显示 6 行，其余滚动；下面留给文档目录
            row_h = max(self.bookmark_list.sizeHintForRow(0), 24)
            self.bookmark_list.setFixedHeight(min(len(self._bookmarks), 6) * row_h + 6)

    def bookmarks(self) -> list[Bookmark]:
        return list(self._bookmarks)

    def _bookmark_index(self, item: QTreeWidgetItem | None) -> int | None:
        if item is None:
            return None
        idx = item.data(0, _ROLE)
        return idx if isinstance(idx, int) and 0 <= idx < len(self._bookmarks) else None

    def _on_bookmark_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        idx = self._bookmark_index(item)
        if idx is not None:
            self.bookmarkActivated.emit(self._bookmarks[idx])

    def _on_bookmark_menu(self, pos: QPoint) -> None:
        idx = self._bookmark_index(self.bookmark_list.itemAt(pos))
        if idx is None:
            return
        menu = QMenu(self)
        menu.addAction("跳转", lambda: self.bookmarkActivated.emit(self._bookmarks[idx]))
        menu.addAction("重命名…", lambda: self.bookmarkRenameRequested.emit(idx))
        menu.addSeparator()
        menu.addAction("删除书签", lambda: self.bookmarkDeleteRequested.emit(idx))
        menu.exec(self.bookmark_list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ 批注
    def set_annotations(self, annotations: Sequence[Annotation]) -> None:
        """刷新批注卡片（按页、自上而下排列）。"""
        self._annotations = sorted(annotations, key=lambda a: (a.page_num, a.rect.y0, a.rect.x0))
        self.annot_list.clear()
        for annot in self._annotations:
            quote = annot.marked_text.replace("\n", " ").strip()
            note = annot.content.strip()
            if not quote and annot.annot_type in (0, 2) and note:  # 便签 / 文本框：正文为主
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
        for widget in (self.annot_search, self.annot_type_filter, self.annot_color_filter):
            widget.setVisible(count > 0)
        self._rebuild_filter_options()
        self._apply_annot_filter()

    def _rebuild_filter_options(self) -> None:
        """类型 / 颜色下拉只列出当前文档里出现过的；刷新后尽量保留原选择。"""
        types: dict[int, str] = {}
        colors: list[str] = []
        for annot in self._annotations:
            types.setdefault(annot.annot_type, annot.type_name)
            hex_color = _hex(annot.color)
            if hex_color not in colors:
                colors.append(hex_color)
        combos: tuple[tuple[QComboBox, str, list[tuple[str, object]]], ...] = (
            (self.annot_type_filter, "全部类型", [(n, t) for t, n in sorted(types.items())]),
            (self.annot_color_filter, "全部颜色", [(c.upper(), c) for c in colors]),
        )
        for combo, label, options in combos:
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, None)
            for text, data in options:
                if combo is self.annot_color_filter:
                    combo.addItem(_swatch(str(data)), text, data)
                else:
                    combo.addItem(text, data)
            pos = combo.findData(previous) if previous is not None else 0
            combo.setCurrentIndex(max(0, pos))
            combo.blockSignals(False)

    def _apply_annot_filter(self) -> None:
        """按搜索词（原文 / 笔记 / 类型，不区分大小写）、类型、颜色隐藏不匹配的卡片。"""
        needle = self.annot_search.text().strip().casefold()
        want_type = self.annot_type_filter.currentData()
        want_color = self.annot_color_filter.currentData()
        shown = 0
        for row in range(self.annot_list.count()):
            item = self.annot_list.item(row)
            annot = self._annotation_for(item)
            if annot is None:
                continue
            ok = (want_type is None or annot.annot_type == want_type) and (
                want_color is None or _hex(annot.color) == want_color
            )
            if ok and needle:
                haystack = f"{annot.marked_text} {annot.content} {annot.type_name}"
                ok = needle in haystack.casefold()
            item.setHidden(not ok)
            shown += int(ok)
        self.annot_list.setVisible(shown > 0)
        self.annot_no_match.setVisible(bool(self._annotations) and shown == 0)

    def visible_annotations(self) -> list[Annotation]:
        """当前筛选条件下可见的批注（按列表顺序）。"""
        result: list[Annotation] = []
        for row in range(self.annot_list.count()):
            item = self.annot_list.item(row)
            annot = self._annotation_for(item)
            if annot is not None and not item.isHidden():
                result.append(annot)
        return result

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

    # ------------------------------------------------------------------ 素材
    @property
    def material_kind_key(self) -> str:
        return str(self.material_kind.currentData() or KINDS[0])

    def set_material_kind(self, kind: str) -> None:
        """切到某类素材（会发 materialKindChanged，由主窗口填数据）。"""
        index = self.material_kind.findData(kind)
        if index >= 0:
            self.material_kind.setCurrentIndex(index)

    def _on_material_kind(self, _index: int) -> None:
        self.material_search.clear()
        self.materialKindChanged.emit(self.material_kind_key)

    def set_materials(self, items: Sequence[Material]) -> None:
        """刷新当前类型的素材卡片（最新的在前）。"""
        kind = self.material_kind_key
        self._materials = list(items)
        self.material_list.clear()
        for i, m in enumerate(self._materials):
            if m.kind == "word":
                phonetic = m.extra.get("phonetic", "")
                quote = f"{m.text}  /{phonetic}/" if phonetic else m.text
            elif m.kind == "term":
                quote = f"{m.text} → {m.note}" if m.note else m.text
            else:
                quote = m.text
            note = "" if m.kind == "term" else m.note
            meta = m.source_label or KIND_LABELS[m.kind]
            item = QListWidgetItem("\n".join(x for x in (meta, quote, note) if x))
            item.setToolTip(m.text if len(m.text) > 60 else "")
            item.setData(_ROLE, i)
            item.setData(
                _ANNOT_ROLE,
                {
                    "meta": meta,
                    "quote": quote,
                    "note": note,
                    "color": _MATERIAL_COLORS[m.kind],
                    "plain": m.kind in ("word", "term"),
                },
            )
            self.material_list.addItem(item)
        count = len(self._materials)
        self.material_empty.setText(_MATERIAL_HINTS[kind])
        self.material_empty.setVisible(count == 0)
        self.material_search.setVisible(count > 0)
        self.material_export_btn.setText(f"导出{_MATERIAL_TITLES[kind]} (.md / .csv)")
        self.material_export_btn.setEnabled(count > 0)
        self._apply_material_filter()

    def _apply_material_filter(self) -> None:
        needle = self.material_search.text().strip().casefold()
        shown = 0
        for row in range(self.material_list.count()):
            item = self.material_list.item(row)
            m = self._material_for(item)
            if m is None:
                continue
            ok = not needle or needle in f"{m.text} {m.note} {m.doc_title}".casefold()
            item.setHidden(not ok)
            shown += int(ok)
        self.material_list.setVisible(shown > 0)
        self.material_no_match.setVisible(bool(self._materials) and shown == 0)

    def visible_materials(self) -> list[Material]:
        return [
            m
            for row in range(self.material_list.count())
            if (m := self._material_for(self.material_list.item(row))) is not None
            and not self.material_list.item(row).isHidden()
        ]

    def _material_for(self, item: QListWidgetItem | None) -> Material | None:
        if item is None:
            return None
        idx = item.data(_ROLE)
        if isinstance(idx, int) and 0 <= idx < len(self._materials):
            return self._materials[idx]
        return None

    def _on_material_clicked(self, item: QListWidgetItem) -> None:
        m = self._material_for(item)
        if m is not None:
            self.materialActivated.emit(m)

    def _on_material_menu(self, pos: QPoint) -> None:
        m = self._material_for(self.material_list.itemAt(pos))
        if m is None:
            return
        menu = QMenu(self)
        if m.doc_path:
            menu.addAction("跳回原文", lambda: self.materialActivated.emit(m))
        menu.addAction("复制", lambda: _copy_text(_material_copy_text(m)))
        menu.addAction(_MATERIAL_EDIT_LABELS[m.kind], lambda: self.materialEditRequested.emit(m))
        menu.addSeparator()
        menu.addAction("删除", lambda: self.materialDeleteRequested.emit(m))
        menu.exec(self.material_list.viewport().mapToGlobal(pos))

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
        self.set_bookmarks(())
        self.set_annotations(())
        self.thumb_list.clear()
        self._thumb_done.clear()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._thumb_timer.start()


_MATERIAL_TITLES = {"word": "单词本", "corpus": "语料库", "quote": "引文库", "term": "术语库"}
_MATERIAL_COLORS = {"word": "#C96A49", "corpus": "#5B8DB8", "quote": "#7A9E6E", "term": "#B48A3C"}
_MATERIAL_EDIT_LABELS = {
    "word": "编辑释义…",
    "corpus": "编辑备注…",
    "quote": "编辑转述…",
    "term": "编辑译名…",
}
_MATERIAL_HINTS = {
    "word": "单词本还是空的。\n选中一个英文单词，在词卡上点「收藏」。",
    "corpus": "语料库还是空的。\n选中值得借鉴的句子，右键「加入语料」。",
    "quote": "引文库还是空的。\n选中原文，右键「加入引文」，会附一句可改写的转述。",
    "term": "术语库还是空的。\n选中术语，右键「加入术语」并填写译名；翻译时按它统一译法。",
}


def _material_copy_text(m: Material) -> str:
    if m.kind == "term":
        return f"{m.text}\t{m.note}"
    if m.kind == "quote" and m.note:
        return m.note
    return m.text


def _copy_text(text: str) -> None:
    from PySide6.QtGui import QGuiApplication

    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def _swatch(color: str) -> QIcon:
    pix = QPixmap(12, 12)
    pix.fill(QColor(color))
    return QIcon(pix)


def _hex(color: tuple[float, float, float] | None) -> str:
    if color is None:
        return "#FACC15"
    return str(QColor.fromRgbF(*color).name())
