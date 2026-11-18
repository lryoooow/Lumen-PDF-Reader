"""文献库首页（Lumen 极简清透版）。

没有打开文档、或在阅读时点击标题栏品牌标 / 网格按钮时显示，占满整个窗口::

    ┌ 简洁侧栏 212 ──┐┌ 全部文档 [4] ··········· [🔍 搜索文献… Ctrl K] [最近阅读▾] [▦|☰] [返回阅读] ┐
    │ ||| lumen.     ││ ┌ [📄] 上次阅读 · 论文标题 / 作者 · 页数 · 进度 ── [✦ AI 导读] [继续阅读 →] ┐ │
    │ [+ 导入文献]   ││ └───────────────────────────────────────────────────────────────────────────┘ │
    │ 文献库 ×4      ││ [全部 4]  [文件夹 A]  [文件夹 B]                                              │
    │ 文件夹         ││ ┌─ 论文卡片 ─────────┐ ┌─ 论文卡片 ─────────┐ ┌─ 论文卡片 ─────────┐          │
    │ ────────────── ││ │ [📄] 文件夹     ☆ │ │ [📄] 文件夹     ★ │ │ [📄] 文件夹     ☆ │          │
    │ ☾ 深色   关于  ││ │ 论文标题（两行内） │ │ 论文标题（两行内） │ │ 论文标题（两行内） │          │
    └────────────────┘│ │ 作者               │ │ 作者               │ │ 作者               │          │
                      │ │ 6 页    ● 正在阅读 │ │ 8 页      已读 45% │ │ 12 页     2 小时前 │          │
                      │ └────────────────────┘ └────────────────────┘ └────────────────────┘          │
                      │ ┄┄┄┄┄┄┄┄┄┄┄ + 拖放 PDF / EPUB 到窗口，或点击导入文献 ┄┄┄┄┄┄┄┄┄┄┄              │
                      └───────────────────────────────────────────────────────────────────────────────┘

设计原则：
* 功能壳层优先（无营销标语、无虚拟头像、无重复主题开关与重复标题）
* 搜索、排序、视图切换与返回阅读统一收敛至常驻顶栏，长列表滚动时始终可达
* 卡片专注呈现「标题 → 作者 → 页数与阅读状态」单主线层级，3 列标准桌面网格保证长标题舒展可读
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShortcut,
    QTextLayout,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import ThemeManager, get_theme
from pdfreader.gui.theme.icons import render_pixmap
from pdfreader.gui.widgets import motion

__all__ = [
    "ALL_FOLDERS",
    "SECTIONS",
    "SORTS",
    "LibraryEntry",
    "LibraryHome",
    "cover_variant",
    "display_title",
    "filter_entries",
    "relative_time",
]

# 导航「文献库」四项：(名称, 图标)
SECTIONS = (
    ("全部文档", "grid"),
    ("最近阅读", "stamp"),
    ("我的收藏", "bookmark"),
    ("含批注文档", "note"),
)
ALL_FOLDERS = "全部"
SORTS = ("最近阅读", "名称排序")
_MAX_COLLECTIONS = 6
_MAX_FILTER_CHIPS = 6
_COLLECTION_DOTS = ("#17795E", "#3B729F", "#7E6BA8", "#B87D3B")
_CONTENT_MAX = 1360


@dataclass(frozen=True)
class LibraryEntry:
    """文献库里的一份文档（由主窗口据设置组装）。"""

    path: str
    title: str  # 展示标题（元数据标题或文件名）
    author: str = ""
    pages: int = 0
    annotations: int = 0
    progress: float = 0.0  # 0..1；0 表示还没读过
    opened_at: float = 0.0
    favorite: bool = False
    is_open: bool = False

    @property
    def name(self) -> str:
        return Path(self.path).stem

    @property
    def folder(self) -> str:
        parent = Path(self.path).parent
        return parent.name or str(parent)

    @property
    def read(self) -> bool:
        return self.progress > 0


def display_title(meta_title: str, path: str | Path) -> str:
    """文献标题：PDF 元数据标题可用就用它，否则回退文件名（去扩展名）。"""
    title = " ".join(meta_title.split())
    for prefix in ("Microsoft Word - ", "Microsoft PowerPoint - "):
        if title.startswith(prefix):
            title = title[len(prefix) :]
    lowered = title.lower()
    if (
        len(title) < 3
        or lowered in ("untitled", "无标题", "title")
        or lowered.endswith((".doc", ".docx", ".tex", ".dvi", ".pdf", ".ps", ".pptx"))
    ):
        return Path(path).stem
    return title[:120]


def relative_time(opened_at: float, now: float | None = None) -> str:
    """「刚刚阅读 / N 分钟前 / N 小时前 / N 天前 / 日期」；没有记录时返回空串。"""
    if opened_at <= 0:
        return ""
    delta = max(0.0, (time.time() if now is None else now) - opened_at)
    if delta < 60:
        return "刚刚阅读"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 30 * 86400:
        return f"{int(delta // 86400)} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(opened_at))


def cover_variant(path: str) -> int:
    """按路径稳定选取四种低饱和纸感色之一。"""
    digest = hashlib.md5(path.encode("utf-8"), usedforsecurity=False).digest()
    return digest[0] % 4


def filter_entries(
    entries: list[LibraryEntry],
    *,
    section: str = SECTIONS[0][0],
    folder: str = ALL_FOLDERS,
    query: str = "",
    sort: str = SORTS[0],
) -> list[LibraryEntry]:
    """按导航分区 / 文件夹 / 关键词过滤，再排序（「最近阅读」保持最近文件原顺序）。"""
    q = query.strip().lower()
    out = [
        e
        for e in entries
        if (section != "最近阅读" or e.read)
        and (section != "我的收藏" or e.favorite)
        and (section != "含批注文档" or e.annotations > 0)
        and (folder == ALL_FOLDERS or e.folder == folder)
        and (not q or q in f"{e.name}\n{e.title}\n{e.author}\n{e.folder}".lower())
    ]
    if sort == "名称排序":
        out.sort(key=lambda e: e.name.lower())
    return out


def _with_alpha(color: str, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _font(px: int, *, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setPixelSize(px)
    font.setWeight(weight)
    return font


def _label(text: str, name: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(name)
    return lbl


def _passthrough(*widgets: QWidget) -> None:
    for w in widgets:
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ============================================================================ 导航栏部件
class _BrandMark(QWidget):
    """品牌标记：三根斜切的森玉绿几何竖条。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 24)
        _passthrough(self)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        pal = get_theme().palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(1, 3)
        p.shear(0, -0.22)
        p.setPen(Qt.PenStyle.NoPen)
        for i, (h, alpha) in enumerate(((12, 0.55), (18, 1.0), (15, 0.78))):
            p.setBrush(_with_alpha(pal.accent, alpha))
            p.drawRoundedRect(QRectF(i * 6.8, 18 - h + 1, 4.2, h), 1.4, 1.4)
        p.end()


