"""文献库首页（Lumen 第二版设计稿 ``UI/src/lumen/Studio.tsx``）。

没有打开文档、或在阅读时点标题栏的「l. lumen.」/ 网格按钮时显示，占满整个窗口::

    ┌ 墨绿导航栏 228 ┐┌ 工作空间 › 全部文档 ··········· [返回阅读] ● 已在本地就绪 | ☾ (L) ┐
    │ lumen.         ││ A LITTLE FOCUS…   让每一次阅读，都有回响。              ☼ 今天… │
    │ 我的阅读空间   ││ ┌ 从上次的思考继续 ──────────── 纸张 ┐┌ AI 助读 ───┐           │
    │ [+ 导入 PDF]   ││ └──────────────────────────────────┘└────────────┘           │
    │ 文献库 ×4      ││ 我的文献库 04         [搜索文档… Ctrl K] [最近阅读▾] [▦ ☰]     │
    │ 我的合集       ││ 全部 4 · 文件夹 …                                 为专注而整理 │
    │ 隐私卡 / 主题  ││ ▢ ▢ ▢ ▢  （封面卡片，悬停上浮）                               │
    └────────────────┘│ ┄┄ 为你的下一次灵感，留一个位置 · 导入 PDF ┄┄      页脚        │

数据全部来自真实设置（不放演示数据）：

* 全部文档 = 最近文件；最近阅读 = 读过（有阅读进度）的；我的收藏 = 收藏列表；
  含批注文档 = 记录有 PDF 批注的文档（不是独立 Markdown 笔记列表）
* 「我的合集」与筛选标签 = 文档所在文件夹（本地优先：不另造一套标签体系）
* 封面按路径稳定地取一种矿物色（sage / sand / rose / blue），标题取 PDF 元数据或文件名

设计稿字号是按网页缩略尺寸标的（5–12px），桌面端整体放大到 ≥10px 并保持比例。
响应式断点与设计稿一致：≥1550 宽松；≤1100 导航收窄、隐藏问候侧注；≤820 导航只留图标、
隐藏 AI 卡；≤500 单列。
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetricsF,
    QHideEvent,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
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
from pdfreader.gui.theme.manager import serif_font
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
    ("含批注文档", "notes"),
)
ALL_FOLDERS = "全部"
SORTS = ("最近阅读", "名称排序")
_MAX_COLLECTIONS = 3
_MAX_FILTER_CHIPS = 6
_COLLECTION_DOTS = ("#A4B098", "#C79C79", "#8EABB4")
_CONTENT_MAX = 1420
# 按钮图标放右侧用了 RightToLeft，布局对齐需按绝对方向，否则「左对齐」会被镜像到右边
_ABS_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute


@dataclass(frozen=True)
class LibraryEntry:
    """文献库里的一份文档（由主窗口据设置组装）。"""

    path: str
    title: str  # 封面标题（元数据标题或文件名）
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
    """封面标题：PDF 元数据标题可用就用它，否则用文件名（去掉扩展名）。

    很多 PDF 的标题是生成工具塞进去的文件名（``Microsoft Word - 报告.docx``、``untitled``），
    这类一律视为没有标题。
    """
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
    """按路径稳定地选一种封面色（同一文件每次都一样）。"""
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


def _font(px: int, *, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    font = QFont()
    font.setPixelSize(px)
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
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


def _two_line(top: str, bottom: str, top_name: str, bottom_name: str, gap: int = 3) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(gap)
    lay.addWidget(_label(top, top_name))
    second = _label(bottom, bottom_name)
    second.setWordWrap(True)
    lay.addWidget(second)
    _passthrough(box)
    return box


# ============================================================================ 导航栏部件
class _BrandMark(QWidget):
    """品牌标记：三根斜切的竖条（高 18 / 27 / 22，skewY −15°）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(26, 34)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(0, 5)
        p.shear(0, -0.27)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#D7DDC7"))
        for i, h in enumerate((18, 27, 22)):
            p.drawRoundedRect(QRectF(i * 9, 27 - h + 4, 5, h), 1, 1)
        p.end()


class _NavItem(QPushButton):
    """导航项：图标 + 名称 + 右侧两位计数（窄屏只留图标）。"""

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
        lay.setContentsMargins(13, 0, 13, 0)
        lay.setSpacing(12)
        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        self._text = _label(label, "NavItemText")
        self.count = _label("00", "NavItemCount")
        _passthrough(self._icon, self._text, self.count)
        lay.addWidget(self._icon)
        lay.addWidget(self._text, stretch=1)
        lay.addWidget(self.count)
        self.setFixedHeight(42)
        self.toggled.connect(lambda _c: self.refresh())

    def set_count(self, n: int) -> None:
        self.count.setText(f"{n:02d}")

    def set_compact(self, compact: bool) -> None:
        self._text.setVisible(not compact)
        self.count.setVisible(not compact)
        if compact:
            self.setFixedWidth(44)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)

    def refresh(self) -> None:
        p = get_theme().palette
        color = p.nav_icon_active if self.isChecked() else p.nav_text
        self._icon.setPixmap(render_pixmap(self.icon_name, color, 18))


class _CollectionItem(QPushButton):
    """「我的合集」一项：色点 + 文件夹名 + 悬停 / 选中才出现的 ›。"""

    def __init__(self, label: str, dot: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollectionItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self.label = label
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)
        dot_lbl = QLabel()
        dot_lbl.setFixedSize(7, 7)
        dot_lbl.setStyleSheet(f"background-color:{dot}; border-radius:2px;")
        self._text = _label(label, "CollectionText")
        self._arrow = QLabel()
        self._arrow.setFixedSize(12, 12)
        _passthrough(dot_lbl, self._text, self._arrow)
        lay.addWidget(dot_lbl)
        lay.addWidget(self._text, stretch=1)
        lay.addWidget(self._arrow)
        self.setFixedHeight(36)
        self._hover = False
        self.toggled.connect(lambda _c: self.refresh())
        self.refresh()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._hover = True
        self.refresh()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover = False
        self.refresh()

    def refresh(self) -> None:
        if self._hover or self.isChecked():
            color = _with_alpha(get_theme().palette.nav_text, 0.6)
            self._arrow.setPixmap(
                render_pixmap("forward", color.name(QColor.NameFormat.HexArgb), 12)
            )
        else:
            self._arrow.clear()


