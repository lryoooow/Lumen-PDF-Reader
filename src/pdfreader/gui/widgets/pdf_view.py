"""PDF 多页连续滚动视图。

在 QGraphicsScene 上纵向排列所有页，按需渲染可见页并用 LRU 缓存避免重复渲染；
同时承担文本选择、链接跳转与批注的实时预览。

工作流：
1. 文档打开后，先计算所有页的占位矩形（无需渲染，只需尺寸）
2. 视口变化时检测哪些页可见，通过 ``renderRequested`` 信号请求渲染
3. 主窗口在主线程直接渲染**受控文档**并回填（见 ``MainWindow._render_page``），
   因此批注/编辑改动所见即所得；渲染倍率由 :meth:`PdfView.render_scale` 给出
   （乘了屏幕缩放比，高分屏上不发虚；并有像素上限，放大不会撑爆内存）
4. LRU 缓存已渲染页（按页数 + 总字节双重上限）

坐标：场景坐标 = PDF 点（可见帧）× zoom，页在场景里自上而下排列；
「当前页」= 在视口中露出面积最大的页（并列取靠前者）。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractAnimation,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from pdfreader.engines.types import InkStroke, LinkInfo, PixmapData, Point, Rect, WordBox
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion
from pdfreader.utils.cache import LRUCache
from pdfreader.utils.qt_bridge import pixmap_to_qimage
from pdfreader.utils.text import clean_copied_text, join_words, normalize_pdf_text

__all__ = ["MAX_ZOOM", "MIN_ZOOM", "PAGE_GAP", "PdfView", "ReadingMode"]

if TYPE_CHECKING:
    from pdfreader.controllers.annotation_controller import AnnotationController

# 页与页之间的垂直间距、首页上方留白（像素，设计稿 mb-6 / py-6）
PAGE_GAP = 24
TOP_PADDING = 24
# 默认 LRU 缓存容量（渲染页数）
DEFAULT_CACHE_SIZE = 30
# 渲染缓存总字节上限：高倍率下 30 页可达数 GB，按字节再限一次
DEFAULT_CACHE_BYTES = 384 * 1024 * 1024
# 100%（实际大小）对应 96 DPI；zoom=1.0 是 72 DPI
ACTUAL_SIZE_ZOOM = 96.0 / 72.0
# 缩放范围：约 10% ~ 800%（以 96 DPI 为 100%）。不设上限时连续放大会让单页位图达数百 MB
MIN_ZOOM = 0.1 * ACTUAL_SIZE_ZOOM
MAX_ZOOM = 8.0 * ACTUAL_SIZE_ZOOM
# 单页位图像素上限（约 24 MP ≈ 72 MB RGB）；超过时降采样渲染、由场景放大显示
MAX_RENDER_PIXELS = 24_000_000
# 跳到页首时在上方留出的边距（像素，设计稿 offsetTop - 24）
_TOP_MARGIN = 24
# 鼠标按下到松开的移动距离在此以内视为「单击」（用于链接点击）
_CLICK_SLOP = 4
# 拖选到视口上下边缘时自动滚动：边缘触发区、每 tick 步长与间隔（像素 / 毫秒）
_AUTOSCROLL_MARGIN = 28
_AUTOSCROLL_STEP = 14
_AUTOSCROLL_INTERVAL_MS = 30
# 双击选词时，同一行内字距不超过此宽度的相邻字并成一个词（中文排版常为 0）
_WORD_JOIN_GAP = 1.0


class ReadingMode:
    """阅读配色模式。"""

    NORMAL = "normal"
    NIGHT = "night"  # 夜间：页面反色（黑底白字）
    SEPIA = "sepia"  # 护眼：暖黄纸张色


_SEPIA_TINT = QColor("#F3E7C9")

# 设计稿：夜间 = invert(0.9) hue-rotate(180deg)；护眼 = sepia(0.35) brightness(0.97)。
# 按 CSS Filter Effects 规范的矩阵实现：夜间只反转明暗、保留色相（彩图 / 图表颜色不变味）。
_HUE_180 = (
    (-0.574, 1.430, 0.144),
    (0.426, 0.430, 0.144),
    (0.426, 1.430, -0.856),
)
_SEPIA = ((0.393, 0.769, 0.189), (0.349, 0.686, 0.168), (0.272, 0.534, 0.131))


def _filter_matrix(
    mode: str,
) -> tuple[tuple[tuple[float, ...], ...], tuple[float, ...], float] | None:
    """返回 (3×3 矩阵, 偏移, 末级亮度)：out = clip(M · in + b) × brightness（0..1）。

    与 CSS 一致：每个滤镜函数的结果先裁剪到 0..1 再交给下一个（sepia → brightness）。
    """
    if mode == ReadingMode.NIGHT:
        # invert(0.9)：c' = 0.9 - 0.8c（落在 0.1..0.9，无需中间裁剪），再乘 hue-rotate(180)
        m = [[-0.8 * v for v in row] for row in _HUE_180]
        b = tuple(0.9 * sum(row) for row in _HUE_180)
        return tuple(tuple(r) for r in m), b, 1.0
    if mode == ReadingMode.SEPIA:
        a = 0.35
        m = [
            [(1 - a) * (1.0 if i == j else 0.0) + a * _SEPIA[i][j] for j in range(3)]
            for i in range(3)
        ]
        return tuple(tuple(r) for r in m), (0.0, 0.0, 0.0), 0.97
    return None


class PdfView(QGraphicsView):
    """多页连续滚动 PDF 视图。

    Signals:
        pageChanged(int): 当前页变化
        renderRequested(int, float): 请求按逻辑缩放 zoom 渲染某页
        linkActivated(LinkInfo): 单击了页面上的链接
        markupRequested(int, tuple[Rect, ...], str): 对选中文字添加标记（kind）
    """

    pageChanged = Signal(int)
    renderRequested = Signal(int, float)  # page_num, zoom（逻辑缩放）
    textCopied = Signal(int)  # 已复制字符数（用于状态栏提示）
    zoomChanged = Signal(float)  # 缩放系数变化（供状态栏更新百分比）
    # 批注提交（松开鼠标后发出，主窗口据此写入文档并重渲染）
    annotationHighlight = Signal(int, object)  # page_num, tuple[Rect, ...]（每行一个）
    annotationInk = Signal(int, object)  # page_num, InkStroke
    annotationNote = Signal(int, object)  # page_num, Point
    eraseRequested = Signal(int, object)  # page_num, Point（橡皮擦点删批注）
    placementFinished = Signal()  # 放置模式结束（已放置或 Esc 取消）
    placementRequested = Signal(
        int, object, str, object
    )  # page, Point, kind, payload（放置图片/文字）
    linkActivated = Signal(int, object)  # 链接所在页, LinkInfo
    markupRequested = Signal(int, object, str)  # page, tuple[Rect, ...], kind
    areaCaptured = Signal(int, object)  # 框选工具松开：page, Rect（可见帧 PDF 点）
    aiRequested = Signal(str)  # 右键选中文字 → AI：translate / explain / ask
    selectionFinished = Signal()  # 拖选松开且选中了文字（划词翻译据此出现）
    materialRequested = Signal(str)  # 右键「加入语料 / 引文 / 术语」：corpus / quote / term
    scrolled = Signal()  # 视口滚动（浮在选区旁的小按钮据此收起）

    def __init__(  # type: ignore[no-untyped-def]
        self,
        parent=None,
        *,
        cache_size: int = DEFAULT_CACHE_SIZE,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
    ) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        # 默认为文本选择模式：拖拽选择文本（滚轮 / 滚动条负责滚动）
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMouseTracking(True)  # 悬停链接时切换手形光标

        # 主题化外观：无边框 + 画布背景色（让白色页面浮起）
        self.setObjectName("PdfView")
        self.setFrameShape(QFrame.Shape.NoFrame)
        from pdfreader.gui.theme import get_theme

        self._palette = get_theme().palette
        self.setBackgroundBrush(QBrush(QColor(self._palette.canvas)))

        # 文档状态
        self._page_count: int = 0
        self._zoom: float = 1.5
        self._fit_mode: str | None = None  # "width" / "page" / None（手动缩放）
        self._page_sizes: list[tuple[int, int]] = []  # 各页原始尺寸 (w, h) @ 72dpi
        self._page_y_positions: list[float] = []  # 各页顶部 y 坐标
        self._page_x_positions: list[float] = []  # 各页左侧 x（宽度不一的页水平居中）
        self._frame_items: dict[int, list[QGraphicsItem]] = {}  # 页面阴影
        self._no_fade: set[int] = set()  # 编辑后重绘的页不做淡入（避免闪烁）
        self._effects: list[QAbstractAnimation] = []  # 进行中的脉冲 / 闪动 / 淡入动画
        self._scroll_anim: QPropertyAnimation | None = None
        self.highlight_color: str = "#FACC15"  # 高亮预览色（主窗口按用户选择同步）
        self._page_items: dict[int, QGraphicsItem] = {}  # 页码 → 场景项
        self._rendered_cache: LRUCache = LRUCache(capacity=cache_size)
        self._cache_bytes_limit = cache_bytes
        self._current_page: int | None = None
        self._reading_mode: str = ReadingMode.NORMAL
        # 批注支持
        self._annotation_controller: AnnotationController | None = None

        # 文本选择支持
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._text_provider: Callable[[int], tuple[WordBox, ...]] | None = None
        self._words_cache: dict[int, tuple[WordBox, ...]] = {}
        self._sel_start: tuple[int, QPointF] | None = None
        self._sel_items: list[QGraphicsItem] = []
        self._sel_page: int | None = None
        self._sel_words: list[WordBox] = []
        self._selected_text: str = ""
        self._selecting: bool = False
        self._search_items: list[QGraphicsItem] = []  # 搜索命中高亮
        # 速读标记：不写入文件的高亮层（页 → 行矩形），缩放 / 重排后重画，换文档清空
        self._marks: dict[int, list[Rect]] = {}
        self._mark_items: list[QGraphicsItem] = []

        # 链接
        self._link_provider: Callable[[int], tuple[LinkInfo, ...]] | None = None
        self._links_cache: dict[int, tuple[LinkInfo, ...]] = {}
        self._press_pos: QPointF | None = None

        # 选择手势：连击（选词 / 选行）、Shift 扩展、跨页选择、拖选自动滚动
        self._sel_anchor: tuple[int, int] | None = None  # (页, 词下标)，固定端
        self._sel_focus: tuple[int, int] | None = None  # 拖动端
        self._sel_spans: list[tuple[int, list[WordBox]]] = []
        self._click_count = 0
        self._last_click_time = 0.0
        self._last_click_pos = QPointF()
        self._shift_click = False
        self._drag_scrolled = False
        self._last_drag_pos: QPointF | None = None
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(_AUTOSCROLL_INTERVAL_MS)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)

        # 批注实时预览状态
        self._annot_page: int | None = None
        self._annot_start: QPointF | None = None
        self._annot_drawing: bool = False
        self._annot_items: list[QGraphicsItem] = []  # 高亮预览方块
        self._hl_words: list[WordBox] = []  # 当前高亮覆盖的词
        self._ink_points: list[QPointF] = []
        self._ink_item: QGraphicsPathItem | None = None
        self._capture_item: QGraphicsItem | None = None  # 框选区域预览（虚线框）

        # 待放置内容（签名图片 / 时间戳文字）：下一次点击落到页面上
        self._pending_kind: str | None = None
        self._pending_payload: object = None

        # 适宽/适页：窗口拖动时 resize 事件密集，合并后再重排（否则每个事件都全量重渲染）
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(60)
        self._fit_timer.timeout.connect(self._apply_fit)

    def set_annotation_controller(self, controller: AnnotationController) -> None:
        """注入批注控制器，使视图能在批注模式下转发鼠标事件。"""
        self._annotation_controller = controller

    # ------------------------------------------------------------------
    # 主题外观 / 阅读模式
    # ------------------------------------------------------------------
    def apply_palette(self, palette) -> None:  # type: ignore[no-untyped-def]
        """应用新配色：更新画布背景与未渲染页的骨架色。"""
        self._palette = palette
        self.setBackgroundBrush(QBrush(QColor(palette.canvas)))
        for page_num, item in list(self._page_items.items()):
            self._make_frame(page_num)
            if isinstance(item, QGraphicsRectItem):
                self._restore_placeholder(page_num)
        self.viewport().update()

    @property
    def reading_mode(self) -> str:
        return self._reading_mode

    def set_reading_mode(self, mode: str) -> None:
        """切换阅读配色（正常 / 夜间反色 / 护眼）；已渲染页按新配色重绘。"""
        if mode not in (ReadingMode.NORMAL, ReadingMode.NIGHT, ReadingMode.SEPIA):
            mode = ReadingMode.NORMAL
        if mode == self._reading_mode:
            return
        self._reading_mode = mode
        if self._page_count > 0:
            self._rendered_cache.clear()
            for page_num in list(self._page_items):
                self._restore_placeholder(page_num)
            self._request_visible_pages()

    def _page_brush(self) -> QBrush:
        """渲染前的页面骨架填充色。"""
        if self._reading_mode == ReadingMode.NIGHT or self._palette.is_dark:
            return QBrush(QColor("#3A404A"))
        if self._reading_mode == ReadingMode.SEPIA:
            return QBrush(_SEPIA_TINT)
        return QBrush(QColor("#FFFFFF"))

    def _to_qpixmap(self, data: PixmapData) -> QPixmap:
        """渲染结果 → QPixmap，并按阅读模式套用设计稿的颜色滤镜。"""
        matrix = _filter_matrix(self._reading_mode)
        if matrix is None or data.n not in (3, 4):
            image: QImage = pixmap_to_qimage(data).copy()
        else:
            image = _apply_color_matrix(data, *matrix)
        pixmap: QPixmap = QPixmap.fromImage(image)
        return pixmap

    # ------------------------------------------------------------------
    # 鼠标事件
    # ------------------------------------------------------------------
    def _current_mode(self) -> str:
        if self._annotation_controller is not None:
            return self._annotation_controller.mode
        return "none"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 待放置内容（签名/时间戳）：下一次左键点击落点
        if self._pending_kind is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                page_num, pdf_point = self._event_to_pdf(event)
                if page_num is not None and pdf_point is not None:
                    self.placementRequested.emit(
                        page_num,
                        Point(pdf_point.x(), pdf_point.y()),
                        self._pending_kind,
                        self._pending_payload,
                    )
                self.clear_pending()
            return
        mode = self._current_mode()
        if mode == "pan":  # 抓手：交给 ScrollHandDrag 平移
            super().mousePressEvent(event)
            return
        if mode == "eraser":  # 橡皮擦：点删命中的批注
            if event.button() == Qt.MouseButton.LeftButton:
                page_num, pdf_point = self._event_to_pdf(event)
                if page_num is not None and pdf_point is not None:
                    self.eraseRequested.emit(page_num, Point(pdf_point.x(), pdf_point.y()))
            return
        if mode != "none":
            if event.button() != Qt.MouseButton.LeftButton:
                return
            page_num, pdf_point = self._event_to_pdf(event)
            if page_num is None or pdf_point is None:
                return
            self._annot_page = page_num
            if mode in ("text", "freetext"):  # 便签 / 文本框：单击放置
                self.annotationNote.emit(page_num, Point(pdf_point.x(), pdf_point.y()))
            elif mode in ("highlight", "underline"):
                self._annot_start = pdf_point
                self._annot_drawing = True
                self._hl_words = []
                self._remove_overlay(self._annot_items)
            elif mode == "ink":
                self._annot_drawing = True
                self._ink_points = [pdf_point]
                self._start_ink_item(page_num)
            elif mode == "capture":
                self._annot_start = pdf_point
                self._annot_drawing = True
                self._update_capture_item(pdf_point)
            return
        # 浏览模式：左键拖拽 = 选择文本；双击选词、三击选行；不拖动直接松开 = 单击（可能是链接）
        if event.button() == Qt.MouseButton.LeftButton:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift and self.has_selection and self._sel_anchor is not None:
                self._press_pos = event.position()
                if self._extend_selection_to(event):
                    self._shift_click = True
                    self._selecting = True
                    return
            streak = 1 if shift else self._register_click(event.position())
            if streak >= 2:
                # 第二次按下在 Qt 里通常走 mouseDoubleClickEvent，这里兜住第三击
                self._begin_word_selection(event, line=streak >= 3)
                return
            self._clear_selection()
            self._press_pos = event.position()
            page_num, pdf_point = self._event_to_pdf(event)
            if page_num is not None and pdf_point is not None:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self._sel_start = (page_num, pdf_point)
                self._selecting = True
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        mode = self._current_mode()
        if mode == "pan":
            super().mouseMoveEvent(event)
            return
        if mode != "none":
            if self._annot_drawing and self._annot_page is not None:
                pt = self._point_on_page(event, self._annot_page)
                if mode in ("highlight", "underline") and self._annot_start is not None:
                    self._update_highlight_preview(
                        self._annot_page, self._annot_start, pt, underline=mode == "underline"
                    )
                elif mode == "ink":
                    self._ink_points.append(pt)
                    self._update_ink_item()
                elif mode == "capture":
                    self._update_capture_item(pt)
            return
        if self._selecting and self._sel_start is not None:
            self._last_drag_pos = event.position()
            self._update_selection(event)
            self._update_autoscroll()
            return
        if self._pending_kind is None:
            # 悬停在链接上显示手形光标，提示可点击（参考文献 / 图表 / 目录跳转）
            hovering = self._link_at_event(event) is not None
            shape = Qt.CursorShape.PointingHandCursor if hovering else Qt.CursorShape.IBeamCursor
            if self.viewport().cursor().shape() != shape:
                self.viewport().setCursor(shape)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        mode = self._current_mode()
        if mode == "pan":
            super().mouseReleaseEvent(event)
            return
        if mode != "none":
            if self._annot_drawing:
                if mode in ("highlight", "underline"):
                    self._commit_highlight(kind=mode)
                elif mode == "ink":
                    self._commit_ink()
                elif mode == "capture" and self._annot_page is not None:
                    self._commit_capture(self._point_on_page(event, self._annot_page))
                self._annot_drawing = False
            return
        if self._selecting:
            self._selecting = False
            self._autoscroll_timer.stop()
            self._last_drag_pos = None
            press, self._press_pos = self._press_pos, None
            moved = press is not None and (event.position() - press).manhattanLength() > _CLICK_SLOP
            moved = moved or self._drag_scrolled
            self._drag_scrolled = False
            if self._shift_click:
                # Shift+点击：只扩展选区，不当作链接点击
                self._shift_click = False
                if self._selected_text:
                    self.selectionFinished.emit()
                return
            if self._click_count >= 2 and not moved:
                # 双击 / 三击：保留选区，不当作链接点击
                if self._selected_text:
                    self.selectionFinished.emit()
                return
            if not moved:
                page_num, pdf_point = self._event_to_pdf(event)
                link = (
                    self.link_at(page_num, Point(pdf_point.x(), pdf_point.y()))
                    if page_num is not None and pdf_point is not None
                    else None
                )
                if link is not None and page_num is not None:
                    self._clear_selection()
                    self.linkActivated.emit(page_num, link)
            elif self._selected_text:
                self.selectionFinished.emit()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if (
            self._pending_kind is None
            and self._current_mode() == "none"
            and event.button() == Qt.MouseButton.LeftButton
        ):
            streak = self._register_click(event.position())
            if streak >= 2:
                self._begin_word_selection(event, line=streak >= 3)
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # 链接
    # ------------------------------------------------------------------
    def set_link_provider(self, provider: Callable[[int], tuple[LinkInfo, ...]]) -> None:
        """注入取链接回调 ``provider(page_num) -> tuple[LinkInfo, ...]``。"""
        self._link_provider = provider
        self._links_cache.clear()

    def _get_links(self, page_num: int) -> tuple[LinkInfo, ...]:
        cached = self._links_cache.get(page_num)
        if cached is not None:
            return cached
        links = self._link_provider(page_num) if self._link_provider is not None else ()
        self._links_cache[page_num] = links
        return links

    def link_at(self, page_num: int, point: Point) -> LinkInfo | None:
        """页内某点（PDF 点，可见帧）上的链接。"""
        for link in self._get_links(page_num):
            if link.rect.contains(point):
                return link
        return None

    def _link_at_event(self, event: QMouseEvent) -> LinkInfo | None:
        page_num, pdf_point = self._event_to_pdf(event)
        if page_num is None or pdf_point is None:
            return None
        return self.link_at(page_num, Point(pdf_point.x(), pdf_point.y()))

    # ------------------------------------------------------------------
    # 待放置内容（签名 / 时间戳）
    # ------------------------------------------------------------------
    def arm_placement(self, kind: str, payload: object) -> None:
        """进入"待放置"状态：下一次点击把内容落到页面上。"""
        self._pending_kind = kind
        self._pending_payload = payload
        self.setCursor(Qt.CursorShape.CrossCursor)

    def clear_pending(self) -> None:
        was_pending = self._pending_kind is not None
        self._pending_kind = None
        self._pending_payload = None
        self.setCursor(Qt.CursorShape.IBeamCursor)
        if was_pending:
            self.placementFinished.emit()

    # ------------------------------------------------------------------
    # 文本选择 / 复制
    # ------------------------------------------------------------------
    def set_text_provider(self, provider: Callable[[int], tuple[WordBox, ...]]) -> None:
        """注入取词回调 ``provider(page_num) -> tuple[WordBox, ...]``（用于选择）。"""
        self._text_provider = provider
        self._words_cache.clear()

    @property
    def has_selection(self) -> bool:
        return bool(self._selected_text)

    @property
    def selected_text(self) -> str:
        return self._selected_text

    def selection_rects(self) -> tuple[int, tuple[Rect, ...]] | None:
        """当前选择所在页与按行合并的矩形（给「高亮所选」等用）；跨页/无选择返回 None。"""
        if len(self._sel_spans) != 1:
            return None
        page, words = self._sel_spans[0]
        if not words:
            return None
        return page, tuple(self._line_rects(words))

    def selection_spans(self) -> tuple[tuple[int, tuple[Rect, ...]], ...]:
        """当前选择按页拆分的行矩形；跨页选择时用于绘制选区与提示。"""
        return tuple(
            (page, tuple(self._line_rects(words))) for page, words in self._sel_spans if words
        )

    def selection_view_rect(self, *, last_word: bool = False) -> QRect | None:
        """当前选区在本控件坐标里的外接矩形（给贴在选区旁的浮窗定位）；无选区返回 None。

        ``last_word=True`` 只取最后一个词（选区末尾的小按钮贴着它）。
        """
        if not self._sel_items:
            return None
        items = self._sel_items[-1:] if last_word else self._sel_items
        scene = QRectF()
        for item in items:
            scene = scene.united(item.sceneBoundingRect())
        box = QRect(self.mapFromScene(scene).boundingRect())
        box.translate(self.viewport().pos())
        return box

    def clear_selection(self) -> None:
        """清除当前文本选择（切换到批注模式、缩放、换文档时调用）。"""
        self._clear_selection()

    def copy_selection(self, *, merge_lines: bool | None = None) -> int:
        """把所选文本复制到剪贴板，返回字符数（无选择返回 0）。

        ``merge_lines=True`` 时合并 PDF 排版换行与断词（「去换行复制」）。
        """
        if not self._selected_text:
            return 0
        merge = bool(merge_lines)
        raw = self._selected_text
        text = clean_copied_text(raw) if merge else normalize_pdf_text(raw)
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        return len(text)

    def _get_words(self, page_num: int) -> tuple[WordBox, ...]:
        cached = self._words_cache.get(page_num)
        if cached is not None:
            return cached
        words = self._text_provider(page_num) if self._text_provider is not None else ()
        self._words_cache[page_num] = words
        return words

    def _update_selection(self, event: QMouseEvent) -> None:
        if self._sel_start is None:
            return
        start_page, start_pt = self._sel_start
        if self._sel_anchor is None:
            words = self._get_words(start_page)
            if not words:
                return
            self._sel_anchor = (start_page, self._nearest_word(words, start_pt))
        self._extend_selection_to(event)

    def _select_range(self, page: int, start_pt: QPointF, end_pt: QPointF) -> None:
        """选择同页内从 ``start_pt`` 到 ``end_pt`` 的文本（保留给外部调用与测试）。"""
        words = self._get_words(page)
        if not words:
            self._clear_selection()
            return
        lo = self._nearest_word(words, start_pt)
        hi = self._nearest_word(words, end_pt)
        if lo > hi:
            lo, hi = hi, lo
        self._sel_anchor = (page, lo)
        self._sel_focus = (page, hi)
        self._sel_start = (page, start_pt)
        self._apply_selection()

    @staticmethod
    def _nearest_word(words: Sequence[WordBox], pt: QPointF) -> int:
        """返回距离点最近（或包含该点）的词索引。"""
        best_i, best_d = 0, float("inf")
        px, py = pt.x(), pt.y()
        for i, w in enumerate(words):
            r = w.rect
            if r.x0 <= px <= r.x1 and r.y0 <= py <= r.y1:
                return i
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            d = (cx - px) ** 2 + (cy - py) ** 2
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    @staticmethod
    def _words_to_text(words: Sequence[WordBox], lo: int, hi: int) -> str:
        """把词序列重建为可读文本：换行处换行；同行英文词间加空格，中文字间不加。"""
        text = ""
        prev: tuple[int, int] | None = None
        for i in range(lo, hi + 1):
            w = words[i]
            key = (w.block, w.line)
            if prev is None:
                text = w.text
            elif key != prev:
                text += "\n" + w.text
            else:
                text += join_words(text, w.text) + w.text
            prev = key
        return text

    def _apply_selection(self) -> None:
        """按 anchor/focus 重算跨页选区、文本与叠加层。"""
        anchor, focus = self._sel_anchor, self._sel_focus
        if anchor is None or focus is None:
            return
        lo, hi = (focus, anchor) if focus < anchor else (anchor, focus)
        self._sel_spans = self._spans_between(lo, hi)
        self._selected_text = "\n".join(
            self._words_to_text(words, 0, len(words) - 1) for _, words in self._sel_spans if words
        )
        if len(self._sel_spans) == 1:
            self._sel_page = self._sel_spans[0][0]
            self._sel_words = list(self._sel_spans[0][1])
        else:
            self._sel_page = None
            self._sel_words = []
        self._remove_sel_items()
        color = QColor(self._palette.accent)
        color.setAlpha(80)
        items: list[QGraphicsItem] = []
        for page, words in self._sel_spans:
            if words:
                items.extend(self._add_overlay_rects(page, self._line_rects(words), color))
        self._sel_items = items

    def _spans_between(
        self, lo: tuple[int, int], hi: tuple[int, int]
    ) -> list[tuple[int, list[WordBox]]]:
        """把 (页, 词下标) 区间拆成按页的连续词段（跨页选择）。"""
        if lo[0] == hi[0]:
            words = list(self._get_words(lo[0]))
            if not words:
                return []
            return [(lo[0], words[lo[1] : hi[1] + 1])]
        spans: list[tuple[int, list[WordBox]]] = []
        first = list(self._get_words(lo[0]))
        if first:
            spans.append((lo[0], first[lo[1] :]))
        for page in range(lo[0] + 1, hi[0]):
            words = list(self._get_words(page))
            if words:
                spans.append((page, words))
        last = list(self._get_words(hi[0]))
        if last:
            spans.append((hi[0], last[: hi[1] + 1]))
        return spans

    @staticmethod
    def _same_line(a: WordBox, b: WordBox) -> bool:
        return (a.block, a.line) == (b.block, b.line)

    @staticmethod
    def _touching(a: WordBox, b: WordBox) -> bool:
        """两词是否紧挨着（中文按字成词时字距为 0；英文单词间有明显空隙）。"""
        gap = b.rect.x0 - a.rect.x1
        return gap <= max(_WORD_JOIN_GAP, a.rect.height * 0.08)

    @classmethod
    def _word_span(cls, words: Sequence[WordBox], idx: int) -> tuple[int, int]:
        """双击：从 idx 向两侧扩展成一个「词」（含中文连字）。"""
        lo = hi = idx
        while (
            lo > 0
            and cls._same_line(words[lo - 1], words[lo])
            and cls._touching(words[lo - 1], words[lo])
        ):
            lo -= 1
        while (
            hi + 1 < len(words)
            and cls._same_line(words[hi], words[hi + 1])
            and cls._touching(words[hi], words[hi + 1])
        ):
            hi += 1
        return lo, hi

    @staticmethod
    def _line_span(words: Sequence[WordBox], idx: int) -> tuple[int, int]:
        """三击：选中 idx 所在的整行。"""
        key = (words[idx].block, words[idx].line)
        lo = hi = idx
        while lo > 0 and (words[lo - 1].block, words[lo - 1].line) == key:
            lo -= 1
        while hi + 1 < len(words) and (words[hi + 1].block, words[hi + 1].line) == key:
            hi += 1
        return lo, hi

    def _begin_word_selection(self, event: QMouseEvent, *, line: bool) -> None:
        """双击选词 / 三击选行，并允许继续拖动扩展。"""
        target = self._selection_page_point(event)
        if target is None:
            return
        page, pt = target
        words = self._get_words(page)
        if not words:
            return
        idx = self._nearest_word(words, pt)
        lo, hi = self._line_span(words, idx) if line else self._word_span(words, idx)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._sel_anchor = (page, lo)
        self._sel_focus = (page, hi)
        self._sel_start = (page, pt)
        self._apply_selection()
        self._press_pos = event.position()
        self._selecting = True

    def _register_click(self, pos: QPointF) -> int:
        """记录连击次数：1 单击、2 双击、3 三击，第 4 击重新开始。"""
        now = time.monotonic()
        interval = QApplication.doubleClickInterval() / 1000.0
        close = (pos - self._last_click_pos).manhattanLength() <= _CLICK_SLOP
        if self._click_count and close and now - self._last_click_time <= interval:
            # 同一次物理点击可能先后收到 press 与 dblclick，50ms 内不重复计数
            if now - self._last_click_time > 0.05:
                self._click_count = 1 if self._click_count >= 3 else self._click_count + 1
        else:
            self._click_count = 1
        self._last_click_time = now
        self._last_click_pos = QPointF(pos)
        return self._click_count

    def _selection_page_point(self, event: QMouseEvent) -> tuple[int, QPointF] | None:
        return self._scene_page_point(self.mapToScene(event.position().toPoint()))

    def _scene_page_point(self, scene_pt: QPointF) -> tuple[int, QPointF] | None:
        """场景坐标 → (页, 页内 PDF 点)；落在页间隙时取纵向最近页。"""
        page = self._page_at_y(scene_pt.y())
        if page is None:
            page = self._nearest_page(scene_pt.y())
        if page is None:
            return None
        page_x, page_y = self._page_origin(page)
        return page, QPointF(
            (scene_pt.x() - page_x) / self._zoom, (scene_pt.y() - page_y) / self._zoom
        )

    def _nearest_page(self, scene_y: float) -> int | None:
        """页间隙里离 scene_y 最近的页。"""
        best: int | None = None
        best_d = float("inf")
        for i in range(min(self._page_count, len(self._page_y_positions))):
            y = self._page_y_positions[i]
            _, h = self._scaled_size(i)
            if scene_y < y:
                d = y - scene_y
            elif scene_y > y + h:
                d = scene_y - (y + h)
            else:
                d = 0.0
            if d < best_d:
                best, best_d = i, d
        return best

    def _extend_selection_to(self, event: QMouseEvent) -> bool:
        return self._extend_selection_to_point(self.mapToScene(event.position().toPoint()))

    def _extend_selection_to_point(self, scene_pt: QPointF) -> bool:
        target = self._scene_page_point(scene_pt)
        if target is None or self._sel_anchor is None:
            return False
        page, pt = target
        words = self._get_words(page)
        if not words:
            return False
        self._sel_focus = (page, self._nearest_word(words, pt))
        self._apply_selection()
        return True

    def _update_autoscroll(self) -> None:
        """拖选到视口上下边缘时启动自动滚动。"""
        pos = self._last_drag_pos
        if pos is None:
            self._autoscroll_timer.stop()
            return
        height = self.viewport().height()
        active = pos.y() < _AUTOSCROLL_MARGIN or pos.y() > height - _AUTOSCROLL_MARGIN
        if active and not self._autoscroll_timer.isActive():
            self._autoscroll_timer.start()
        elif not active:
            self._autoscroll_timer.stop()

    def _autoscroll_tick(self) -> None:
        pos = self._last_drag_pos
        if pos is None or not self._selecting:
            self._autoscroll_timer.stop()
            return
        height = self.viewport().height()
        if pos.y() < _AUTOSCROLL_MARGIN:
            delta = -_AUTOSCROLL_STEP
        elif pos.y() > height - _AUTOSCROLL_MARGIN:
            delta = _AUTOSCROLL_STEP
        else:
            self._autoscroll_timer.stop()
            return
        bar = self.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + delta)
        if bar.value() == before:  # 已到文档头/尾
            self._autoscroll_timer.stop()
            return
        self._drag_scrolled = True
        self._extend_selection_to_point(self.mapToScene(pos.toPoint()))

    def _remove_sel_items(self) -> None:
        self._remove_overlay(self._sel_items)

    # ------------------------------------------------------------------
    # 通用叠加层（选择框 / 高亮预览共用）
    # ------------------------------------------------------------------
    def _page_origin(self, page: int) -> tuple[float, float]:
        """某页左上角在场景中的坐标。"""
        x = self._page_x_positions[page] if page < len(self._page_x_positions) else 0.0
        y = self._page_y_positions[page] if page < len(self._page_y_positions) else 0.0
        return x, y

    def _add_overlay_rects(
        self, page: int, rects: Sequence[Rect], color: QColor
    ) -> list[QGraphicsItem]:
        page_x, page_y = self._page_origin(page)
        brush = QBrush(color)
        pen = QPen(Qt.PenStyle.NoPen)
        items: list[QGraphicsItem] = []
        for r in rects:
            item = self._scene.addRect(
                QRectF(
                    page_x + r.x0 * self._zoom,
                    page_y + r.y0 * self._zoom,
                    r.width * self._zoom,
                    r.height * self._zoom,
                ),
                pen,
                brush,
            )
            item.setZValue(10)
            items.append(item)
        return items

    @staticmethod
    def _remove_overlay(items: list[QGraphicsItem]) -> None:
        for item in items:
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
        items.clear()

    # ------------------------------------------------------------------
    # 批注实时预览（拖拽时即时显示，松开提交）
    # ------------------------------------------------------------------
    def _point_on_page(self, event: QMouseEvent, page: int) -> QPointF:
        """把鼠标事件换算成相对某页的 PDF 点（允许超出页边界）。"""
        scene_pt = self.mapToScene(event.position().toPoint())
        page_x, page_y = self._page_origin(page)
        return QPointF((scene_pt.x() - page_x) / self._zoom, (scene_pt.y() - page_y) / self._zoom)

    def _update_highlight_preview(
        self, page: int, start_pt: QPointF, end_pt: QPointF, *, underline: bool = False
    ) -> None:
        words = self._get_words(page)
        self._remove_overlay(self._annot_items)
        if not words:
            self._hl_words = []
            return
        i0 = self._nearest_word(words, start_pt)
        i1 = self._nearest_word(words, end_pt)
        lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
        self._hl_words = list(words[lo : hi + 1])
        if underline:  # 下划线预览：词下方 1.5pt 的蓝线
            color = QColor("#60A5FA")
            rects = [Rect(w.rect.x0, w.rect.y1 - 1.5, w.rect.x1, w.rect.y1) for w in self._hl_words]
        else:  # 高亮预览：当前高亮色，约 45% 不透明（设计稿 color + "70"）
            color = QColor(self.highlight_color)
            color.setAlpha(115)
            rects = [w.rect for w in self._hl_words]
        self._annot_items = self._add_overlay_rects(page, rects, color)

    def _commit_highlight(self, kind: str = "highlight") -> None:
        if self._hl_words and self._annot_page is not None:
            rects = self._line_rects(self._hl_words)
            if rects:
                if kind == "underline":
                    self.markupRequested.emit(self._annot_page, tuple(rects), "underline")
                else:
                    self.annotationHighlight.emit(self._annot_page, tuple(rects))
        self._remove_overlay(self._annot_items)
        self._hl_words = []
        self._annot_start = None

    @staticmethod
    def _line_rects(words: Sequence[WordBox]) -> list[Rect]:
        """按行分组，每行合并为一个包围矩形（高亮贴合文字，按行走）。"""
        groups: dict[tuple[int, int], list[WordBox]] = {}
        for w in words:
            groups.setdefault((w.block, w.line), []).append(w)
        rects: list[Rect] = []
        for line in groups.values():
            x0 = min(w.rect.x0 for w in line)
            y0 = min(w.rect.y0 for w in line)
            x1 = max(w.rect.x1 for w in line)
            y1 = max(w.rect.y1 for w in line)
            rects.append(Rect(x0, y0, x1, y1))
        return rects

    def _ink_pen(self) -> QPen:
        pen = QPen(QColor(self._palette.danger))  # 红色手写，醒目
        pen.setWidthF(max(2.0, 2.2 * self._zoom))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def _start_ink_item(self, page: int) -> None:
        self._ink_item = self._scene.addPath(QPainterPath(), self._ink_pen())
        self._ink_item.setZValue(11)

    def _update_ink_item(self) -> None:
        if self._ink_item is None or self._annot_page is None:
            return
        page_x, page_y = self._page_origin(self._annot_page)
        path = QPainterPath()
        pts = self._ink_points
        if pts:
            path.moveTo(page_x + pts[0].x() * self._zoom, page_y + pts[0].y() * self._zoom)
            for p in pts[1:]:
                path.lineTo(page_x + p.x() * self._zoom, page_y + p.y() * self._zoom)
        self._ink_item.setPath(path)

    def _commit_ink(self) -> None:
        if len(self._ink_points) >= 2 and self._annot_page is not None:
            stroke = InkStroke(points=tuple(Point(p.x(), p.y()) for p in self._ink_points))
            self.annotationInk.emit(self._annot_page, stroke)
        if self._ink_item is not None:
            scene = self._ink_item.scene()
            if scene is not None:
                scene.removeItem(self._ink_item)
            self._ink_item = None
        self._ink_points = []

    def _capture_rect(self, end: QPointF) -> Rect | None:
        start, page = self._annot_start, self._annot_page
        if start is None or page is None:
            return None
        return Rect(
            min(start.x(), end.x()),
            min(start.y(), end.y()),
            max(start.x(), end.x()),
            max(start.y(), end.y()),
        )

    def _update_capture_item(self, end: QPointF) -> None:
        rect = self._capture_rect(end)
        if rect is None or self._annot_page is None:
            return
        page_x, page_y = self._page_origin(self._annot_page)
        scene_rect = QRectF(
            page_x + rect.x0 * self._zoom,
            page_y + rect.y0 * self._zoom,
            rect.width * self._zoom,
            rect.height * self._zoom,
        )
        if self._capture_item is None:
            accent = QColor(self._palette.accent)
            pen = QPen(accent, 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            fill = QColor(accent)
            fill.setAlpha(28)
            self._capture_item = self._scene.addRect(scene_rect, pen, QBrush(fill))
            self._capture_item.setZValue(12)
        elif isinstance(self._capture_item, QGraphicsRectItem):
            self._capture_item.setRect(scene_rect)

    def _remove_capture_item(self) -> None:
        if self._capture_item is not None:
            scene = self._capture_item.scene()
            if scene is not None:
                scene.removeItem(self._capture_item)
            self._capture_item = None

    def _commit_capture(self, end: QPointF) -> None:
        rect = self._capture_rect(end)
        page = self._annot_page
        self._remove_capture_item()
        self._annot_start = None
        # 太小的框多半是误点（< 6pt）
        if rect is not None and page is not None and rect.width >= 6 and rect.height >= 6:
            self.areaCaptured.emit(page, rect)

    def _clear_annot_preview(self) -> None:
        self._remove_overlay(self._annot_items)
        self._remove_capture_item()
        if self._ink_item is not None:
            scene = self._ink_item.scene()
            if scene is not None:
                scene.removeItem(self._ink_item)
            self._ink_item = None
        self._ink_points = []
        self._hl_words = []
        self._annot_start = None
        self._annot_page = None
        self._annot_drawing = False

    def _clear_selection(self) -> None:
        self._remove_sel_items()
        self._selected_text = ""
        self._sel_words = []
        self._sel_page = None
        self._sel_start = None
        self._selecting = False
        self._sel_anchor = None
        self._sel_focus = None
        self._sel_spans = []
        self._shift_click = False
        self._drag_scrolled = False
        self._last_drag_pos = None
        self._autoscroll_timer.stop()

    def select_all_page(self) -> None:
        """全选当前页的文字（没有当前页时取第一页）。"""
        if self._page_count == 0:
            return
        page = self._current_page if self._current_page is not None else 0
        words = self._get_words(page)
        if not words:
            return
        self._sel_anchor = (page, 0)
        self._sel_focus = (page, len(words) - 1)
        self._sel_start = (page, QPointF(words[0].rect.x0, words[0].rect.y0))
        self._apply_selection()
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)

    # ------------------------------------------------------------------
    # 搜索命中定位
    # ------------------------------------------------------------------
    def show_search_hit(self, page_num: int, rect: Rect) -> None:
        """滚动到某页的命中矩形并高亮它（``rect`` 为可见帧坐标）。"""
        if page_num < 0 or page_num >= self._page_count:
            return
        self._remove_overlay(self._search_items)
        if page_num >= len(self._page_y_positions):
            return
        page_x, page_y = self._page_origin(page_num)
        color = QColor(self._palette.mark)
        color.setAlpha(130)
        self._search_items = self._add_overlay_rects(page_num, [rect], color)
        for item in self._search_items:
            if isinstance(item, QGraphicsRectItem):
                item.setPen(QPen(QColor(self._palette.mark), 0))
        # 让命中出现在视口中央；命中框脉冲一次（设计稿 anim-pulse）
        center_y = page_y + (rect.y0 + rect.height / 2) * self._zoom
        center_x = page_x + (rect.x0 + rect.x1) / 2 * self._zoom
        self.centerOn(center_x, center_y)
        self._update_current_page()
        self.flash_rects(page_num, [rect], QColor(self._palette.accent), pulse=True)

    # ------------------------------------------------------------------
    # 速读标记
    # ------------------------------------------------------------------
    def set_marks(self, page_num: int, rects: Sequence[Rect]) -> None:
        """给某页设置速读标记（空序列 = 清除该页）；不改文件，缩放后仍在。"""
        if rects:
            self._marks[page_num] = list(rects)
        else:
            self._marks.pop(page_num, None)
        self._draw_marks()

    def clear_marks(self) -> None:
        self._marks.clear()
        self._draw_marks()

    def marks(self) -> dict[int, list[Rect]]:
        return {page: list(rects) for page, rects in self._marks.items()}

    def _draw_marks(self) -> None:
        self._remove_overlay(self._mark_items)
        color = QColor(self._palette.accent)
        color.setAlpha(60)
        for page, rects in sorted(self._marks.items()):
            if page < len(self._page_y_positions):
                self._mark_items.extend(self._add_overlay_rects(page, rects, color))
        for item in self._mark_items:
            item.setZValue(9)

    def clear_search_hit(self) -> None:
        """清除搜索命中高亮（关闭搜索面板 / 换文档 / 缩放时调用）。"""
        self._remove_overlay(self._search_items)

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape:
            # Esc：取消待放置 / 退出批注功能 / 清除选择
            if self._pending_kind is not None:
                self.clear_pending()
                return
            if self._current_mode() != "none":
                self._clear_annot_preview()
                if self._annotation_controller is not None:
                    self._annotation_controller.set_mode("none")
                return
            if self._selecting or self.has_selection:
                self._clear_selection()
                return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all_page()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self._emit_copy(merge_lines=None)
            return
        super().keyPressEvent(event)

    def _emit_copy(self, *, merge_lines: bool | None) -> None:
        n = self.copy_selection(merge_lines=merge_lines)
        if n:
            self.textCopied.emit(n)

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.has_selection:
            menu = QMenu(self)
            menu.addAction("复制", lambda: self._emit_copy(merge_lines=False))
            menu.addAction("复制（合并换行）", lambda: self._emit_copy(merge_lines=True))
            menu.addSeparator()
            if len(self._sel_spans) > 1:
                hint = menu.addAction("跨页选择：暂不支持加标记，可复制或翻译")
                if hint is not None:
                    hint.setEnabled(False)
            else:
                for label, kind in (
                    ("高亮", "highlight"),
                    ("下划线", "underline"),
                    ("删除线", "strikeout"),
                    ("波浪线", "squiggly"),
                ):
                    menu.addAction(label, lambda k=kind: self._request_markup(k))
            menu.addSeparator()
            for label, kind in (
                ("AI 翻译", "translate"),
                ("AI 解释", "explain"),
                ("问 AI…", "ask"),
            ):
                menu.addAction(label, lambda k=kind: self.aiRequested.emit(k))
            menu.addSeparator()
            for label, kind in (
                ("加入语料", "corpus"),
                ("加入引文", "quote"),
                ("加入术语…", "term"),
            ):
                menu.addAction(label, lambda k=kind: self.materialRequested.emit(k))
            menu.exec(event.globalPos())
            return
        super().contextMenuEvent(event)

    def _request_markup(self, kind: str) -> None:
        selection = self.selection_rects()
        if selection is None:
            return
        page, rects = selection
        self._clear_selection()
        self.markupRequested.emit(page, rects, kind)

    def _event_to_pdf(self, event: QMouseEvent) -> tuple[int | None, QPointF | None]:
        """将鼠标事件转换为 (页码, PDF点坐标 QPointF)。"""
        scene_pt = self.mapToScene(event.position().toPoint())
        page_num = self._page_at_y(scene_pt.y())
        if page_num is None:
            return None, None
        page_x, page_y = self._page_origin(page_num)
        return page_num, QPointF(
            (scene_pt.x() - page_x) / self._zoom, (scene_pt.y() - page_y) / self._zoom
        )

    def _page_at_y(self, scene_y: float) -> int | None:
        """返回场景 y 坐标所在的页码（落在页间隙里返回 None）。"""
        for i in range(self._page_count):
            y = self._page_y_positions[i]
            _, h = self._scaled_size(i)
            if y <= scene_y <= y + h:
                return i
        return None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def current_page_num(self) -> int | None:
        return self._current_page

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def page_count(self) -> int:
        return self._page_count

    # ------------------------------------------------------------------
    # 文档设置
    # ------------------------------------------------------------------
    def setup_document(
        self,
        page_count: int,
        page_sizes: list[tuple[int, int]],
        *,
        keep_page: int = 0,
        keep_offset: float = 0.0,
    ) -> None:
        """设置文档：页数 + 各页原始尺寸，计算占位布局，并定位到 ``keep_page``。

        编辑（删页 / 旋转 / 撤销）后重排时由调用方传入原来的页与页内位置；旧实现在这里
        把当前页清成 None，下一次「删除 / 旋转」就会落到第 1 页上。
        """
        self._scene.clear()
        self._page_items.clear()
        self._rendered_cache.clear()
        # 场景已 clear，旧叠加项全部失效，直接重置状态
        self._sel_items.clear()
        self._selected_text = ""
        self._sel_words = []
        self._sel_page = None
        self._sel_start = None
        self._selecting = False
        self._sel_anchor = None
        self._sel_focus = None
        self._sel_spans.clear()
        self._shift_click = False
        self._drag_scrolled = False
        self._last_drag_pos = None
        self._autoscroll_timer.stop()
        self._words_cache.clear()
        self._links_cache.clear()
        self._search_items.clear()
        self._mark_items.clear()
        self._marks.clear()
        self._annot_items.clear()
        self._ink_item = None
        self._ink_points = []
        self._hl_words = []
        self._annot_start = None
        self._annot_page = None
        self._annot_drawing = False
        self._frame_items.clear()
        self._no_fade.clear()
        self._page_count = page_count
        self._page_sizes = list(page_sizes)
        self._current_page = None
        self._reset_page_items()
        if page_count > 0:
            self.restore_position(min(max(keep_page, 0), page_count - 1), keep_offset)
        self._request_visible_pages()

    def _scaled_size(self, page_num: int) -> tuple[float, float]:
        """计算某页缩放后的尺寸。"""
        if page_num >= len(self._page_sizes):
            return (595 * self._zoom, 842 * self._zoom)  # A4 默认
        w, h = self._page_sizes[page_num]
        return (w * self._zoom, h * self._zoom)

    # ------------------------------------------------------------------
    # 渲染结果接收
    # ------------------------------------------------------------------
    def render_scale(self, page_num: int) -> float:
        """渲染该页应使用的引擎缩放：逻辑缩放 × 屏幕缩放比，并受像素上限约束。

        不乘屏幕缩放比时，125% / 150% 的 Windows 屏幕会把位图再放大显示，文字发虚；
        不设像素上限时高倍率单页位图可达数百 MB。
        """
        dpr = max(1.0, float(self.devicePixelRatioF()))
        scale = self._zoom * dpr
        if 0 <= page_num < len(self._page_sizes):
            w, h = self._page_sizes[page_num]
        else:
            w, h = 595, 842
        if w > 0 and h > 0 and w * h * scale * scale > MAX_RENDER_PIXELS:
            scale = math.sqrt(MAX_RENDER_PIXELS / (w * h))
        return scale

    def on_page_rendered(self, page_num: int, zoom: float, data: PixmapData) -> None:
        """渲染结果回填：转 QPixmap 并替换占位；zoom 与当前不一致（缩放期间）则丢弃。"""
        if zoom != self._zoom:
            return  # 过时结果
        if page_num < 0 or page_num >= self._page_count:
            return

        qpixmap = self._to_qpixmap(data)
        self._rendered_cache.put(page_num, qpixmap)
        self._enforce_cache_bytes(keep=page_num)
        self._update_page_item(page_num, qpixmap)
        self._unload_offscreen_pages()

    def _enforce_cache_bytes(self, *, keep: int) -> None:
        """渲染缓存总字节超限时，从最久未用的页开始淘汰（刚渲染的页保留）。"""
        total = sum(_pixmap_bytes(pm) for _k, pm in self._rendered_cache.items())
        while total > self._cache_bytes_limit and len(self._rendered_cache) > 1:
            oldest = self._rendered_cache.keys()[0]
            if oldest == keep:
                break
            popped = self._rendered_cache.pop_oldest()
            if popped is None:
                break
            total -= _pixmap_bytes(popped[1])

    def _unload_offscreen_pages(self) -> None:
        """把「既不在渲染缓存、也不在视口内」的页换回骨架占位。

        场景项会一直持有位图：只淘汰 LRU 条目等于位图永不释放。视口内的页即使已被
        淘汰也先留着，否则用户眼前的页会突然变成空白。
        """
        visible = set(self._visible_pages(self._viewport_scene_rect()))
        for page_num, item in list(self._page_items.items()):
            if page_num in visible or self._rendered_cache.contains(page_num):
                continue
            if isinstance(item, QGraphicsPixmapItem):
                self._restore_placeholder(page_num)

    def _viewport_scene_rect(self) -> QRectF:
        """当前视口在场景坐标系里的矩形。"""
        rect: QRectF = self.mapToScene(self.viewport().rect()).boundingRect()
        return rect

    def is_page_rendered(self, page_num: int) -> bool:
        """该页是否已有渲染缓存。"""
        return self._rendered_cache.contains(page_num)

    def release_render_cache(self) -> None:
        """标签切到后台：丢掉全部渲染位图，只留骨架占位与阅读位置（切回时按需重渲染）。

        后台标签只占文档本身的内存，开再多标签渲染内存也只有当前这一份。
        """
        self._rendered_cache.clear()
        for page_num, item in list(self._page_items.items()):
            if isinstance(item, QGraphicsPixmapItem):
                self._restore_placeholder(page_num)

    def settle_scroll(self) -> None:
        """平滑滚动进行中就立刻落到终点（标签切到后台时调用，免得停在半路）。"""
        anim = self._scroll_anim
        if anim is None:
            return
        self._scroll_anim = None
        end = anim.endValue()
        anim.stop()
        self.verticalScrollBar().setValue(int(end))
        self._update_current_page()

    def request_visible(self) -> None:
        """重新请求视口内尚未渲染的页（标签切回前台时调用）。"""
        self._update_current_page()
        self._request_visible_pages()

    def invalidate_page(self, page_num: int) -> None:
        """丢弃某页的渲染 / 词表 / 链接缓存，并把该页换回骨架占位（调用方随后重新渲染）。"""
        self._rendered_cache.remove(page_num)
        self._words_cache.pop(page_num, None)
        self._links_cache.pop(page_num, None)
        self._no_fade.add(page_num)
        self._restore_placeholder(page_num)

    def _update_page_item(self, page_num: int, pixmap: QPixmap) -> None:
        """用渲染好的 pixmap 替换占位项（位图比逻辑尺寸大时按比例缩小显示）。

        首次出现的页淡入（设计稿页面 anim-up）；批注 / 编辑后的重绘直接替换，不闪。
        """
        if page_num not in self._page_items:
            return
        old_item = self._page_items[page_num]
        self._scene.removeItem(old_item)
        x, y = self._page_origin(page_num)
        w, h = self._scaled_size(page_num)
        new_item = self._scene.addPixmap(pixmap)
        # 用 setPos 定位：setScale 作用于项自身坐标，若改用 setOffset 定位，
        # 缩放比例会把 offset 一起缩放，页面越靠后错位越大。
        new_item.setPos(x, y)
        new_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        if abs(pixmap.width() - w) > 1 or abs(pixmap.height() - h) > 1:
            new_item.setScale(w / pixmap.width() if pixmap.width() else 1.0)
        self._page_items[page_num] = new_item
        if page_num in self._no_fade:
            self._no_fade.discard(page_num)
        elif motion.enabled():
            new_item.setOpacity(0.0)
            self._run_effect(
                0.0, 1.0, TOKENS.anim_drop, lambda v, it=new_item: it.setOpacity(float(v))
            )

    # ------------------------------------------------------------------
    # 动效：脉冲 / 闪动 / 平滑滚动
    # ------------------------------------------------------------------
    def _run_effect(self, start: float, end: float, duration: int, step, loops: int = 1) -> None:  # type: ignore[no-untyped-def]
        anim = QVariantAnimation(self)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setDuration(duration)
        anim.setLoopCount(loops)
        anim.setEasingCurve(motion.EASE if loops == 1 else anim.easingCurve())

        def safe_step(value: object) -> None:
            # 动画期间场景项可能已被移除（缩放 / 换文档 / 重绘），此时直接结束动画
            try:
                step(value)
            except RuntimeError:
                anim.stop()

        anim.valueChanged.connect(safe_step)
        self._effects.append(anim)

        def done() -> None:
            if anim in self._effects:
                self._effects.remove(anim)

        anim.finished.connect(done)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def pulse_page(self, page_num: int) -> None:
        """目标页外圈向外扩散的强调色光环（设计稿跳转后的 anim-pulse，0.9s × 2）。"""
        if not motion.enabled() or not 0 <= page_num < self._page_count:
            return
        x, y = self._page_origin(page_num)
        w, h = self._scaled_size(page_num)
        ring = self._scene.addRect(QRectF(x, y, w, h), QPen(Qt.PenStyle.NoPen))
        ring.setZValue(9)
        base = QColor(self._palette.accent)

        def step(v: object, item: QGraphicsRectItem = ring) -> None:
            t = float(v)  # type: ignore[arg-type]
            spread = 14 * t
            color = QColor(base)
            color.setAlphaF(0.55 * (1 - t))
            pen = QPen(color, spread * 2)
            item.setPen(pen)

        self._run_effect(0.0, 1.0, TOKENS.anim_pulse, step, loops=2)
        QTimer.singleShot(TOKENS.anim_pulse * 2 + 50, lambda it=ring: _remove_item(it))

    def flash_rects(
        self, page_num: int, rects: Sequence[Rect], color: QColor, *, pulse: bool = False
    ) -> None:
        """在页内矩形上闪一下再淡出（标记提交反馈 / 搜索命中脉冲）。"""
        if not motion.enabled() or not rects:
            return
        items = self._add_overlay_rects(page_num, rects, color)
        for item in items:
            item.setZValue(12)

        def step(v: object) -> None:
            t = float(v)  # type: ignore[arg-type]
            for item in items:
                if isinstance(item, QGraphicsRectItem):
                    if pulse:
                        c = QColor(color)
                        c.setAlphaF(0.6 * (1 - t))
                        item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                        item.setPen(QPen(c, 2 + 10 * t))
                    else:
                        item.setOpacity(1 - t)

        duration = TOKENS.anim_pulse if pulse else 450
        self._run_effect(0.0, 1.0, duration, step, loops=2 if pulse else 1)
        QTimer.singleShot(
            duration * (2 if pulse else 1) + 50, lambda its=items: self._remove_overlay(its)
        )

    # ------------------------------------------------------------------
    # 按需渲染
    # ------------------------------------------------------------------
    def _request_visible_pages(self) -> None:
        """检测可见页并请求渲染（未缓存的部分）。"""
        for page_num in self._visible_pages(self._viewport_scene_rect()):
            if not self.is_page_rendered(page_num):
                self.renderRequested.emit(page_num, self._zoom)

    def _visible_pages(self, viewport_rect: QRectF) -> list[int]:
        """返回与视口相交的页码列表。"""
        result: list[int] = []
        for i in range(self._page_count):
            y = self._page_y_positions[i]
            _, h = self._scaled_size(i)
            if viewport_rect.intersects(QRectF(0, y, max(1.0, viewport_rect.width()), h)):
                result.append(i)
        return result

    # ------------------------------------------------------------------
    # 滚动：更新当前页 + 触发按需渲染
    # ------------------------------------------------------------------
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._update_current_page()
        self._request_visible_pages()
        self.scrolled.emit()

    def _update_current_page(self) -> None:
        """当前页 = 视口中露出高度最大的页（并列取靠前者）；无文档时为 None。"""
        if self._page_count == 0 or not self._page_y_positions:
            return
        view = self._viewport_scene_rect()
        best, best_h = None, 0.0
        for i in range(self._page_count):
            y = self._page_y_positions[i]
            h = self._scaled_size(i)[1]
            visible = min(y + h, view.bottom()) - max(y, view.top())
            if visible > best_h + 0.5:
                best, best_h = i, visible
        if best is None:  # 视口完全落在页之外（极端情况）：取最近的页
            best = 0 if view.top() < self._page_y_positions[0] else self._page_count - 1
        if self._current_page != best:
            self._current_page = best
            self.pageChanged.emit(best)

    # ------------------------------------------------------------------
    # 阅读位置（编辑后恢复 / 记住上次阅读位置 / 缩放后保持）
    # ------------------------------------------------------------------
    def reading_position(self) -> tuple[int, float]:
        """当前阅读位置：(页码, 视口顶边在该页内的相对位置 0..1)。"""
        if self._page_count == 0:
            return (0, 0.0)
        top = self._viewport_scene_rect().top()
        page = self._current_page if self._current_page is not None else 0
        h = self._scaled_size(page)[1]
        offset = (top + _TOP_MARGIN - self._page_y_positions[page]) / h if h > 0 else 0.0
        return (page, min(max(offset, 0.0), 1.0))

    def restore_position(
        self, page_num: int, offset: float = 0.0, *, animate: bool = False
    ) -> None:
        """滚到某页的页内相对位置（0 = 页首）。"""
        if page_num < 0 or page_num >= self._page_count:
            return
        h = self._scaled_size(page_num)[1]
        target = self._page_y_positions[page_num] + min(max(offset, 0.0), 1.0) * h
        self._scroll_top_to(target - _TOP_MARGIN, animate=animate)

    def _scroll_top_to(self, scene_top: float, *, animate: bool = False) -> None:
        """让视口顶边对齐到场景 y（受滚动范围限制），水平位置保持不变。

        ``animate`` 时平滑滚动（设计稿 scrollTo behavior: smooth）。
        """
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
            self._scroll_anim = None
        bar = self.verticalScrollBar()
        delta = scene_top - self._viewport_scene_rect().top()
        target = min(max(bar.value() + round(delta), bar.minimum()), bar.maximum())
        if animate and motion.enabled() and abs(target - bar.value()) > 2:
            anim = QPropertyAnimation(bar, b"value", self)
            anim.setStartValue(bar.value())
            anim.setEndValue(target)
            anim.setDuration(TOKENS.anim_scroll)
            anim.setEasingCurve(motion.EASE)
            anim.finished.connect(self._update_current_page)
            self._scroll_anim = anim
            anim.start()
            return
        bar.setValue(target)
        self._update_current_page()

    # ------------------------------------------------------------------
    # 缩放
    # ------------------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        """设置缩放并重新布局（限制在 MIN_ZOOM..MAX_ZOOM），保持当前阅读位置。"""
        if zoom <= 0:
            return
        zoom = min(max(zoom, MIN_ZOOM), MAX_ZOOM)
        if math.isclose(zoom, self._zoom, rel_tol=1e-9):
            self.zoomChanged.emit(self._zoom)
            return
        # 缩放后选择框 / 批注预览位置会失效，先清除
        self._clear_selection()
        self._clear_annot_preview()
        self.clear_search_hit()
        page, offset = self.reading_position()
        self._zoom = zoom
        if self._page_count > 0:
            self._reset_page_items()
            self._rendered_cache.clear()
            self.restore_position(page, offset)
            self._request_visible_pages()
        self.zoomChanged.emit(self._zoom)

    def _make_placeholder(self, page_num: int, y: float) -> QGraphicsRectItem:
        """某页渲染前的骨架占位（页面底色，阴影由 _make_frame 单独画）。"""
        w, h = self._scaled_size(page_num)
        x = self._page_x_positions[page_num] if page_num < len(self._page_x_positions) else 0.0
        item: QGraphicsRectItem = self._scene.addRect(
            QRectF(x, y, w, h), QPen(Qt.PenStyle.NoPen), self._page_brush()
        )
        return item

    def _make_frame(self, page_num: int) -> None:
        """页面柔和阴影（设计稿 shadow-[0_1px_2px_.06, 0_8px_30px_-8px_.18]）：几层渐淡的圆角矩形。"""
        for item in self._frame_items.pop(page_num, []):
            _remove_item(item)
        x, y = self._page_origin(page_num)
        w, h = self._scaled_size(page_num)
        dark = self._palette.is_dark
        layers = [
            (0.0, 1.0, 0.0, 16 if not dark else 60),  # 0 1px 2px
            (-2.0, 8.0, 6.0, 10 if not dark else 40),  # 0 8px 30px -8px（近似）
            (-4.0, 12.0, 12.0, 6 if not dark else 26),
            (-6.0, 16.0, 20.0, 3 if not dark else 14),
        ]
        items: list[QGraphicsItem] = []
        for inset, dy, spread, alpha in layers:
            path = QPainterPath()
            rect = QRectF(
                x - spread / 2 - inset, y + dy - spread / 2, w + spread + 2 * inset, h + spread
            )
            path.addRoundedRect(rect, 3 + spread / 2, 3 + spread / 2)
            item = self._scene.addPath(
                path, QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0, alpha))
            )
            item.setZValue(-1)
            items.append(item)
        self._frame_items[page_num] = items

    def _reset_page_items(self) -> None:
        """按当前 zoom 重算页位置（水平居中），并把所有页恢复为骨架占位。"""
        for item in self._page_items.values():
            _remove_item(item)
        self._page_items.clear()
        for frame in self._frame_items.values():
            for item in frame:
                _remove_item(item)
        self._frame_items.clear()

        max_w = max((self._scaled_size(i)[0] for i in range(self._page_count)), default=0.0)
        self._page_y_positions = []
        self._page_x_positions = []
        total_height = 0.0
        for i in range(self._page_count):
            w = self._scaled_size(i)[0]
            self._page_x_positions.append((max_w - w) / 2)
            self._page_y_positions.append(total_height)
            self._make_frame(i)
            self._page_items[i] = self._make_placeholder(i, total_height)
            total_height += self._scaled_size(i)[1] + PAGE_GAP
        self._update_scene_rect(total_height)
        self._draw_marks()

    def _update_scene_rect(self, total_height: float) -> None:
        """场景矩形：上方留 TOP_PADDING、左右各留阴影余量。"""
        if self._page_count > 0:
            max_w = max(self._scaled_size(i)[0] for i in range(self._page_count))
            self._scene.setSceneRect(-16, -TOP_PADDING, max_w + 32, total_height + TOP_PADDING)
        else:
            self._scene.setSceneRect(0, 0, 0, 0)

    def _restore_placeholder(self, page_num: int) -> None:
        """把某页换回骨架占位（渲染缓存失效、等待重新渲染时使用）。"""
        if page_num < 0 or page_num >= len(self._page_y_positions):
            return
        old_item = self._page_items.pop(page_num, None)
        if old_item is not None:
            _remove_item(old_item)
        self._page_items[page_num] = self._make_placeholder(
            page_num, self._page_y_positions[page_num]
        )

    @property
    def zoom_percent(self) -> int:
        """显示用缩放百分比（100% = 实际大小 = 96 DPI）。"""
        return round(self._zoom / ACTUAL_SIZE_ZOOM * 100)

    def _manual_zoom(self, zoom: float) -> None:
        """用户手动指定缩放：退出适宽 / 适页，并取消尚未执行的适配重排（否则会被它改回去）。"""
        self._fit_mode = None
        self._fit_timer.stop()
        self.set_zoom(zoom)

    def zoom_in(self, factor: float = 1.25) -> None:
        self._manual_zoom(self._zoom * factor)

    def zoom_out(self, factor: float = 0.8) -> None:
        self._manual_zoom(self._zoom * factor)

    def set_zoom_percent(self, percent: float) -> None:
        """按显示百分比缩放（100% = 实际大小 96 DPI；状态栏滑杆用）。"""
        self._manual_zoom(percent / 100 * ACTUAL_SIZE_ZOOM)

    def fit_to_width(self) -> None:
        """适应视口宽度（随窗口变化保持）。"""
        self._fit_mode = "width"
        self._apply_fit()

    def fit_to_page(self) -> None:
        """整页适应视口（宽高都放得下）。"""
        self._fit_mode = "page"
        self._apply_fit()

    def set_actual_size(self) -> None:
        """实际大小（100% = 96 DPI）。"""
        self._manual_zoom(ACTUAL_SIZE_ZOOM)

    def _apply_fit(self) -> None:
        if self._page_count == 0 or not self._page_sizes or self._fit_mode is None:
            return
        page = self._current_page or 0
        w, h = self._page_sizes[page] if page < len(self._page_sizes) else self._page_sizes[0]
        # 两侧各留 24px（场景左右各有 16px 阴影留白，再多留一点避免横向滚动条）
        vw = max(50, self.viewport().width() - 48)
        vh = max(50, self.viewport().height() - TOP_PADDING - PAGE_GAP)
        if self._fit_mode == "page" and w > 0 and h > 0:
            self.set_zoom(min(vw / w, vh / h))
        elif w > 0:
            self.set_zoom(vw / w)

    # ------------------------------------------------------------------
    # 页码跳转
    # ------------------------------------------------------------------
    def jump_to_page(self, page_num: int, y: float | None = None, *, animate: bool = False) -> None:
        """滚动到指定页的页首（或页内纵坐标 ``y``，PDF 点）。

        旧实现把页面**中心**对到视口中心：页面比视口高时，翻页后看到的是页面中部。
        """
        if page_num < 0 or page_num >= self._page_count:
            return
        top = self._page_y_positions[page_num]
        if y is not None:
            top += max(0.0, y) * self._zoom - 3 * _TOP_MARGIN  # 目标行上方留一点上下文
        self._scroll_top_to(top - _TOP_MARGIN, animate=animate)

    # ------------------------------------------------------------------
    # 兼容阶段 3 的单页接口（保留 show_pixmap_data 供集成测试）
    # ------------------------------------------------------------------
    def show_pixmap(self, pixmap: QPixmap, page_num: int) -> None:
        """显示单页（阶段 3 兼容接口，内部转为单页文档）。"""
        self.setup_document(1, [(pixmap.width(), pixmap.height())])
        self._rendered_cache.put(0, pixmap)
        self._update_page_item(0, pixmap)

    def show_pixmap_data(self, data: PixmapData, page_num: int) -> None:
        """从 PixmapData 显示单页（阶段 3 兼容接口）。"""
        self.show_pixmap(self._to_qpixmap(data), page_num)

    def clear(self) -> None:
        """清空视图。"""
        self._scene.clear()
        self._page_items.clear()
        self._rendered_cache.clear()
        self._sel_items.clear()
        self._selected_text = ""
        self._sel_words = []
        self._sel_page = None
        self._sel_start = None
        self._selecting = False
        self._sel_anchor = None
        self._sel_focus = None
        self._sel_spans.clear()
        self._shift_click = False
        self._drag_scrolled = False
        self._last_drag_pos = None
        self._autoscroll_timer.stop()
        self._words_cache.clear()
        self._links_cache.clear()
        self._search_items.clear()
        self._mark_items.clear()
        self._marks.clear()
        self._annot_items.clear()
        self._ink_item = None
        self._ink_points = []
        self._hl_words = []
        self._annot_start = None
        self._annot_page = None
        self._annot_drawing = False
        self._page_count = 0
        self._page_sizes = []
        self._page_y_positions = []
        self._page_x_positions = []
        self._frame_items.clear()
        self._no_fade.clear()
        self._current_page = None
        self._fit_timer.stop()
        self._scene.setSceneRect(0, 0, 0, 0)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Ctrl + 滚轮 = 缩放；否则正常滚动
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self._fit_mode is not None:
            self._fit_timer.start()  # 适宽/适页：合并密集的 resize 事件后再重排
        else:
            self._update_current_page()
            self._request_visible_pages()


def _remove_item(item: QGraphicsItem) -> None:
    """安全地把场景项移出（场景已 clear 时项已被销毁，忽略）。"""
    try:
        scene = item.scene()
    except RuntimeError:
        return
    if scene is not None:
        scene.removeItem(item)


def _apply_color_matrix(
    data: PixmapData,
    matrix: tuple[tuple[float, ...], ...],
    offset: tuple[float, ...],
    brightness: float = 1.0,
) -> QImage:
    """对 RGB(A) 像素套用 3×3 颜色矩阵（numpy 向量化，整页约几十毫秒）。"""
    import numpy as np

    rows = np.frombuffer(data.samples, dtype=np.uint8).reshape(data.height, data.stride)
    pixels = rows[:, : data.width * data.n].reshape(data.height, data.width, data.n)
    rgb = pixels[..., :3].astype(np.float32) / 255.0
    m = np.asarray(matrix, dtype=np.float32)
    out = np.clip(rgb @ m.T + np.asarray(offset, dtype=np.float32), 0.0, 1.0) * brightness
    out = np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out = np.ascontiguousarray(out)
    image = QImage(out.data, data.width, data.height, data.width * 3, QImage.Format.Format_RGB888)
    detached: QImage = image.copy()  # 脱离 numpy 缓冲区
    return detached


def _pixmap_bytes(pixmap: object) -> int:
    """缓存条目占用的字节数（按 4 字节/像素估算）。"""
    if isinstance(pixmap, QPixmap):
        return int(pixmap.width()) * int(pixmap.height()) * 4
    return 0