class _NavItem(QPushButton):
    """导航项：左侧选中指示条 + 图标 + 名称 + 简洁计数（窄屏只留图标）。"""

    def __init__(self, label: str, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.label = label
        self.icon_name = icon
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)
        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        self._text = _label(label, "NavItemText")
        self.count = _label("0", "NavItemCount")
        _passthrough(self._icon, self._text, self.count)
        lay.addWidget(self._icon)
        lay.addWidget(self._text, stretch=1)
        lay.addWidget(self.count)
        self.setFixedHeight(36)
        self.toggled.connect(lambda _c: self.refresh())

    def set_count(self, n: int) -> None:
        self.count.setText(str(n))
        self.count.setProperty("zero", "true" if n == 0 else "false")
        _repolish(self.count)

    def set_compact(self, compact: bool) -> None:
        self._text.setVisible(not compact)
        self.count.setVisible(not compact)
        if compact:
            self.setFixedWidth(40)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(12, 0, 12, 0)

    def refresh(self) -> None:
        p = get_theme().palette
        checked = self.isChecked()
        active = "true" if checked else "false"
        color = p.nav_icon_active if checked else p.nav_text
        self._icon.setPixmap(render_pixmap(self.icon_name, color, 16))
        self._text.setProperty("active", active)
        self.count.setProperty("active", active)
        _repolish(self._text)
        _repolish(self.count)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.isChecked():
            pal = get_theme().palette
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.accent))
            p.drawRoundedRect(QRectF(3.0, (self.height() - 16.0) / 2.0, 3.0, 16.0), 1.5, 1.5)
            p.end()