# ============================================================================ 封面与卡片
class _Cover(QAbstractButton):
    """自绘封面：矿物色底 + 字距小标题 + 衬线书名 + 同心几何纹 + 作者；悬停浮出「→」。"""

    def __init__(self, entry: LibraryEntry, compact: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.compact = compact
        self.variant = cover_variant(entry.path)
        self.hover = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        name = Path(entry.path).name
        self.setToolTip(f"打开 {name}")
        self.setAccessibleName(f"打开 {name}")
        if compact:
            self.setFixedSize(110, 80)
        else:
            self.setFixedHeight(177)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(110, 80) if self.compact else QSize(200, self.height())

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        motion.tween(self, self.hover, 1.0, 300, self._set_hover)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        motion.tween(self, self.hover, 0.0, 300, self._set_hover)

    def _set_hover(self, v: float) -> None:
        self.hover = v
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        bg, ink = get_theme().palette.covers[self.variant]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 4, 4)
        p.setClipPath(clip)
        p.fillRect(r, QColor(bg))
        self._paint_art(p, r, ink)

        small = self.compact or r.height() < 160
        if self.compact:
            pad_x, pad_top = 10.0, 10.0
        else:
            pad_x, pad_top = (12.0, 16.0) if small else (17.0, 20.0)
        inner_w = r.width() - 2 * pad_x
        # 小标题：文件夹名，大写 + 字距
        p.setPen(_with_alpha(ink, 0.7))
        p.setFont(_font(7 if self.compact else 8, spacing=0.6 if small else 1.3))
        overline = p.fontMetrics().elidedText(
            self.entry.folder.upper() or "PDF", Qt.TextElideMode.ElideRight, int(inner_w)
        )
        p.drawText(QRectF(pad_x, pad_top, inner_w, 12), Qt.AlignmentFlag.AlignLeft, overline)
        # 衬线书名
        top = pad_top + (14 if self.compact else 26)
        bottom = 8 if self.compact else 34
        title_rect = QRectF(pad_x, top, inner_w - 4, r.height() - top - bottom)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        flags |= int(Qt.TextFlag.TextWordWrap)
        p.setFont(self._fit_title(title_rect, flags, 13 if self.compact else (18 if small else 21)))
        p.setPen(QColor(ink))
        p.drawText(title_rect, flags, self.entry.title)
        if not self.compact:
            author = self.entry.author or (f"{self.entry.pages} 页" if self.entry.pages else "")
            p.setFont(_font(10))
            p.drawText(
                QRectF(pad_x, r.height() - 26, inner_w - 40, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                p.fontMetrics().elidedText(author, Qt.TextElideMode.ElideRight, int(inner_w) - 40),
            )
        # 悬停：右下角从左滑入的打开箭头
        if self.hover > 0.01 and not self.compact:
            t = self.hover
            p.setOpacity(t)
            cx = r.width() - 12 - 16 - 8 * (1 - t)
            cy = r.height() - 12 - 16
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 168))
            p.drawEllipse(QPointF(cx, cy), 16, 16)
            p.drawPixmap(int(cx - 10), int(cy - 10), render_pixmap("forward", ink, 20))
            p.setOpacity(1.0)
        p.end()

    def _fit_title(self, rect: QRectF, flags: int, px: int) -> QFont:
        """衬线书名从设计字号起逐级缩小，直到最长的词放得下、整段不超出封面。"""
        words = self.entry.title.split() or [self.entry.title]
        font = serif_font(px)
        while px > 11:
            font = serif_font(px)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.4)
            fm = QFontMetricsF(font)
            widest = max(fm.horizontalAdvance(w) for w in words)
            used = fm.boundingRect(rect, flags, self.entry.title)
            if widest <= rect.width() and used.height() <= rect.height():
                break
            px -= 1
        return font

    def _paint_art(self, p: QPainter, r: QRectF, ink: str) -> None:
        """同心几何纹（透明度 .2）：sage 椭圆 / sand 方框 / rose 菱形 / blue 圆角方。"""
        p.save()
        p.setOpacity(0.2)
        p.setPen(QPen(QColor(ink), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        k = 0.6 if self.compact else 1.0
        w, h, right, bottom, angle = {
            0: (180.0, 130.0, -35.0, -39.0, -30.0),
            1: (180.0, 130.0, -35.0, -40.0, -25.0),
            2: (140.0, 140.0, -45.0, -30.0, 0.0),
            3: (180.0, 130.0, -30.0, -60.0, 45.0),
        }[self.variant]
        w, h, right, bottom = w * k, h * k, right * k, bottom * k
        p.translate(r.width() - right - w / 2, r.height() - bottom - h / 2)
        p.rotate(angle)
        for i in range(5):
            inset = i * 10 * k
            rect = QRectF(-w / 2 + inset, -h / 2 + inset, w - 2 * inset, h - 2 * inset)
            if self.variant == 0:
                p.drawEllipse(rect)
            elif self.variant == 1:
                p.drawRect(rect)
            elif self.variant == 2:
                p.save()
                p.translate(rect.center())
                p.rotate(45)
                p.drawRect(
                    QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
                )
                p.restore()
            else:
                radius = min(rect.width(), rect.height()) * 0.3
                p.drawRoundedRect(rect, radius, radius)
        p.restore()


class _Star(QToolButton):
    """封面右上角的收藏书签（收藏后实心、常亮；未收藏时悬停才看清）。"""

    def __init__(self, ink: str, starred: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BookStar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(24, 24)
        self.ink = ink
        self.starred = starred
        self._hover = False
        tip = "取消收藏" if starred else "收藏文档"
        self.setToolTip(tip)
        self.setAccessibleName(tip)

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(1.0 if (self.starred or self._hover) else 0.35)
        ox, oy = (self.width() - 14) / 2, (self.height() - 14) / 2
        if self.starred:
            s = 14 / 24
            path = QPainterPath()
            pts = ((6, 3), (18, 3), (18, 21), (12, 17), (6, 21))
            path.moveTo(QPointF(ox + pts[0][0] * s, oy + pts[0][1] * s))
            for x, y in pts[1:]:
                path.lineTo(QPointF(ox + x * s, oy + y * s))
            path.closeSubpath()
            p.fillPath(path, _with_alpha(self.ink, 0.15))
        p.drawPixmap(int(ox), int(oy), render_pixmap("bookmark", self.ink, 14))
        p.end()


class _ProgressLine(QWidget):
    """卡片底部 2px 阅读进度（封面墨色，45% 不透明）。"""

    def __init__(self, value: float, ink: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = max(0.0, min(1.0, value))
        self._ink = ink
        self.setFixedHeight(2)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(get_theme().palette.surface_hover))
        if self.value > 0:
            p.fillRect(QRectF(0, 0, self.width() * self.value, 2), _with_alpha(self._ink, 0.45))
        p.end()


class _BookCard(QFrame):
    """一张文档卡片（网格：封面在上；列表：封面在左）。悬停时上浮 5px 并投出柔和阴影。"""

    openRequested = Signal(str)
    favoriteToggled = Signal(str)

    def __init__(self, entry: LibraryEntry, compact: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BookCardHolder")
        self.entry = entry
        self.compact = compact
        _bg, ink = get_theme().palette.covers[cover_variant(entry.path)]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 5, 0, 0)
        outer.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName("BookCard")
        outer.addWidget(self.card)

        self.cover = _Cover(entry, compact)
        self.star = _Star(ink, entry.favorite, self.cover)
        self.title_btn = QPushButton(entry.name)
        self.title_btn.setObjectName("BookTitle")
        self.title_btn.setProperty("compact", "true" if compact else "false")
        self.title_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_btn.setToolTip(entry.path)
        self.title_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        tag = _label(entry.folder, "BookTag")
        tag.setToolTip(str(Path(entry.path).parent))
        tag.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        pdf = _label("PDF", "BookTagPdf")
        info_text = f"{entry.pages} 页" if entry.pages else "PDF 文档"
        if entry.annotations:
            info_text += f" · {entry.annotations} 条批注"
        self.info = _label(info_text, "BookInfo")
        self.when = _label(
            "正在阅读" if entry.is_open else relative_time(entry.opened_at), "BookInfo"
        )
        self.progress = _ProgressLine(entry.progress, ink)

        details = QVBoxLayout()
        details.setSpacing(0)
        tag_row = QHBoxLayout()
        tag_row.setSpacing(8)
        tag_row.addWidget(tag, stretch=1)
        tag_row.addWidget(pdf)
        details.addLayout(tag_row)
        details.addSpacing(6)
        details.addWidget(self.title_btn)
        details.addSpacing(8)
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        info_row.addWidget(self.info, stretch=1)
        info_row.addWidget(self.when)
        details.addLayout(info_row)
        details.addSpacing(11)
        details.addWidget(self.progress)

        lay: QBoxLayout
        if compact:
            lay = QHBoxLayout(self.card)
            lay.setContentsMargins(8, 8, 18, 8)
            lay.setSpacing(22)
            lay.addWidget(self.cover)
            lay.addLayout(details, stretch=1)
        else:
            lay = QVBoxLayout(self.card)
            lay.setContentsMargins(9, 9, 9, 0)
            lay.setSpacing(0)
            lay.addWidget(self.cover)
            box = QVBoxLayout()
            box.setContentsMargins(4, 12, 4, 14)
            box.addLayout(details)
            lay.addLayout(box)
        self._lift = 0.0
        self.cover.clicked.connect(lambda: self.openRequested.emit(entry.path))
        self.title_btn.clicked.connect(lambda: self.openRequested.emit(entry.path))
        self.star.clicked.connect(lambda: self.favoriteToggled.emit(entry.path))

    def set_cover_height(self, h: int) -> None:
        if not self.compact:
            self.cover.setFixedHeight(h)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_star()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._place_star()

    def _place_star(self) -> None:
        inset = 8 if self.compact else 10
        self.star.move(self.cover.width() - self.star.width() - inset, inset)
        self.star.raise_()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._set_shadow(True)
        motion.tween(self, self._lift, 1.0, 400, self._apply_lift)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        motion.tween(self, self._lift, 0.0, 400, self._apply_lift)
        self._set_shadow(False)

    def _apply_lift(self, v: float) -> None:
        self._lift = v
        lay = self.layout()
        if lay is not None:
            dy = round(5 * v)
            lay.setContentsMargins(0, 5 - dy, 0, dy)

    def _set_shadow(self, on: bool) -> None:
        if on and motion.enabled():
            effect = QGraphicsDropShadowEffect(self.card)
            effect.setBlurRadius(45)
            effect.setOffset(0, 16)
            effect.setColor(QColor(get_theme().palette.soft_shadow))
            self.card.setGraphicsEffect(effect)
        else:
            self.card.setGraphicsEffect(None)


# ============================================================================ 精选卡片
class _HeroProgress(QWidget):
    """「继续阅读」卡上的 112×3 进度条。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = 0.0
        self.setFixedSize(112, 3)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(90, 112, 86, 34))
        p.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        if self.value > 0:
            p.setBrush(QColor("#718066"))
            p.drawRoundedRect(QRectF(0, 0, max(3.0, self.width() * self.value), 3), 1.5, 1.5)
        p.end()


class _PaperScene:
    """「继续阅读」卡片右侧的纸张场景（由卡片的 paintEvent 绘制，随卡片圆角裁剪）。"""

    def __init__(self) -> None:
        self.hover = 0.0
        self.float_phase = 0.0
        self.title = ""
        self.year = ""
        self.paper = QSize(171, 232)
        self.right = 24.0
        self.opacity = 1.0

    def paint(self, p: QPainter, area: QRectF) -> None:
        p.save()
        p.setOpacity(self.opacity)
        w_paper, h_paper = float(self.paper.width()), float(self.paper.height())
        # 轨道
        p.setPen(QPen(QColor(117, 131, 102, 28), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for size, r_off, top in ((330.0, 90.0, 10.0), (430.0, 140.0, -40.0)):
            p.drawEllipse(QRectF(area.right() + r_off - size, area.top() + top, size, size))
        t = self.hover
        back = QRectF(area.right() - (self.right + 9) - w_paper, area.top() + 47, w_paper, h_paper)
        front = QRectF(area.right() - self.right - w_paper, area.top() + 38, w_paper, h_paper)
        self._paper(p, back, -5 - 4 * t, QPointF(-7 * t, 0), QColor("#C5CCBA"), content=False)
        self._paper(p, front, 9 - 5 * t, QPointF(0, -6 * t), QColor("#FFFEF7"), content=True)
        self._note(p, area)
        p.restore()

    def _note(self, p: QPainter, area: QRectF) -> None:
        """浮动便签「好的想法，值得留下。」：7s 一个周期上下浮动 6px。"""
        dy = -6 * math.sin(math.pi * self.float_phase) ** 2
        note_w, note_h = 196.0, 40.0
        p.save()
        p.translate(area.right() - 9 - note_w / 2, area.bottom() - 30 - note_h / 2 + dy)
        p.rotate(-3)
        rect = QRectF(-note_w / 2, -note_h / 2, note_w, note_h)
        p.setPen(QPen(QColor(255, 255, 255, 153), 1))
        p.setBrush(QColor(250, 250, 240, 232))
        p.drawRoundedRect(rect, 7, 7)
        icon = render_pixmap("highlight", "#667056", 15)
        p.drawPixmap(int(rect.left() + 13), int(rect.center().y() - 7), icon)
        p.setPen(QColor("#667056"))
        p.setFont(_font(11))
        p.drawText(
            rect.adjusted(36, 0, -8, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "好的想法，值得留下。",
        )
        p.restore()

    def _paper(
        self,
        p: QPainter,
        rect: QRectF,
        angle: float,
        offset: QPointF,
        color: QColor,
        *,
        content: bool,
    ) -> None:
        p.save()
        p.translate(rect.center() + offset)
        p.rotate(angle)
        local = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
        p.setPen(Qt.PenStyle.NoPen)
        for i, alpha in enumerate((12, 8, 5)):  # 0 10px 28px 的柔和投影
            p.setBrush(QColor(52, 64, 50, alpha))
            p.drawRoundedRect(
                local.translated(0, 6 + i * 3).adjusted(-i * 2, -i, i * 2, i * 2), 3, 3
            )
        p.setBrush(color)
        p.drawRoundedRect(local, 2, 2)
        if content:
            self._paper_content(p, local)
        p.restore()

    def _paper_content(self, p: QPainter, local: QRectF) -> None:
        ink = QColor("#404840")
        muted = _with_alpha("#404840", 0.6)
        x, y = local.left() + 17, local.top() + 17
        width = local.width() - 34
        p.setPen(muted)
        p.setFont(_font(6, spacing=0.7))
        p.drawText(QRectF(x, y, width, 8), Qt.AlignmentFlag.AlignLeft, "RESEARCH PAPER")
        p.drawText(QRectF(x, y, width, 8), Qt.AlignmentFlag.AlignRight, self.year)
        y += 22
        title_font = serif_font(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        p.setFont(title_font)
        p.setPen(ink)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        p.drawText(QRectF(x, y, width, 38), flags | int(Qt.TextFlag.TextWordWrap), self.title)
        y += 44
        p.fillRect(QRectF(x, y, width, 1), QColor(84, 103, 71, 119))
        y += 8
        p.setPen(muted)
        p.setFont(_font(6))
        byline = "A quiet place for careful reading"
        p.drawText(QRectF(x, y, width, 8), Qt.AlignmentFlag.AlignLeft, byline)
        y += 14
        p.setFont(_font(6, weight=QFont.Weight.DemiBold, spacing=0.5))
        p.setPen(ink)
        p.drawText(QRectF(x, y, width, 8), Qt.AlignmentFlag.AlignLeft, "ABSTRACT")
        y = self._lines(p, x, y + 11, width, 4)
        # 注意力矩阵（skewX −12°）
        grid_w = 63.0
        cell = (grid_w - 12) / 5
        p.save()
        p.translate(x + (width - grid_w) / 2, y + 8)
        p.shear(-0.21, 0)
        for i in range(25):
            filled = i % 6 == 0 or i in (11, 17)
            p.setPen(QPen(QColor("#CBD2C0"), 1))
            p.setBrush(QColor("#86997C") if filled else QColor("#E9EADE"))
            p.drawRect(QRectF((i % 5) * (cell + 3), (i // 5) * 10, cell, 7))
        p.restore()
        if y + 79 < local.bottom() - 18:
            self._lines(p, x, y + 64, width, 3)
        p.setPen(QColor("#93998C"))
        p.setFont(_font(6))
        p.drawText(
            QRectF(local.left(), local.bottom() - 16, local.width(), 8),
            Qt.AlignmentFlag.AlignHCenter,
            "01",
        )

    @staticmethod
    def _lines(p: QPainter, x: float, y: float, width: float, n: int) -> float:
        for i in range(n):
            p.fillRect(QRectF(x, y, width * (0.72 if i == n - 1 else 1.0), 2), QColor("#C7C9BC"))
            y += 5
        return y


class _ContinueCard(QFrame):
    """「从上次的思考继续」：最近一份文档的书名 / 作者 / 进度 + 继续阅读；右侧纸张场景。"""

    continueRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContinueCard")
        self.setMinimumHeight(297)
        self.scene = _PaperScene()
        self._copy = QWidget(self)
        self._copy.setObjectName("ContinueCopy")
        lay = QVBoxLayout(self._copy)
        self._copy_lay = lay
        lay.setContentsMargins(30, 25, 30, 25)
        lay.setSpacing(0)
        self.kicker = _label("", "SectionKicker")
        self.kicker.setTextFormat(Qt.TextFormat.RichText)
        self.title = _label("", "ContinueTitle")
        self.title.setWordWrap(True)
        self.subtitle = _label("", "ContinueSubtitle")
        self.subtitle.setWordWrap(True)
        self.meta_left = _label("", "ContinueMeta")
        self.meta_right = _label("", "ContinueMeta")
        meta = QHBoxLayout()
        meta.setSpacing(14)
        meta.addWidget(self.meta_left)
        meta.addWidget(self.meta_right)
        meta.addStretch(1)
        self.bar = _HeroProgress()
        self.read_label = _label("", "ContinueRead")
        prog = QHBoxLayout()
        prog.setSpacing(10)
        prog.addWidget(self.bar)
        prog.addWidget(self.read_label)
        prog.addStretch(1)
        self.button = QPushButton()
        self.button.setObjectName("ContinueButton")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.button.setIconSize(QSize(16, 16))
        self.button.setIcon(QIcon(render_pixmap("forward", "#F0F0E7", 16)))
        self.button.clicked.connect(self.continueRequested.emit)
        lay.addWidget(self.kicker)
        lay.addSpacing(20)
        lay.addWidget(self.title)
        lay.addSpacing(10)
        lay.addWidget(self.subtitle)
        lay.addSpacing(17)
        lay.addLayout(meta)
        lay.addSpacing(15)
        lay.addLayout(prog)
        lay.addSpacing(20)
        lay.addWidget(self.button, alignment=_ABS_LEFT)
        lay.addStretch(1)
        self._float: QVariantAnimation | None = None

    # ---------------------------------------------------------------- 内容
    def set_entry(self, entry: LibraryEntry | None) -> None:
        dot = '<span style="color:#869879;">●</span>&nbsp;&nbsp;'
        if entry is None:
            self.kicker.setText(f"{dot}从这里开始")
            self.title.setText("打开第一篇文献")
            self.subtitle.setText(
                "导入 PDF，或把文件拖放到窗口——阅读位置、书签与批注都只留在本机。"
            )
            self.meta_left.setText("")
            self.meta_right.setText("")
            self.bar.setVisible(False)
            self.read_label.setText("")
            self.button.setText("导入 PDF")
            self.scene.title = "Your next\nfavourite paper"
            self.scene.year = time.strftime("%Y")
        else:
            self.kicker.setText(f"{dot}从上次的思考继续")
            self.title.setText(entry.title)
            self.subtitle.setText(Path(entry.path).name)
            self.meta_left.setText(entry.author or entry.folder)
            parts = [f"{entry.pages} 页"] if entry.pages else []
            if entry.annotations:
                parts.append(f"{entry.annotations} 条批注")
            self.meta_right.setText(" · ".join(parts))
            self.bar.setVisible(True)
            self.bar.value = entry.progress
            self.bar.update()
            self.read_label.setText(f"已读 {round(entry.progress * 100)}%")
            self.button.setText("回到阅读" if entry.is_open else "继续阅读")
            self.scene.title = entry.title
            stamp = entry.opened_at if entry.opened_at > 0 else time.time()
            self.scene.year = time.strftime("%Y", time.localtime(stamp))
        self.update()

    def set_scale(self, cls: str) -> None:
        """按宽度档位（xl / lg / md / sm / xs）套用设计稿的断点尺寸。"""
        margins = {"xl": (36, 30), "lg": (30, 25), "md": (24, 24), "sm": (24, 24)}.get(
            cls, (18, 22)
        )
        self._copy_lay.setContentsMargins(margins[0], margins[1], margins[0], margins[1])
        self.setMinimumHeight({"xl": 320, "lg": 297, "md": 297, "sm": 290}.get(cls, 280))
        self.title.setProperty("size", cls)
        _repolish(self.title)
        paper = {"xl": (190, 255, 45.0), "md": (145, 220, -1.0), "sm": (170, 230, 10.0)}
        w, h, right = paper.get(cls, (171, 232, 24.0))
        self.scene.paper, self.scene.right = QSize(w, h), right
        self.scene.opacity = {"md": 0.75, "xs": 0.3}.get(cls, 1.0)
        self._layout_copy()
        self.update()

    # ---------------------------------------------------------------- 动效
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if motion.enabled() and self._float is None:
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(7000)
            anim.setLoopCount(-1)
            anim.valueChanged.connect(self._on_float)
            self._float = anim
        if self._float is not None:
            self._float.start()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        if self._float is not None:
            self._float.stop()

    def _on_float(self, v: object) -> None:
        self.scene.float_phase = float(v) if isinstance(v, (int, float)) else 0.0
        self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        motion.tween(self, self.scene.hover, 1.0, 700, self._on_hover)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        motion.tween(self, self.scene.hover, 0.0, 700, self._on_hover)

    def _on_hover(self, v: float) -> None:
        self.scene.hover = v
        self.update()

    # ---------------------------------------------------------------- 布局与绘制
    def _scene_rect(self) -> QRectF:
        w = self.width() * 0.46
        return QRectF(self.width() - w, 0, w, self.height())

    def _layout_copy(self) -> None:
        ratio = 0.8 if self.width() <= 420 else 0.53
        copy_w = min(self.width(), max(int(self.width() * ratio), 300))
        self._copy.setGeometry(0, 0, copy_w, self.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_copy()

    def sizeHint(self) -> QSize:
        return QSize(640, max(self.minimumHeight(), self._copy.sizeHint().height()))

    def minimumSizeHint(self) -> QSize:
        return QSize(300, max(self.minimumHeight(), self._copy.minimumSizeHint().height()))

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        p.fillRect(r, QColor(get_theme().palette.hero))
        if self.width() >= 420:
            self.scene.paint(p, self._scene_rect())
        p.end()


class _InsightCard(QFrame):
    """AI 助读介绍卡：图标 → 小标题 → 衬线标题 → 说明 → 「探索 AI 助读 →」→ 脚注；右侧淡淡的大火花。"""

    exploreRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InsightCard")
        self.setMinimumWidth(260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(27, 24, 27, 19)
        lay.setSpacing(0)
        self._lay = lay
        self.icon = QLabel()
        self.icon.setFixedSize(24, 24)
        kicker = _label("YOUR READING COMPANION", "InsightKicker")
        self.title = _label("不止读懂，\n更进一步。", "InsightTitle")
        body = _label("让 AI 帮你梳理脉络、解释难点，\n为每一个好问题找到起点。", "InsightBody")
        body.setWordWrap(True)
        self.button = QPushButton("探索 AI 助读")
        self.button.setObjectName("InsightButton")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.button.setIconSize(QSize(16, 16))
        self.button.clicked.connect(self.exploreRequested.emit)
        foot = _label("·&nbsp;&nbsp;仅在你主动提问时使用 AI", "InsightFootnote")
        foot.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.icon)
        lay.addSpacing(19)
        lay.addWidget(kicker)
        lay.addSpacing(12)
        lay.addWidget(self.title)
        lay.addSpacing(10)
        lay.addWidget(body)
        lay.addSpacing(17)
        lay.addWidget(self.button, alignment=_ABS_LEFT)
        lay.addSpacing(18)
        lay.addWidget(foot)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        p = get_theme().palette
        self.icon.setPixmap(render_pixmap("ai", "#A47852", 24))
        self.button.setIcon(QIcon(render_pixmap("forward", p.insight_ink, 16)))

    def set_compact(self, compact: bool) -> None:
        m = (21, 24, 21, 18) if compact else (27, 24, 27, 19)
        self._lay.setContentsMargins(*m)
        self.setMinimumWidth(220 if compact else 260)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        p.fillRect(r, QColor(get_theme().palette.insight))
        # 火花：right −12 / top 72 / rotate −15° / 透明度 .24
        p.setOpacity(0.24)
        p.translate(r.right() + 12 - 70, 72 + 70)
        p.rotate(-15)
        p.drawPixmap(-70, -70, render_pixmap("ai", "#C6B08A", 140, stroke=0.7))
        p.end()


# ============================================================================ 首页
class LibraryHome(QWidget):
    """文献库首页（导航 + 顶栏 + 问候 + 精选 + 文献网格 + 导入 + 页脚）。"""

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
        self._columns = 4
        self._cover_h = 177
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

    # ---------------------------------------------------------------- 构建：导航
    def _build_nav(self) -> QWidget:
        # 导航的固定间距、合集及底部信息会超过短窗口高度。让导航单独滚动，
        # 不把整窗撑高，也不带走右侧常驻的返回阅读和恢复操作。
        scroll = QScrollArea()
        scroll.setObjectName("StudioNavScroll")
        scroll.setAccessibleName("文献库导航")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setFixedWidth(228)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._nav = scroll
        nav = QFrame()
        nav.setObjectName("StudioNav")
        scroll.setWidget(nav)
        lay = QVBoxLayout(nav)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._nav_lay = lay
        lay.setContentsMargins(20, 33, 20, 18)
        lay.setSpacing(0)

        brand = QHBoxLayout()
        self._brand_lay = brand
        brand.setContentsMargins(12, 0, 0, 0)
        brand.setSpacing(12)
        brand.addWidget(_BrandMark())
        self._brand_text = _label('lumen<span style="color:#D99A75;">.</span>', "StudioBrand")
        self._brand_text.setTextFormat(Qt.TextFormat.RichText)
        brand.addWidget(self._brand_text)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(40)

        self._ws_label = _label("PERSONAL WORKSPACE", "NavCaption")
        self._ws_label.setWordWrap(True)
        lay.addWidget(self._ws_label)
        lay.addSpacing(14)
        self._ws_switch = QPushButton()
        self._ws_switch.setObjectName("WorkspaceSwitch")
        self._ws_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ws_switch.setToolTip("回到全部文档")
        ws = QHBoxLayout(self._ws_switch)
        ws.setContentsMargins(10, 0, 10, 0)
        ws.setSpacing(10)
        avatar = _label("L", "WorkspaceAvatar")
        avatar.setFixedSize(32, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ws_chevron = QLabel()
        _passthrough(avatar, self._ws_chevron)
        ws.addWidget(avatar)
        ws.addWidget(_two_line("我的阅读空间", "Personal space", "NavStrong", "NavSmall"), 1)
        ws.addWidget(self._ws_chevron)
        self._ws_switch.setFixedHeight(40)
        self._ws_switch.clicked.connect(self.reset_filters)
        lay.addWidget(self._ws_switch)
        lay.addSpacing(23)

        self.import_button = QPushButton()
        self.import_button.setObjectName("ImportButton")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.setToolTip("导入 PDF  (Ctrl+O)")
        self.import_button.setAccessibleName("导入 PDF")
        self.import_button.setFixedHeight(41)
        imp = QHBoxLayout(self.import_button)
        imp.setContentsMargins(13, 0, 13, 0)
        imp.setSpacing(9)
        import_icon = QLabel()
        import_icon.setPixmap(render_pixmap("plus", "#FFF6ED", 18))
        self._import_text = _label("导入 PDF", "ImportText")
        self._import_key = _label("Ctrl O", "ImportKey")
        _passthrough(import_icon, self._import_text, self._import_key)
        imp.addWidget(import_icon)
        imp.addWidget(self._import_text, 1)
        imp.addWidget(self._import_key)
        self.import_button.clicked.connect(self.openRequested.emit)
        lay.addWidget(self.import_button)
        lay.addSpacing(29)

        self._lib_caption = _label("文献库", "NavCaption")
        lay.addWidget(self._lib_caption)
        lay.addSpacing(13)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self.nav_items: dict[str, _NavItem] = {}
        for label, icon in SECTIONS:
            item = _NavItem(label, icon)
            self._nav_group.addButton(item)
            item.clicked.connect(lambda _c=False, s=label: self.set_section(s))
            self.nav_items[label] = item
            lay.addWidget(item)
            lay.addSpacing(5)
        self.nav_items[self.section].setChecked(True)

        self._col_caption_box = QWidget()
        cap = QHBoxLayout(self._col_caption_box)
        cap.setContentsMargins(0, 0, 0, 0)
        cap.addWidget(_label("我的合集", "NavCaption"))
        cap.addStretch(1)
        self._col_count = _label("00", "NavCaption")
        cap.addWidget(self._col_count)
        lay.addSpacing(24)
        lay.addWidget(self._col_caption_box)
        lay.addSpacing(13)
        self._col_box = QWidget()
        self._col_lay = QVBoxLayout(self._col_box)
        self._col_lay.setContentsMargins(0, 0, 0, 0)
        self._col_lay.setSpacing(0)
        lay.addWidget(self._col_box)
        lay.addStretch(1)

        self._nav_bottom = QWidget()
        bottom = QVBoxLayout(self._nav_bottom)
        bottom.setContentsMargins(0, 35, 0, 0)
        bottom.setSpacing(0)
        privacy = QFrame()
        privacy.setObjectName("PrivacyCard")
        pv = QHBoxLayout(privacy)
        pv.setContentsMargins(11, 14, 11, 14)
        pv.setSpacing(9)
        self._privacy_icon = QLabel()
        self._privacy_icon.setFixedSize(18, 18)
        pv.addWidget(self._privacy_icon, alignment=Qt.AlignmentFlag.AlignTop)
        pv.addWidget(
            _two_line(
                "只属于你的思考空间", "文档在本地，灵感无边界。", "PrivacyTitle", "PrivacyBody", 7
            ),
            1,
        )
        bottom.addWidget(privacy)
        bottom.addSpacing(22)
        rule = QFrame()
        rule.setObjectName("NavRule")
        rule.setFixedHeight(1)
        bottom.addWidget(rule)
        self.profile = QPushButton()
        self.profile.setObjectName("ProfileRow")
        self.profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.profile.setToolTip("切换主题")
        prof = QHBoxLayout(self.profile)
        prof.setContentsMargins(3, 20, 3, 0)
        prof.setSpacing(10)
        pav = _label("L", "ProfileAvatar")
        pav.setFixedSize(31, 31)
        pav.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._profile_theme = QLabel()
        _passthrough(pav, self._profile_theme)
        prof.addWidget(pav)
        prof.addWidget(_two_line("独立思考者", "本地工作空间", "NavStrong", "NavSmall"), 1)
        prof.addWidget(self._profile_theme)
        self.profile.setFixedHeight(51)
        self.profile.clicked.connect(self.themeToggleRequested.emit)
        bottom.addWidget(self.profile)
        lay.addWidget(self._nav_bottom)

        self._compact_theme = QToolButton()
        self._compact_theme.setObjectName("NavIconButton")
        self._compact_theme.setToolTip("切换主题")
        self._compact_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compact_theme.setIconSize(QSize(18, 18))
        self._compact_theme.clicked.connect(self.themeToggleRequested.emit)
        self._compact_theme.setVisible(False)
        lay.addWidget(self._compact_theme, alignment=Qt.AlignmentFlag.AlignHCenter)
        return scroll

    # ---------------------------------------------------------------- 构建：顶栏
    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("StudioTopbar")
        bar.setFixedHeight(70)
        self._topbar = bar
        lay = QHBoxLayout(bar)
        self._topbar_lay = lay
        lay.setContentsMargins(42, 0, 42, 0)
        lay.setSpacing(13)
        lay.addWidget(_label("工作空间", "CrumbMuted"))
        self._crumb_arrow = QLabel()
        lay.addWidget(self._crumb_arrow)
        self.crumb = _label(self.section, "CrumbCurrent")
        lay.addWidget(self.crumb)
        lay.addStretch(1)

        self.return_button = QPushButton("返回阅读")
        self.return_button.setObjectName("ReturnButton")
        self.return_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.return_button.setIconSize(QSize(14, 14))
        self.return_button.clicked.connect(self.returnRequested.emit)
        self.return_button.setVisible(False)
        lay.addWidget(self.return_button)
        lay.addSpacing(7)
        self._local = _label(
            '<span style="color:#869879;">●</span>&nbsp;&nbsp;已在本地就绪', "LocalStatus"
        )
        self._local.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._local)
        lay.addSpacing(7)
        self._divider = QFrame()
        self._divider.setObjectName("TopbarDivider")
        self._divider.setFixedSize(1, 17)
        lay.addWidget(self._divider)
        lay.addSpacing(7)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("TopbarIcon")
        self.theme_button.setToolTip("切换主题")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.setIconSize(QSize(17, 17))
        self.theme_button.clicked.connect(self.themeToggleRequested.emit)
        lay.addWidget(self.theme_button)
        lay.addSpacing(7)
        mini = _label("L", "MiniAvatar")
        mini.setFixedSize(28, 28)
        mini.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(mini)
        return bar

    # ---------------------------------------------------------------- 构建：恢复提示
    def _build_recovery_banner(self) -> QFrame:
        """顶部恢复提示条：非模态，避免启动时弹窗打断（也便于自动化测试）。"""
        bar = QFrame()
        bar.setObjectName("RecoveryBanner")
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(42, 0, 42, 0)
        lay.setSpacing(10)
        self._recovery_title = _label("", "RecoveryTitle")
        lay.addWidget(self._recovery_title)
        self._recovery_text = _label("", "RecoveryText")
        self._recovery_text.setWordWrap(False)
        # 文件名很长时让正文先收缩，别把「恢复 / 丢弃 / 稍后」挤出窗口
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

    # ---------------------------------------------------------------- 构建：主体
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
        lay.setContentsMargins(42, 38, 42, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_greeting())
        lay.addSpacing(30)
        self._featured = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._featured.setSpacing(20)
        self.continue_card = _ContinueCard()
        self.continue_card.continueRequested.connect(self._on_continue)
        self.insight_card = _InsightCard()
        self.insight_card.exploreRequested.connect(self.exploreAiRequested.emit)
        self._featured.addWidget(self.continue_card, 185)
        self._featured.addWidget(self.insight_card, 100)
        lay.addLayout(self._featured)
        lay.addSpacing(34)
        lay.addWidget(self._build_library_heading())
        lay.addSpacing(21)
        lay.addWidget(self._build_filters())
        lay.addSpacing(19)
        grid_host = QWidget()
        grid_host.setObjectName("BookGrid")
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(18)
        lay.addWidget(grid_host)
        self._empty = self._build_empty()
        lay.addWidget(self._empty)
        lay.addSpacing(19)
        lay.addWidget(self._build_quiet_import())
        lay.addWidget(self._build_footer())
        lay.addStretch(1)
        return scroll

    def _build_greeting(self) -> QWidget:
        self._greeting = QWidget()
        greet = QHBoxLayout(self._greeting)
        greet.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(_label("A LITTLE FOCUS, A LOT OF POSSIBILITY", "Eyebrow"))
        left.addSpacing(14)
        self._title = _label("", "GreetingTitle")
        self._title.setTextFormat(Qt.TextFormat.RichText)
        self._title.setWordWrap(True)
        left.addWidget(self._title)
        left.addSpacing(9)
        sub = _label("在文字之间发现联结，把片刻灵感变成自己的见解。", "GreetingBody")
        sub.setWordWrap(True)
        left.addWidget(sub)
        greet.addLayout(left, 1)
        self._aside = QWidget()
        aside = QVBoxLayout(self._aside)
        aside.setContentsMargins(0, 0, 0, 2)
        aside.setSpacing(9)
        aside.addStretch(1)
        self._sun = QLabel()
        self._sun.setPixmap(render_pixmap("sun", "#B29164", 22))
        aside.addWidget(self._sun, alignment=Qt.AlignmentFlag.AlignRight)
        aside.addWidget(_label("今天，也是适合阅读的一天", "GreetingAside"))
        greet.addWidget(self._aside, alignment=Qt.AlignmentFlag.AlignBottom)
        return self._greeting

    def _build_library_heading(self) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(15)
        left = QVBoxLayout()
        left.setSpacing(8)
        h2 = QHBoxLayout()
        h2.setSpacing(10)
        self.heading = _label("我的文献库", "LibraryHeading")
        self.heading_count = _label("00", "LibraryCount")
        h2.addWidget(self.heading)
        h2.addWidget(self.heading_count)
        h2.addStretch(1)
        left.addLayout(h2)
        self.heading_desc = _label("每一份文档，都是一个新的思考起点。", "LibraryDesc")
        self.heading_desc.setWordWrap(True)
        left.addWidget(self.heading_desc)
        lay.addLayout(left, 1)

        tools = QHBoxLayout()
        tools.setSpacing(12)
        search_box = QFrame()
        search_box.setObjectName("LibrarySearch")
        sb = QHBoxLayout(search_box)
        sb.setContentsMargins(10, 0, 10, 0)
        sb.setSpacing(8)
        self._search_icon = QLabel()
        self.search = QLineEdit()
        self.search.setObjectName("LibrarySearchInput")
        self.search.setPlaceholderText("搜索文档…")
        self.search.setAccessibleName("搜索文档")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(140)
        self.search.textChanged.connect(lambda _t: self._rebuild())
        self._kbd = _label("Ctrl K", "Kbd")
        sb.addWidget(self._search_icon)
        sb.addWidget(self.search, 1)
        sb.addWidget(self._kbd)
        search_box.setFixedHeight(34)
        tools.addWidget(search_box)
        self.sort = QComboBox()
        self.sort.setObjectName("LibrarySort")
        self.sort.setAccessibleName("文档排序")
        self.sort.addItems(list(SORTS))
        self.sort.currentIndexChanged.connect(lambda _i: self._rebuild())
        tools.addWidget(self.sort)
        toggle = QFrame()
        toggle.setObjectName("ViewToggle")
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
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(lambda _c=False, m=mode: self.set_view_mode(m, notify=True))
            self._view_group.addButton(btn)
            self.view_buttons[mode] = btn
            tl.addWidget(btn)
        self.view_buttons["grid"].setChecked(True)
        tools.addWidget(toggle)
        lay.addLayout(tools)
        return box

    def _build_filters(self) -> QWidget:
        box = QWidget()
        self._filter_lay = QHBoxLayout(box)
        self._filter_lay.setContentsMargins(0, 0, 0, 0)
        self._filter_lay.setSpacing(5)
        self._filter_lay.addStretch(1)
        self._hint = _label("为专注而整理", "LibraryHint")
        self._filter_lay.addWidget(self._hint)
        return box

    def _build_empty(self) -> QWidget:
        box = QWidget()
        box.setObjectName("LibraryEmpty")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(0)
        self._empty_icon = QLabel()
        lay.addWidget(self._empty_icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(12)
        self.empty_title = _label("这里还没有文档", "EmptyTitle")
        lay.addWidget(self.empty_title, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(12)
        self.empty_body = _label("试试其他关键词或分类，新的灵感也许就在下一页。", "EmptyBody")
        self.empty_body.setWordWrap(True)
        self.empty_body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.empty_body)
        lay.addSpacing(15)
        self.empty_button = QPushButton("查看全部文档")
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
        self.quiet_import.setAccessibleName("导入 PDF")
        self.quiet_import.setProperty("dragging", "false")
        self.quiet_import.setMinimumHeight(52)
        qi = QHBoxLayout(self.quiet_import)
        qi.setContentsMargins(16, 0, 16, 0)
        qi.setSpacing(9)
        self._quiet_icon = QLabel()
        main = _label("为你的下一次灵感，留一个位置", "QuietImportText")
        cta = _label("导入 PDF", "QuietImportCta")
        self._quiet_hint = _label("或拖放文件至窗口", "QuietImportMuted")
        _passthrough(self._quiet_icon, main, cta, self._quiet_hint)
        qi.addStretch(1)
        qi.addWidget(self._quiet_icon)
        qi.addWidget(main)
        qi.addSpacing(12)
        qi.addWidget(cta)
        qi.addWidget(self._quiet_hint)
        qi.addStretch(1)
        self.quiet_import.clicked.connect(self.openRequested.emit)
        return self.quiet_import

    def _build_footer(self) -> QWidget:
        box = QWidget()
        box.setObjectName("StudioFooter")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 24, 0, 22)
        lay.setSpacing(9)
        lay.addWidget(_label("l.", "FooterMark"))
        lay.addWidget(_label("专注阅读，安静思考。", "FooterText"))
        lay.addStretch(1)
        self._footer_right = QWidget()
        right = QHBoxLayout(self._footer_right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(9)
        right.addWidget(_label("本地优先", "FooterText"))
        right.addWidget(_label("·", "FooterDot"))
        right.addWidget(_label("为好奇心而设计", "FooterText"))
        right.addSpacing(15)
        self.about_button = QPushButton("关于 Lumen")
        self.about_button.setObjectName("FooterLink")
        self.about_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.about_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.about_button.setIconSize(QSize(12, 12))
        self.about_button.clicked.connect(self.aboutRequested.emit)
        right.addWidget(self.about_button)
        lay.addWidget(self._footer_right)
        return box

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
        """当前（过滤后）网格里显示的文档路径。"""
        return [card.entry.path for card in self._cards]

    def folders(self) -> list[str]:
        """按文档数排序的文件夹（合集与筛选标签用）。"""
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
        self.set_section(SECTIONS[0][0])

    def set_view_mode(self, mode: str, *, notify: bool = False) -> None:
        if mode not in self.view_buttons:
            return
        changed = mode != self.view_mode
        self.view_mode = mode
        self.view_buttons[mode].setChecked(True)
        if changed:
            self._rebuild()
            if notify:
                self.viewModeChanged.emit(mode)

    def set_can_return(self, can: bool, tip: str = "", *, label: str = "返回阅读") -> None:
        """有打开的标签页时在顶栏显示「返回阅读」。"""
        self.return_button.setText(label)
        self.return_button.setVisible(can)
        self.return_button.setToolTip(tip or "回到正在阅读的文档")

    def set_recovery_notice(self, labels: Sequence[str]) -> None:
        """显示「崩溃前未保存的修改」提示；``labels`` 是文档名（为空则隐藏）。"""
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
        """拖入文件时高亮「留一个位置」导入区。"""
        self.quiet_import.setProperty("dragging", "true" if dragging else "false")
        _repolish(self.quiet_import)

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()
        self.scroll_area.ensureWidgetVisible(self.search, 20, 20)

    def animate_in(self) -> None:
        """进入首页时各区块依次自下淡入（设计稿 a-fade-up，间隔 70ms）。"""
        widgets: list[QWidget] = [self._greeting, self.continue_card, self.insight_card]
        widgets += self._cards[:8]
        for i, w in enumerate(widgets):
            if w.isVisibleTo(self):
                motion.fade_up(w, delay=min(i, 7) * 70)

    # ---------------------------------------------------------------- 内部：数据 → 视图
    def _rebuild_collections(self) -> None:
        for item in self._collections:
            item.setParent(None)
            item.deleteLater()
        self._collections.clear()
        folders = self.folders()[:_MAX_COLLECTIONS]
        self._col_count.setText(f"{len(folders):02d}")
        for i, name in enumerate(folders):
            item = _CollectionItem(name, _COLLECTION_DOTS[i % len(_COLLECTION_DOTS)])
            item.clicked.connect(lambda _c=False, f=name: self._on_collection(f))
            self._col_lay.addWidget(item)
            self._collections.append(item)
        self._sync_nav_visibility()

    def _rebuild_chips(self) -> None:
        for chip in self._chips:
            chip.setParent(None)
            chip.deleteLater()
        self._chips.clear()
        for i, name in enumerate([ALL_FOLDERS, *self.folders()[:_MAX_FILTER_CHIPS]]):
            chip = QPushButton(f"{name}  {len(self._entries)}" if name == ALL_FOLDERS else name)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setProperty("folder", name)
            chip.clicked.connect(lambda _c=False, f=name: self.set_folder(f))
            self._filter_lay.insertWidget(i, chip)
            self._chips.append(chip)
        self._sync_folder_buttons()

    def _sync_folder_buttons(self) -> None:
        for chip in self._chips:
            chip.setChecked(chip.property("folder") == self.folder)
        for item in self._collections:
            item.setChecked(item.label == self.folder)

    def _rebuild(self) -> None:
        entries = filter_entries(
            self._entries,
            section=self.section,
            folder=self.folder,
            query=self.search.text(),
            sort=self.sort.currentText(),
        )
        self.crumb.setText(self.section)
        self.heading.setText("我的文献库" if self.section == SECTIONS[0][0] else self.section)
        self.heading_count.setText(f"{len(entries):02d}")
        self.heading_desc.setText(
            "显示含 PDF 批注的文档；独立 Markdown 笔记请从阅读器的「工具 → 阅读笔记」打开。"
            if self.section == "含批注文档"
            else "每一份文档，都是一个新的思考起点。"
        )
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        compact = self.view_mode == "list"
        self._cards = []
        for entry in entries:
            card = _BookCard(entry, compact)
            card.set_cover_height(self._cover_h)
            card.openRequested.connect(self.openRecentRequested.emit)
            card.favoriteToggled.connect(self.favoriteToggled.emit)
            self._cards.append(card)
        self._place_cards()
        self._empty.setVisible(not entries)
        if self._entries:
            if self.section == "含批注文档":
                filtered = bool(self.search.text().strip()) or self.folder != ALL_FOLDERS
                self.empty_title.setText("没有匹配的批注文档" if filtered else "还没有含批注的文档")
                self.empty_body.setText(
                    "这里只显示记录有 PDF 批注的文档，独立 Markdown 笔记不在此列表中。"
                    "可清除关键词或查看全部文档。"
                )
            else:
                self.empty_title.setText("这里还没有文档")
                self.empty_body.setText("试试其他关键词或分类，新的灵感也许就在下一页。")
            self.empty_button.setText("查看全部文档")
        else:
            self.empty_title.setText("文献库还是空的")
            self.empty_body.setText("导入第一份 PDF，读过的文档会按时间整理在这里。")
            self.empty_button.setText("导入 PDF")

    def _place_cards(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        columns = 1 if self.view_mode == "list" else self._columns
        for c in range(4):
            self._grid.setColumnStretch(c, 1 if c < columns else 0)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // columns, i % columns)

    # ---------------------------------------------------------------- 响应式
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.apply_width(self.width())

    def apply_width(self, width: int) -> None:
        """按窗口宽度切换设计稿的断点档位（xl ≥1550 / lg / md ≤1100 / sm ≤820 / xs ≤500）。"""
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
        self._nav.setFixedWidth({"xl": 228, "lg": 228, "md": 195}.get(cls, 68))
        nav_margins = {"md": (12, 33, 12, 18), "sm": (10, 25, 10, 15), "xs": (10, 25, 10, 15)}
        self._nav_lay.setContentsMargins(*nav_margins.get(cls, (20, 33, 20, 18)))
        self._brand_lay.setContentsMargins(12 if cls in ("xl", "lg") else 0, 0, 0, 0)
        for w in (
            self._brand_text,
            self._ws_label,
            self._ws_switch,
            self._lib_caption,
            self._nav_bottom,
            self._import_text,
            self._import_key,
        ):
            w.setVisible(not narrow_nav)
        self._compact_theme.setVisible(narrow_nav)
        if narrow_nav:
            self.import_button.setFixedWidth(44)
        else:
            self.import_button.setMinimumWidth(0)
            self.import_button.setMaximumWidth(16777215)
            self.import_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 窄导航只剩图标按钮：居中；常规宽度铺满整行
        align = Qt.AlignmentFlag.AlignHCenter if narrow_nav else Qt.AlignmentFlag(0)
        self._nav_lay.setAlignment(self.import_button, align)
        for item in self.nav_items.values():
            item.set_compact(narrow_nav)
            self._nav_lay.setAlignment(item, align)
        self._sync_nav_visibility()

        pad = {"xl": 42, "lg": 42, "md": 26, "sm": 22}.get(cls, 15)
        top = {"xl": 50, "lg": 38, "md": 30, "sm": 26}.get(cls, 25)
        self._content_lay.setContentsMargins(pad, top, pad, 0)
        bar_pad = {"md": 26, "sm": 26, "xs": 17}.get(cls, 42)
        self._topbar_lay.setContentsMargins(bar_pad, 0, bar_pad, 0)
        self._topbar.setFixedHeight(55 if cls == "xs" else 70)
        self._local.setVisible(cls != "xs")
        self._divider.setVisible(cls != "xs")
        self._aside.setVisible(cls in ("xl", "lg"))
        self._title.setProperty("size", cls)
        _repolish(self._title)

        self.continue_card.set_scale(cls)
        self.insight_card.setVisible(not narrow_nav)
        self.insight_card.set_compact(cls == "md")
        self._featured.setSpacing({"xl": 24, "lg": 20}.get(cls, 15))
        self._featured.setStretch(0, 160 if cls == "md" else 185)
        self._kbd.setVisible(cls in ("xl", "lg"))
        self.search.setFixedWidth({"xl": 140, "lg": 140, "md": 96, "sm": 120}.get(cls, 160))
        self.sort.setVisible(not narrow_nav)
        self._hint.setVisible(cls != "xs")
        self._quiet_hint.setVisible(not narrow_nav)
        self._footer_right.setVisible(not narrow_nav)

        self._columns = 4 if cls in ("xl", "lg", "md") else 2
        gap = {"xl": 18, "lg": 18}.get(cls, 12 if cls != "xs" else 10)
        self._grid.setHorizontalSpacing(gap)
        self._grid.setVerticalSpacing(gap)
        self._cover_h = {"xl": 215, "lg": 177, "md": 160, "sm": 195}.get(cls, 145)
        for card in self._cards:
            card.set_cover_height(self._cover_h)
        self._place_cards()

    def _sync_nav_visibility(self) -> None:
        show = bool(self._collections) and self.width_class not in ("sm", "xs")
        self._col_caption_box.setVisible(show)
        self._col_box.setVisible(show)

    # ---------------------------------------------------------------- 槽
    def _on_collection(self, folder: str) -> None:
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

    def _on_theme_changed(self) -> None:
        p = self._theme.palette
        mode_icon = "sun" if self._theme.is_dark else "moon"
        self._title.setText(f'让每一次阅读，<span style="color:{p.accent};">都有回响。</span>')
        self._ws_chevron.setPixmap(render_pixmap("chevron_down", p.nav_text, 14))
        self._privacy_icon.setPixmap(render_pixmap("eye", "#A5B198", 18))
        self._profile_theme.setPixmap(render_pixmap(mode_icon, p.nav_text, 17))
        self._compact_theme.setIcon(QIcon(render_pixmap(mode_icon, p.nav_text, 18)))
        self._crumb_arrow.setPixmap(render_pixmap("forward", p.text_muted, 12))
        self.theme_button.setIcon(QIcon(render_pixmap(mode_icon, p.icon, 17)))
        self.return_button.setIcon(QIcon(render_pixmap("back", p.accent_text, 14)))
        self._search_icon.setPixmap(render_pixmap("search", p.text_muted, 16))
        for mode, btn in self.view_buttons.items():
            btn.setIcon(QIcon(render_pixmap(mode, p.text, 16)))
        self._quiet_icon.setPixmap(render_pixmap("plus", p.text_muted, 17))
        self._empty_icon.setPixmap(render_pixmap("search", p.text_muted, 28))
        self.about_button.setIcon(QIcon(render_pixmap("forward", p.text_muted, 12)))
        for item in self.nav_items.values():
            item.refresh()
        for col in self._collections:
            col.refresh()
        self.insight_card.refresh()
        self.update()