class _CollectionItem(QPushButton):
    """「文件夹」筛选项：圆色点 + 文件夹名 + 文档数。"""

    def __init__(self, label: str, dot: str, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollectionItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.label = label
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(10)
        dot_lbl = QLabel()
        dot_lbl.setFixedSize(6, 6)
        dot_lbl.setStyleSheet(f"background-color:{dot}; border-radius:3px;")
        self._text = _label(label, "CollectionText")
        self._text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._count = _label(str(count), "NavItemCount")
        _passthrough(dot_lbl, self._text, self._count)
        lay.addWidget(dot_lbl)
        lay.addWidget(self._text, stretch=1)
        lay.addWidget(self._count)
        self.setFixedHeight(32)
        self.toggled.connect(lambda _c: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        active = "true" if self.isChecked() else "false"
        self._text.setProperty("active", active)
        self._count.setProperty("active", active)
        _repolish(self._text)
        _repolish(self._count)
        self.update()


# ============================================================================ 微缩纸张与卡片部件
class _Cover(QAbstractButton):
    """安静的直立文献微缩页标（无倾斜几何杂线，点击打开文档）。"""

    def __init__(
        self,
        entry: LibraryEntry | None,
        compact: bool = False,
        *,
        size: tuple[int, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.compact = compact
        self.variant = cover_variant(entry.path) if entry is not None else 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if entry is not None:
            name = Path(entry.path).name
            self.setToolTip(f"打开 {name}")
            self.setAccessibleName(f"打开 {name}")
        w, h = size if size is not None else ((26, 32) if compact else (28, 34))
        self.setFixedSize(w, h)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.height())

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        pal = get_theme().palette
        bg, ink = pal.covers[self.variant]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fold = 6.5

        page = QPainterPath()
        page.moveTo(r.left() + 4, r.top())
        page.lineTo(r.right() - fold, r.top())
        page.lineTo(r.right(), r.top() + fold)
        page.lineTo(r.right(), r.bottom() - 4)
        page.quadTo(r.right(), r.bottom(), r.right() - 4, r.bottom())
        page.lineTo(r.left() + 4, r.bottom())
        page.quadTo(r.left(), r.bottom(), r.left(), r.bottom() - 4)
        page.lineTo(r.left(), r.top() + 4)
        page.quadTo(r.left(), r.top(), r.left() + 4, r.top())
        page.closeSubpath()

        p.fillPath(page, QColor(bg))
        p.setPen(QPen(_with_alpha(ink, 0.24), 1.0))
        p.drawPath(page)

        # 右上折角细节
        crease = QPainterPath()
        crease.moveTo(r.right() - fold, r.top())
        crease.lineTo(r.right() - fold, r.top() + fold)
        crease.lineTo(r.right(), r.top() + fold)
        p.setPen(QPen(_with_alpha(ink, 0.20), 1.0))
        p.drawPath(crease)

        # 内页抽象正文横线（克制清晰，代表学术论文排版）
        lx = r.left() + 5.5
        lw = r.width() - 11.0
        ly = r.top() + 11.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_with_alpha(ink, 0.52))
        p.drawRoundedRect(QRectF(lx, ly, lw * 0.68, 2.0), 1.0, 1.0)
        p.setBrush(_with_alpha(ink, 0.24))
        p.drawRoundedRect(QRectF(lx, ly + 5.0, lw, 1.6), 0.8, 0.8)
        p.drawRoundedRect(QRectF(lx, ly + 9.2, lw * 0.84, 1.6), 0.8, 0.8)
        if r.height() >= 34:
            p.drawRoundedRect(QRectF(lx, ly + 13.4, lw * 0.60, 1.6), 0.8, 0.8)
        p.end()


class _CardTitleButton(QAbstractButton):
    """自绘多行省略标题按钮（网格视图最多 2 行，列表视图单行，中英文混排零截断错位）。"""

    def __init__(self, text: str, compact: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BookTitle")
        self.setText(text)
        self.compact = compact
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if compact:
            self.setFixedHeight(20)
        else:
            self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_card_hovered(self, hovered: bool) -> None:
        if self._hovered != hovered:
            self._hovered = hovered
            self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.set_card_hovered(True)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.set_card_hovered(False)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        pal = get_theme().palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = _font(13 if self.compact else 14, weight=QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(pal.accent_text if self._hovered else pal.text))
        fm = QFontMetrics(font)
        w = max(20, self.width())
        raw = " ".join(self.text().split())

        if self.compact:
            elided = fm.elidedText(raw, Qt.TextElideMode.ElideRight, w)
            p.drawText(
                QRectF(0, 0, w, self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided,
            )
            p.end()
            return

        layout = QTextLayout(raw, font)
        layout.beginLayout()
        line1 = layout.createLine()
        if not line1.isValid():
            layout.endLayout()
            p.end()
            return
        line1.setLineWidth(w)
        line1.setPosition(QPointF(0, 0))
        end1 = line1.textStart() + line1.textLength()
        layout.endLayout()
        layout.draw(p, QPointF(0, 0))

        rest = raw[end1:].lstrip()
        if rest:
            line2_text = fm.elidedText(rest, Qt.TextElideMode.ElideRight, w)
            p.drawText(
                QRectF(0, 20, w, 20),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                line2_text,
            )
        p.end()


class _Star(QToolButton):
    """右上角收藏书签（已收藏常亮森玉绿；未收藏平时极淡，卡片悬停时浮现）。"""

    def __init__(self, starred: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BookStar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(26, 26)
        self.starred = starred
        self._hover = False
        self._card_hover = False
        tip = "取消收藏" if starred else "收藏文档"
        self.setToolTip(tip)
        self.setAccessibleName(tip)

    def set_card_hovered(self, hovered: bool) -> None:
        if self._card_hover != hovered:
            self._card_hover = hovered
            self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        pal = get_theme().palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.surface_hover))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        opacity = 1.0 if self.starred else (0.85 if (self._hover or self._card_hover) else 0.28)
        p.setOpacity(opacity)
        ox, oy = (self.width() - 14) / 2, (self.height() - 14) / 2
        color = pal.accent if self.starred else pal.text_muted
        if self.starred:
            s = 14 / 24
            path = QPainterPath()
            pts = ((6, 3), (18, 3), (18, 21), (12, 17), (6, 21))
            path.moveTo(QPointF(ox + pts[0][0] * s, oy + pts[0][1] * s))
            for x, y in pts[1:]:
                path.lineTo(QPointF(ox + x * s, oy + y * s))
            path.closeSubpath()
            p.fillPath(path, _with_alpha(pal.accent, 0.22))
        p.drawPixmap(int(ox), int(oy), render_pixmap("bookmark", color, 14))
        p.end()


class _ProgressLine(QWidget):
    """行内紧凑 44×4 圆角阅读进度条（未读文档隐藏，不与卡片底边框粘连）。"""

    def __init__(self, value: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = max(0.0, min(1.0, value))
        self.setFixedSize(44, 4)
        self.setVisible(self.value > 0)
        _passthrough(self)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        if self.value <= 0:
            return
        pal = get_theme().palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.surface_hover))
        p.drawRoundedRect(QRectF(self.rect()), 2.0, 2.0)
        p.setBrush(_with_alpha(pal.accent, 0.85))
        p.drawRoundedRect(QRectF(0, 0, max(4.0, self.width() * self.value), 4.0), 2.0, 2.0)
        p.end()


class _BookCard(QFrame):
    """极简文献卡片（网格：清爽留白四层排版；列表：紧凑单行学术表格行）。"""

    openRequested = Signal(str)
    favoriteToggled = Signal(str)

    def __init__(self, entry: LibraryEntry, compact: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BookCardHolder")
        self.entry = entry
        self.compact = compact
        self._press_inside = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName("BookCard")
        self.card.setCursor(Qt.CursorShape.PointingHandCursor)
        outer.addWidget(self.card)

        self.cover = _Cover(entry, compact)
        self.star = _Star(entry.favorite)
        self.title_btn = _CardTitleButton(entry.title, compact)
        tip = f"{entry.title}\n{entry.path}" if entry.title != entry.path else entry.path
        self.card.setToolTip(tip)
        self.title_btn.setToolTip(tip)

        tag = _label(entry.folder, "BookTag")
        tag.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        is_epub = entry.path.lower().endswith(".epub")
        ext_badge = _label("EPUB", "BookTagPdf") if is_epub else None

        author_str = entry.author or (entry.name if entry.name != entry.title else "")
        self.author_lbl = _label(author_str, "BookAuthor")
        self.author_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        info_text = f"{entry.pages} 页" if entry.pages else "文档"
        if entry.annotations:
            info_text += f" · {entry.annotations} 条批注"
        self.info = _label(info_text, "BookInfo")

        if entry.is_open:
            when_str = "● 正在阅读"
        elif entry.progress > 0:
            when_str = f"{round(entry.progress * 100)}%"
        else:
            when_str = relative_time(entry.opened_at)
        self.when = _label(when_str, "BookStatus" if entry.is_open else "BookInfo")

        self.progress = _ProgressLine(0.0 if entry.is_open else entry.progress)
        _passthrough(tag, self.author_lbl, self.info, self.when)
        if ext_badge is not None:
            _passthrough(ext_badge)

        status_box = QHBoxLayout()
        status_box.setContentsMargins(0, 0, 0, 0)
        status_box.setSpacing(6)
        status_box.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignVCenter)
        status_box.addWidget(self.when, alignment=Qt.AlignmentFlag.AlignVCenter)

        if compact:
            self.card.setFixedHeight(56)
            row = QHBoxLayout(self.card)
            row.setContentsMargins(14, 0, 12, 0)
            row.setSpacing(12)
            row.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignVCenter)

            title_col = QVBoxLayout()
            title_col.setContentsMargins(0, 0, 0, 0)
            title_col.setSpacing(2)
            title_col.addStretch(1)
            title_col.addWidget(self.title_btn)
            if author_str:
                title_col.addWidget(self.author_lbl)
            title_col.addStretch(1)
            row.addLayout(title_col, stretch=1)

            if ext_badge is not None:
                ext_badge.setFixedHeight(20)
                ext_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                row.addWidget(ext_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
            tag.setObjectName("BookFolderPill")
            tag.setFixedHeight(22)
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tag.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            row.addWidget(tag, alignment=Qt.AlignmentFlag.AlignVCenter)
            row.addSpacing(4)
            row.addWidget(self.info, alignment=Qt.AlignmentFlag.AlignVCenter)
            row.addSpacing(8)
            status_wrap = QWidget()
            status_wrap.setMinimumWidth(92)
            _passthrough(status_wrap)
            sw_lay = QHBoxLayout(status_wrap)
            sw_lay.setContentsMargins(0, 0, 0, 0)
            sw_lay.setSpacing(6)
            sw_lay.addStretch(1)
            sw_lay.addLayout(status_box)
            row.addWidget(status_wrap, alignment=Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self.star, alignment=Qt.AlignmentFlag.AlignVCenter)
        else:
            self.card.setFixedHeight(148)
            vlay = QVBoxLayout(self.card)
            vlay.setContentsMargins(16, 14, 14, 13)
            vlay.setSpacing(0)

            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            top_row.addWidget(self.cover)
            top_row.addWidget(tag, stretch=1)
            if ext_badge is not None:
                ext_badge.setFixedHeight(18)
                ext_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                top_row.addWidget(ext_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
            top_row.addWidget(self.star)
            vlay.addLayout(top_row)
            vlay.addSpacing(10)

            vlay.addWidget(self.title_btn)
            vlay.addSpacing(3)
            vlay.addWidget(self.author_lbl)
            vlay.addStretch(1)

            foot = QHBoxLayout()
            foot.setContentsMargins(0, 0, 2, 0)
            foot.setSpacing(8)
            foot.addWidget(self.info, stretch=1)
            foot.addLayout(status_box)
            vlay.addLayout(foot)

        self.cover.clicked.connect(lambda: self.openRequested.emit(entry.path))
        self.title_btn.clicked.connect(lambda: self.openRequested.emit(entry.path))
        self.star.clicked.connect(lambda: self.favoriteToggled.emit(entry.path))

    def set_cover_height(self, h: int) -> None:
        del h

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_inside = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_inside:
            self._press_inside = False
            if self.rect().contains(event.position().toPoint()):
                self.openRequested.emit(self.entry.path)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.title_btn.set_card_hovered(True)
        self.star.set_card_hovered(True)
        self._set_shadow(True)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._press_inside = False
        self.title_btn.set_card_hovered(False)
        self.star.set_card_hovered(False)
        self._set_shadow(False)

    def _set_shadow(self, on: bool) -> None:
        if on and motion.enabled():
            effect = QGraphicsDropShadowEffect(self.card)
            effect.setBlurRadius(18)
            effect.setOffset(0, 4)
            effect.setColor(QColor(get_theme().palette.soft_shadow))
            self.card.setGraphicsEffect(effect)
        else:
            self.card.setGraphicsEffect(None)


# ============================================================================ 顶部单行「上次阅读」横条
class _HeroProgress(QWidget):
    """「继续阅读」横条上的 72×4 进度条。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = 0.0
        self.setFixedSize(72, 4)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        pal = get_theme().palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.surface_hover))
        p.drawRoundedRect(QRectF(self.rect()), 2.0, 2.0)
        if self.value > 0:
            p.setBrush(QColor(pal.accent))
            p.drawRoundedRect(QRectF(0, 0, max(4.0, self.width() * self.value), 4.0), 2.0, 2.0)
        p.end()


class _InsightAction(QWidget):
    """嵌入上次阅读横条的轻量「AI 导读」快捷入口（兼容 insight_card.button 接口）。"""

    exploreRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InsightAction")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.button = QPushButton("AI 导读")
        self.button.setObjectName("InsightButton")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setToolTip("打开最近文献并展开右侧 AI 导读工作台")
        self.button.setAccessibleName("AI 导读")
        self.button.setIconSize(QSize(14, 14))
        self.button.setFixedHeight(32)
        self.button.clicked.connect(self.exploreRequested.emit)
        lay.addWidget(self.button)
        self.refresh()

    def refresh(self) -> None:
        p = get_theme().palette
        self.button.setIcon(QIcon(render_pixmap("ai", p.accent_text, 14)))

    def set_compact(self, compact: bool) -> None:
        self.button.setVisible(not compact)


class _ContinueCard(QFrame):
    """紧凑单行「上次阅读」接续条（仅在有文献且未筛选时显示，取代高大插画横幅）。"""

    continueRequested = Signal()

    def __init__(self, insight: _InsightAction, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContinueCard")
        self._entry: LibraryEntry | None = None
        self._insight = insight

        lay = QHBoxLayout(self)
        self._lay = lay
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)

        self._thumb = _Cover(None, size=(34, 42))
        self._thumb.clicked.connect(self.continueRequested.emit)
        lay.addWidget(self._thumb)

        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.kicker = _label("上次阅读", "ContinueBadge")
        self.title = _label("", "ContinueTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.kicker)
        top.addWidget(self.title, stretch=1)
        mid.addLayout(top)

        sub = QHBoxLayout()
        sub.setSpacing(10)
        self.meta_left = _label("", "ContinueMeta")
        self.meta_left.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.bar = _HeroProgress()
        self.read_label = _label("", "ContinueRead")
        sub.addWidget(self.meta_left)
        sub.addWidget(self.bar)
        sub.addWidget(self.read_label)
        sub.addStretch(1)
        mid.addLayout(sub)

        lay.addLayout(mid, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self._insight)
        self.button = QPushButton()
        self.button.setObjectName("ContinueButton")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.button.setIconSize(QSize(14, 14))
        self.button.setFixedHeight(32)
        self.button.clicked.connect(self.continueRequested.emit)
        actions.addWidget(self.button)
        lay.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        pal = get_theme().palette
        self.button.setIcon(QIcon(render_pixmap("forward", pal.on_accent, 14)))
        self._insight.refresh()
        self.set_entry(self._entry)

    def set_entry(self, entry: LibraryEntry | None) -> None:
        self._entry = entry
        self._thumb.entry = entry
        self._thumb.variant = cover_variant(entry.path) if entry is not None else 0
        self._thumb.update()
        if entry is None:
            self.kicker.setText("开始阅读")
            self.title.setText("打开第一篇文献")
            self.meta_left.setText("PDF / EPUB 本地精读")
            self.bar.setVisible(False)
            self.read_label.setText("")
            self.button.setText("导入文献")
        else:
            self.kicker.setText("正在阅读" if entry.is_open else "上次阅读")
            self.title.setText(entry.title)
            self.title.setToolTip(f"{entry.title}\n{entry.path}")
            parts: list[str] = []
            if entry.author:
                parts.append(entry.author)
            elif entry.folder:
                parts.append(entry.folder)
            if entry.pages:
                parts.append(f"{entry.pages} 页")
            if entry.annotations:
                parts.append(f"{entry.annotations} 条批注")
            self.meta_left.setText(" · ".join(parts))
            has_prog = entry.progress > 0
            self.bar.setVisible(has_prog)
            self.bar.value = entry.progress
            self.bar.update()
            self.read_label.setText(
                f"已读 {round(entry.progress * 100)}%"
                if has_prog
                else relative_time(entry.opened_at)
            )
            self.button.setText("回到阅读" if entry.is_open else "继续阅读")
        self.update()

    def set_scale(self, cls: str) -> None:
        compact = cls in ("sm", "xs")
        self._thumb.setVisible(cls != "xs")
        self._insight.set_compact(compact)
        pad_x = 12 if compact else 16
        self._lay.setContentsMargins(pad_x, 11, pad_x, 11)


# ============================================================================ 首页主体
class LibraryHome(QWidget):
    """文献库首页（极简导航栏 + 一体化常驻命令顶栏 + 上次阅读条 + 清爽文献网格）。"""

    openRequested = Signal()
    openRecentRequested = Signal(str)
    favoriteToggled = Signal(str)
    themeToggleRequested = Signal()
    aboutRequested = Signal()
    exploreAiRequested = Signal()
    returnRequested = Signal()
    viewModeChanged = Signal(str)
    recoveryRestoreRequested = Signal()
    recoveryDiscardRequested = Signal()
    recoveryLaterRequested = Signal()

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LibraryRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = theme
        self._entries: list[LibraryEntry] = []
        self.section = SECTIONS[0][0]
        self.folder = ALL_FOLDERS
        self.view_mode = "grid"
        self._columns = 3
        self._cards: list[_BookCard] = []
        self._chips: list[QPushButton] = []
        self._collections: list[_CollectionItem] = []
        self.width_class = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_nav())

        main = QWidget()
        main.setObjectName("StudioMain")
        main_lay = QVBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)
        main_lay.addWidget(self._build_topbar())
        self._recovery_banner = self._build_recovery_banner()
        main_lay.addWidget(self._recovery_banner)
        main_lay.addWidget(self._build_scroll(), stretch=1)
        root.addWidget(main, stretch=1)

        QShortcut(QKeySequence("Ctrl+K"), self, self.focus_search)
        theme.changed.connect(self._on_theme_changed)
        self._on_theme_changed()
        self.apply_width(1280)
        self.set_entries([])

    # ---------------------------------------------------------------- 构建：左侧导航栏
    def _build_nav(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("StudioNavScroll")
        scroll.setAccessibleName("文献库导航")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setFixedWidth(212)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._nav = scroll

        nav = QFrame()
        nav.setObjectName("StudioNav")
        scroll.setWidget(nav)
        lay = QVBoxLayout(nav)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._nav_lay = lay
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(0)

        # 顶部品牌标（点击重置筛选）
        self._brand_btn = QPushButton()
        self._brand_btn.setObjectName("StudioBrandBtn")
        self._brand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._brand_btn.setToolTip("回到全部文档")
        self._brand_btn.setFixedHeight(32)
        brand = QHBoxLayout(self._brand_btn)
        self._brand_lay = brand
        brand.setContentsMargins(8, 0, 8, 0)
        brand.setSpacing(9)
        self._brand_mark = _BrandMark()
        brand.addWidget(self._brand_mark)
        self._brand_text = _label('lumen<span style="color:#17795E;">.</span>', "StudioBrand")
        self._brand_text.setTextFormat(Qt.TextFormat.RichText)
        _passthrough(self._brand_text)
        brand.addWidget(self._brand_text)
        brand.addStretch(1)
        self._brand_btn.clicked.connect(self.reset_filters)
        lay.addWidget(self._brand_btn)
        lay.addSpacing(14)

        # 主操作：导入文献
        self.import_button = QPushButton()
        self.import_button.setObjectName("ImportButton")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.setToolTip("导入 PDF / EPUB  (Ctrl+O)")
        self.import_button.setAccessibleName("导入文献")
        self.import_button.setFixedHeight(36)
        imp = QHBoxLayout(self.import_button)
        imp.setContentsMargins(12, 0, 11, 0)
        imp.setSpacing(8)
        self._import_icon = QLabel()
        self._import_icon.setFixedSize(15, 15)
        self._import_text = _label("导入文献", "ImportText")
        self._import_key = _label("Ctrl+O", "ImportKey")
        _passthrough(self._import_icon, self._import_text, self._import_key)
        imp.addWidget(self._import_icon)
        imp.addWidget(self._import_text, 1)
        imp.addWidget(self._import_key)
        self.import_button.clicked.connect(self.openRequested.emit)
        lay.addWidget(self.import_button)
        lay.addSpacing(20)

        # 分区导航
        self._lib_caption = _label("文献库", "NavCaption")
        lay.addWidget(self._lib_caption)
        lay.addSpacing(6)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self.nav_items: dict[str, _NavItem] = {}
        for label, icon in SECTIONS:
            item = _NavItem(label, icon)
            self._nav_group.addButton(item)
            item.clicked.connect(lambda _c=False, s=label: self.set_section(s))
            self.nav_items[label] = item
            lay.addWidget(item)
            lay.addSpacing(2)
        self.nav_items[self.section].setChecked(True)

        # 文件夹筛选
        self._col_caption_box = QWidget()
        cap = QHBoxLayout(self._col_caption_box)
        cap.setContentsMargins(0, 16, 0, 6)
        cap.addWidget(_label("文件夹", "NavCaption"))
        cap.addStretch(1)
        lay.addWidget(self._col_caption_box)
        self._col_box = QWidget()
        self._col_lay = QVBoxLayout(self._col_box)
        self._col_lay.setContentsMargins(0, 0, 0, 0)
        self._col_lay.setSpacing(1)
        lay.addWidget(self._col_box)
        lay.addStretch(1)

        # 底部极简工具栏（单一外观切换入口 + 关于，去除虚拟头像与标语框）
        self._nav_bottom = QWidget()
        bottom = QVBoxLayout(self._nav_bottom)
        bottom.setContentsMargins(0, 14, 0, 0)
        bottom.setSpacing(8)
        rule = QFrame()
        rule.setObjectName("NavRule")
        rule.setFixedHeight(1)
        bottom.addWidget(rule)

        util = QHBoxLayout()
        util.setContentsMargins(2, 0, 2, 0)
        util.setSpacing(6)
        self.profile = QPushButton("深色模式")
        self.profile.setObjectName("ProfileRow")
        self.profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.profile.setToolTip("切换浅色 / 深色外观")
        self.profile.setAccessibleName("切换主题")
        self.profile.setIconSize(QSize(15, 15))
        self.profile.setFixedHeight(32)
        self.profile.clicked.connect(self.themeToggleRequested.emit)
        self.theme_button = self.profile  # 统一主题按钮引用，避免多处重复渲染月亮图标
        util.addWidget(self.profile)
        util.addStretch(1)

        self.about_button = QPushButton("关于")
        self.about_button.setObjectName("FooterLink")
        self.about_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.about_button.setToolTip("关于 Lumen")
        self.about_button.setAccessibleName("关于 Lumen")
        self.about_button.setFixedHeight(32)
        self.about_button.clicked.connect(self.aboutRequested.emit)
        util.addWidget(self.about_button)
        bottom.addLayout(util)
        lay.addWidget(self._nav_bottom)

        self._compact_theme = QToolButton()
        self._compact_theme.setObjectName("NavIconButton")
        self._compact_theme.setToolTip("切换浅色 / 深色外观")
        self._compact_theme.setAccessibleName("切换主题")
        self._compact_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compact_theme.setIconSize(QSize(16, 16))
        self._compact_theme.clicked.connect(self.themeToggleRequested.emit)
        self._compact_theme.setVisible(False)
        lay.addWidget(self._compact_theme, alignment=Qt.AlignmentFlag.AlignHCenter)
        return scroll

    # ---------------------------------------------------------------- 构建：一体化常驻命令顶栏
    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("StudioTopbar")
        bar.setFixedHeight(56)
        self._topbar = bar
        lay = QHBoxLayout(bar)
        self._topbar_lay = lay
        lay.setContentsMargins(28, 0, 28, 0)
        lay.setSpacing(10)

        self.heading = _label(self.section, "LibraryHeading")
        self.crumb = self.heading
        self.heading_count = _label("0", "LibraryCount")
        self.heading_count.setFixedHeight(20)
        self.heading_count.setMinimumWidth(22)
        self.heading_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.heading, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.heading_count, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)

        # 搜索框（支持聚焦高亮环与 Ctrl+K；使用最大宽度而非死锁最小宽度，保证窗口可自由缩窄）
        self._search_box = QFrame()
        self._search_box.setObjectName("LibrarySearch")
        self._search_box.setProperty("focused", "false")
        self._search_box.setFixedHeight(34)
        self._search_box.setMinimumWidth(96)
        self._search_box.setMaximumWidth(260)
        self._search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sb = QHBoxLayout(self._search_box)
        sb.setContentsMargins(10, 0, 8, 0)
        sb.setSpacing(7)
        self._search_icon = QLabel()
        self._search_icon.setFixedSize(15, 15)
        self.search = QLineEdit()
        self.search.setObjectName("LibrarySearchInput")
        self.search.setPlaceholderText("搜索标题、作者或文件夹…")
        self.search.setAccessibleName("搜索文档")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(48)
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self._on_search_changed)
        self._kbd = _label("Ctrl K", "Kbd")
        self._kbd.setFixedHeight(20)
        self._kbd.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb.addWidget(self._search_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        sb.addWidget(self.search, 1)
        sb.addWidget(self._kbd, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._search_box, 1)

        # 排序下拉
        self.sort = QComboBox()
        self.sort.setObjectName("LibrarySort")
        self.sort.setAccessibleName("文档排序")
        self.sort.setFixedHeight(34)
        self.sort.addItems(list(SORTS))
        self.sort.currentIndexChanged.connect(lambda _i: self._rebuild())
        lay.addWidget(self.sort)

        # 网格 / 列表切换
        toggle = QFrame()
        toggle.setObjectName("ViewToggle")
        toggle.setFixedHeight(34)
        tl = QHBoxLayout(toggle)
        tl.setContentsMargins(3, 3, 3, 3)
        tl.setSpacing(2)
        self._view_group = QButtonGroup(self)
        self.view_buttons: dict[str, QToolButton] = {}
        for mode, tip in (("grid", "网格视图"), ("list", "列表视图")):
            btn = QToolButton()
            btn.setObjectName("ViewToggleButton")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(lambda _c=False, m=mode: self.set_view_mode(m, notify=True))
            self._view_group.addButton(btn)
            self.view_buttons[mode] = btn
            tl.addWidget(btn)
        self.view_buttons["grid"].setChecked(True)
        lay.addWidget(toggle)

        # 返回阅读（仅后台有已打开标签时显示）
        self.return_button = QPushButton("返回阅读")
        self.return_button.setObjectName("ReturnButton")
        self.return_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.return_button.setIconSize(QSize(14, 14))
        self.return_button.setFixedHeight(34)
        self.return_button.clicked.connect(self.returnRequested.emit)
        self.return_button.setVisible(False)
        lay.addWidget(self.return_button)
        return bar

    # ---------------------------------------------------------------- 构建：崩溃恢复提示条
    def _build_recovery_banner(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("RecoveryBanner")
        bar.setFixedHeight(48)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(28, 0, 28, 0)
        lay.setSpacing(10)
        self._recovery_title = _label("", "RecoveryTitle")
        lay.addWidget(self._recovery_title)
        self._recovery_text = _label("", "RecoveryText")
        self._recovery_text.setWordWrap(False)
        self._recovery_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._recovery_text, 1)

        restore = QPushButton("恢复")
        restore.setObjectName("RecoveryRestore")
        restore.setCursor(Qt.CursorShape.PointingHandCursor)
        restore.setToolTip("把快照写回原文件（原文件会先备份）")
        restore.clicked.connect(self.recoveryRestoreRequested.emit)
        lay.addWidget(restore)
        discard = QPushButton("丢弃")
        discard.setObjectName("RecoveryDiscard")
        discard.setCursor(Qt.CursorShape.PointingHandCursor)
        discard.setToolTip("删除这些恢复快照，不可撤销")
        discard.clicked.connect(self.recoveryDiscardRequested.emit)
        lay.addWidget(discard)
        later = QPushButton("稍后")
        later.setObjectName("RecoveryLater")
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        later.setToolTip("保留快照，下次启动再提示")
        later.clicked.connect(self.recoveryLaterRequested.emit)
        lay.addWidget(later)
        bar.setVisible(False)
        return bar

    # ---------------------------------------------------------------- 构建：内容滚动区
    def _build_scroll(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("StudioScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area = scroll

        holder = QWidget()
        holder.setObjectName("StudioScrollBody")
        center = QHBoxLayout(holder)
        center.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("StudioContent")
        content.setMaximumWidth(_CONTENT_MAX)
        center.addWidget(content)
        scroll.setWidget(holder)

        lay = QVBoxLayout(content)
        self._content_lay = lay
        lay.setContentsMargins(28, 22, 28, 24)
        lay.setSpacing(16)

        # 仅特定分区需要说明范围时才显示（其余分区隐藏，零多余副标题）
        self.heading_desc = _label("", "LibraryDesc")
        self.heading_desc.setWordWrap(True)
        self.heading_desc.setVisible(False)
        lay.addWidget(self.heading_desc)

        # 单行紧凑「上次阅读」横条
        self.insight_card = _InsightAction()
        self.insight_card.exploreRequested.connect(self.exploreAiRequested.emit)
        self.continue_card = _ContinueCard(self.insight_card)
        self.continue_card.continueRequested.connect(self._on_continue)
        lay.addWidget(self.continue_card)

        # 多文件夹快速筛选胶囊（少于 2 个文件夹时自动隐藏，避免同义重复）
        self._filter_box = self._build_filters()
        lay.addWidget(self._filter_box)

        # 文献网格 / 列表
        self._grid_host = QWidget()
        self._grid_host.setObjectName("BookGrid")
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(14)
        lay.addWidget(self._grid_host)

        self._empty = self._build_empty()
        lay.addWidget(self._empty)

        lay.addWidget(self._build_quiet_import())
        lay.addStretch(1)
        return scroll

    def _build_filters(self) -> QWidget:
        box = QWidget()
        self._filter_lay = QHBoxLayout(box)
        self._filter_lay.setContentsMargins(0, 0, 0, 0)
        self._filter_lay.setSpacing(6)
        self._filter_lay.addStretch(1)
        box.setVisible(False)
        return box

    def _build_empty(self) -> QWidget:
        box = QFrame()
        box.setObjectName("LibraryEmpty")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(40, 56, 40, 56)
        lay.setSpacing(0)
        self._empty_icon = QLabel()
        self._empty_icon.setFixedSize(44, 44)
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_icon.setObjectName("EmptyIconBadge")
        lay.addWidget(self._empty_icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(14)
        self.empty_title = _label("文献库还是空的", "EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_title)
        lay.addSpacing(6)
        self.empty_body = _label(
            "导入 PDF / EPUB 或直接将文件拖入窗口，阅读进度与批注自动保存在本地。", "EmptyBody"
        )
        self.empty_body.setWordWrap(True)
        self.empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_body)
        lay.addSpacing(18)
        self.empty_button = QPushButton("导入文献")
        self.empty_button.setObjectName("EmptyAction")
        self.empty_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.empty_button.clicked.connect(self._on_empty_action)
        lay.addWidget(self.empty_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.setVisible(False)
        return box

    def _build_quiet_import(self) -> QWidget:
        self.quiet_import = QPushButton()
        self.quiet_import.setObjectName("QuietImport")
        self.quiet_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quiet_import.setAccessibleName("导入文献")
        self.quiet_import.setProperty("dragging", "false")
        self.quiet_import.setFixedHeight(40)
        qi = QHBoxLayout(self.quiet_import)
        qi.setContentsMargins(16, 0, 16, 0)
        qi.setSpacing(6)
        self._quiet_icon = QLabel()
        self._quiet_icon.setFixedSize(14, 14)
        cta = _label("导入文献", "QuietImportCta")
        self._quiet_hint = _label("或直接将 PDF / EPUB 拖入窗口", "QuietImportMuted")
        _passthrough(self._quiet_icon, cta, self._quiet_hint)
        qi.addStretch(1)
        qi.addWidget(self._quiet_icon)
        qi.addWidget(cta)
        qi.addWidget(self._quiet_hint)
        qi.addStretch(1)
        self.quiet_import.clicked.connect(self.openRequested.emit)
        return self.quiet_import

    # ---------------------------------------------------------------- 事件过滤（搜索框焦点环）
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        ):
            focused = event.type() == QEvent.Type.FocusIn
            self._search_box.setProperty("focused", "true" if focused else "false")
            _repolish(self._search_box)
        return bool(super().eventFilter(watched, event))

    # ---------------------------------------------------------------- 公共接口
    def set_entries(self, entries: list[LibraryEntry]) -> None:
        """换一批文档（主窗口在最近文件 / 收藏 / 元数据变化时调用）。"""
        self._entries = list(entries)
        if self.folder != ALL_FOLDERS and self.folder not in self.folders():
            self.folder = ALL_FOLDERS
        self._rebuild_collections()
        self._rebuild_chips()
        for label, _icon in SECTIONS:
            self.nav_items[label].set_count(len(filter_entries(self._entries, section=label)))
        self.continue_card.set_entry(self._entries[0] if self._entries else None)
        self._rebuild()

    def entries(self) -> list[LibraryEntry]:
        return list(self._entries)

    def entry_paths(self) -> list[str]:
        """当前（过滤后）显示的文档路径。"""
        return [card.entry.path for card in self._cards]

    def folders(self) -> list[str]:
        """按文档数排序的文件夹列表。"""
        return [name for name, _n in Counter(e.folder for e in self._entries).most_common()]

    def set_section(self, section: str) -> None:
        if section not in self.nav_items:
            return
        self.section = section
        self.folder = ALL_FOLDERS
        self.nav_items[section].setChecked(True)
        self._sync_folder_buttons()
        self._rebuild()

    def set_folder(self, folder: str) -> None:
        self.folder = folder if folder in self.folders() else ALL_FOLDERS
        self._sync_folder_buttons()
        self._rebuild()

    def reset_filters(self) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._sync_kbd_visibility()
        self.set_section(SECTIONS[0][0])

    def set_view_mode(self, mode: str, *, notify: bool = False) -> None:
        if mode not in self.view_buttons:
            return
        changed = mode != self.view_mode
        self.view_mode = mode
        self.view_buttons[mode].setChecked(True)
        self._refresh_view_icons()
        if changed:
            self._rebuild()
            if notify:
                self.viewModeChanged.emit(mode)

    def set_can_return(self, can: bool, tip: str = "", *, label: str = "返回阅读") -> None:
        """后台有打开的标签页时在顶栏显示「返回阅读」。"""
        self.return_button.setText(label)
        self.return_button.setVisible(can)
        self.return_button.setToolTip(tip or "回到正在阅读的文档")

    def set_recovery_notice(self, labels: Sequence[str]) -> None:
        """显示「崩溃前未保存的修改」提示；``labels`` 为空则隐藏。"""
        if not labels:
            self.hide_recovery_notice()
            return
        names = "、".join(labels[:3])
        if len(labels) > 3:
            names += f" 等 {len(labels)} 个"
        self._recovery_title.setText(f"发现 {len(labels)} 个未保存的修改")
        self._recovery_text.setText(f"{names} · 恢复后原文件会先备份，改动过的原文件另存副本")
        self._recovery_text.setToolTip(names)
        self._recovery_banner.setVisible(True)

    def hide_recovery_notice(self) -> None:
        self._recovery_banner.setVisible(False)

    @property
    def recovery_notice_visible(self) -> bool:
        return bool(self._recovery_banner.isVisibleTo(self))

    def set_dragging(self, dragging: bool) -> None:
        """拖入文件时高亮导入区与空态框。"""
        val = "true" if dragging else "false"
        self.quiet_import.setProperty("dragging", val)
        self._empty.setProperty("dragging", val)
        _repolish(self.quiet_import)
        _repolish(self._empty)

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def animate_in(self) -> None:
        """进入首页时仅作轻量短促淡入，克制不晃眼。"""
        widgets: list[QWidget] = [self.continue_card, *self._cards[:6]]
        for i, w in enumerate(widgets):
            if w.isVisibleTo(self):
                motion.fade_up(w, delay=min(i, 5) * 45)

    # ---------------------------------------------------------------- 内部：数据 → 视图
    def _on_search_changed(self, _text: str) -> None:
        self._sync_kbd_visibility()
        self._rebuild()

    def _sync_kbd_visibility(self) -> None:
        self._kbd.setVisible(self.width_class in ("xl", "lg") and not self.search.text())

    def _rebuild_collections(self) -> None:
        for item in self._collections:
            item.setParent(None)
            item.deleteLater()
        self._collections.clear()
        counts = Counter(e.folder for e in self._entries)
        for i, (name, n) in enumerate(counts.most_common(_MAX_COLLECTIONS)):
            item = _CollectionItem(name, _COLLECTION_DOTS[i % len(_COLLECTION_DOTS)], n)
            item.clicked.connect(lambda _c=False, f=name: self._on_collection(f))
            self._col_lay.addWidget(item)
            self._collections.append(item)
        self._sync_nav_visibility()

    def _rebuild_chips(self) -> None:
        for chip in self._chips:
            chip.setParent(None)
            chip.deleteLater()
        self._chips.clear()
        folders = self.folders()[:_MAX_FILTER_CHIPS]
        for i, name in enumerate([ALL_FOLDERS, *folders]):
            chip = QPushButton(f"{name} {len(self._entries)}" if name == ALL_FOLDERS else name)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(28)
            chip.setProperty("folder", name)
            chip.clicked.connect(lambda _c=False, f=name: self.set_folder(f))
            self._filter_lay.insertWidget(i, chip)
            self._chips.append(chip)
        self._filter_box.setVisible(len(folders) >= 2)
        self._sync_folder_buttons()

    def _sync_folder_buttons(self) -> None:
        for chip in self._chips:
            chip.setChecked(chip.property("folder") == self.folder)
        for item in self._collections:
            item.setChecked(item.label == self.folder)

    def _rebuild(self) -> None:
        query = self.search.text()
        entries = filter_entries(
            self._entries,
            section=self.section,
            folder=self.folder,
            query=query,
            sort=self.sort.currentText(),
        )
        self.heading.setText(self.section)
        self.heading_count.setText(str(len(entries)))

        is_annot_sec = self.section == "含批注文档"
        self.heading_desc.setText(
            "显示含 PDF 批注的文档；独立 Markdown 笔记可在阅读器的「工作台 → 伴随笔记」打开。"
            if is_annot_sec
            else ""
        )
        self.heading_desc.setVisible(is_annot_sec)

        # 仅在默认未过滤「全部文档」且有文献时展示顶部上次阅读横条
        unfiltered_home = (
            bool(self._entries)
            and self.section == SECTIONS[0][0]
            and self.folder == ALL_FOLDERS
            and not query.strip()
        )
        self.continue_card.setVisible(unfiltered_home)

        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        compact = self.view_mode == "list"
        self._cards = []
        for entry in entries:
            card = _BookCard(entry, compact)
            card.openRequested.connect(self.openRecentRequested.emit)
            card.favoriteToggled.connect(self.favoriteToggled.emit)
            self._cards.append(card)
        self._place_cards()

        has_entries = bool(entries)
        self._grid_host.setVisible(has_entries)
        self._empty.setVisible(not has_entries)
        self.quiet_import.setVisible(bool(self._entries))

        p = self._theme.palette
        if self._entries:
            self._empty_icon.setPixmap(render_pixmap("search", p.text_muted, 22))
            if is_annot_sec:
                filtered = bool(query.strip()) or self.folder != ALL_FOLDERS
                self.empty_title.setText("没有匹配的批注文档" if filtered else "还没有含批注的文档")
                self.empty_body.setText(
                    "这里只显示记录有 PDF 批注的文档，独立 Markdown 笔记不在此列表中。"
                    "可清除关键词或查看全部文档。"
                )
            else:
                self.empty_title.setText("这里还没有文档")
                self.empty_body.setText(
                    "未找到符合当前筛选条件的文献，可清除关键词或返回全部文档。"
                )
            self.empty_button.setText("查看全部文档")
        else:
            self._empty_icon.setPixmap(render_pixmap("doc", p.accent_text, 22))
            self.empty_title.setText("文献库还是空的")
            self.empty_body.setText(
                "导入 PDF / EPUB 或直接将文件拖入窗口，阅读进度与批注自动保存在本地。"
            )
            self.empty_button.setText("导入 PDF")

    def _place_cards(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        compact = self.view_mode == "list"
        columns = 1 if compact else self._columns
        self._grid.setVerticalSpacing(8 if compact else (10 if self.width_class == "xs" else 14))
        for c in range(4):
            self._grid.setColumnStretch(c, 1 if c < columns else 0)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // columns, i % columns)

    # ---------------------------------------------------------------- 响应式断点
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.apply_width(self.width())

    def apply_width(self, width: int) -> None:
        """按窗口宽度切换响应式断点（xl ≥1550 / lg >1100 / md >820 / sm >500 / xs ≤500）。"""
        if width >= 1550:
            cls = "xl"
        elif width > 1100:
            cls = "lg"
        elif width > 820:
            cls = "md"
        elif width > 500:
            cls = "sm"
        else:
            cls = "xs"
        if cls == self.width_class:
            return
        self.width_class = cls
        narrow_nav = cls in ("sm", "xs")

        self._nav.setFixedWidth({"xl": 212, "lg": 212, "md": 188}.get(cls, 64))
        nav_margins = {"md": (10, 14, 10, 14), "sm": (8, 14, 8, 12), "xs": (8, 14, 8, 12)}
        self._nav_lay.setContentsMargins(*nav_margins.get(cls, (14, 14, 14, 14)))
        self._brand_lay.setContentsMargins(0 if narrow_nav else 8, 0, 0 if narrow_nav else 8, 0)

        for w in (
            self._brand_text,
            self._lib_caption,
            self._nav_bottom,
            self._import_text,
            self._import_key,
        ):
            w.setVisible(not narrow_nav)
        self._compact_theme.setVisible(narrow_nav)

        if narrow_nav:
            self.import_button.setFixedWidth(40)
            self._brand_btn.setFixedWidth(40)
        else:
            self.import_button.setMinimumWidth(0)
            self.import_button.setMaximumWidth(16777215)
            self.import_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._brand_btn.setMinimumWidth(0)
            self._brand_btn.setMaximumWidth(16777215)

        align = Qt.AlignmentFlag.AlignHCenter if narrow_nav else Qt.AlignmentFlag(0)
        self._nav_lay.setAlignment(self._brand_btn, align)
        self._nav_lay.setAlignment(self.import_button, align)
        for item in self.nav_items.values():
            item.set_compact(narrow_nav)
            self._nav_lay.setAlignment(item, align)
        self._sync_nav_visibility()

        pad = {"xl": 32, "lg": 28, "md": 22, "sm": 18}.get(cls, 14)
        top = {"xl": 24, "lg": 20, "md": 18, "sm": 16}.get(cls, 14)
        self._content_lay.setContentsMargins(pad, top, pad, 24)
        self._topbar_lay.setContentsMargins(pad, 0, pad, 0)
        self._topbar.setFixedHeight(50 if cls == "xs" else 56)

        self.continue_card.set_scale(cls)
        self._sync_kbd_visibility()
        self._search_box.setMaximumWidth({"xl": 290, "lg": 260, "md": 200, "sm": 170}.get(cls, 140))
        self.sort.setVisible(not narrow_nav)
        self._quiet_hint.setVisible(not narrow_nav)

        # 标准桌面（lg）采用舒展的 3 列卡片，超宽屏 4 列，中等/平板 2 列，极窄单列
        self._columns = {"xl": 4, "lg": 3, "md": 2, "sm": 2}.get(cls, 1)
        gap = {"xl": 16, "lg": 14, "md": 12, "sm": 12}.get(cls, 10)
        self._grid.setHorizontalSpacing(gap)
        self._place_cards()

    def _sync_nav_visibility(self) -> None:
        show = bool(self._collections) and self.width_class not in ("sm", "xs")
        self._col_caption_box.setVisible(show)
        self._col_box.setVisible(show)

    # ---------------------------------------------------------------- 槽与主题刷新
    def _on_collection(self, folder: str) -> None:
        if self.section == SECTIONS[0][0] and self.folder == folder:
            self.set_folder(ALL_FOLDERS)
            return
        self.section = SECTIONS[0][0]
        self.nav_items[self.section].setChecked(True)
        self.set_folder(folder)

    def _on_continue(self) -> None:
        if self._entries:
            self.openRecentRequested.emit(self._entries[0].path)
        else:
            self.openRequested.emit()

    def _on_empty_action(self) -> None:
        if self._entries:
            self.reset_filters()
        else:
            self.openRequested.emit()

    def _refresh_view_icons(self) -> None:
        p = self._theme.palette
        for mode, btn in self.view_buttons.items():
            color = p.accent_text if btn.isChecked() else p.text_muted
            btn.setIcon(QIcon(render_pixmap(mode, color, 15)))

    def _on_theme_changed(self) -> None:
        p = self._theme.palette
        mode_icon = "sun" if self._theme.is_dark else "moon"
        self._brand_text.setText(f'lumen<span style="color:{p.accent};">.</span>')
        self._brand_mark.update()
        self._import_icon.setPixmap(render_pixmap("plus", p.on_accent, 15))
        self.profile.setIcon(QIcon(render_pixmap(mode_icon, p.nav_text, 15)))
        self.profile.setText("浅色模式" if self._theme.is_dark else "深色模式")
        self._compact_theme.setIcon(QIcon(render_pixmap(mode_icon, p.nav_text, 16)))
        self.return_button.setIcon(QIcon(render_pixmap("back", p.accent_text, 14)))
        self._search_icon.setPixmap(render_pixmap("search", p.text_muted, 15))
        self._refresh_view_icons()
        self._quiet_icon.setPixmap(render_pixmap("plus", p.accent_text, 14))
        self._empty_icon.setPixmap(
            render_pixmap(
                "search" if self._entries else "doc",
                p.text_muted if self._entries else p.accent_text,
                22,
            )
        )
        for item in self.nav_items.values():
            item.refresh()
        for col in self._collections:
            col.refresh()
        if hasattr(self, "continue_card"):
            self.continue_card.refresh()
        for card in self._cards:
            card.update()
        self.update()
