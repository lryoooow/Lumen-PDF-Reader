"""PDF 多页连续滚动视图。

阶段 4：在 QGraphicsScene 上纵向排列所有页，配合渲染线程按需渲染，
配合 LRU 缓存避免重复渲染。这是滚动浏览的核心。

工作流：
1. 文档打开后，先计算所有页的占位矩形（无需渲染，只需尺寸）
2. 视口变化时，检测哪些页可见，请求渲染线程渲染
3. 渲染线程返回 PixmapData，转 QPixmap 后填入对应占位
4. LRU 缓存已渲染页，避免重复渲染

PyMuPDF 非线程安全：Document 仅存在于 RenderWorker 线程内，
主线程通过信号收发不可变数据。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

from pdfreader.engines.types import PixmapData
from pdfreader.utils.cache import LRUCache
from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

__all__ = ["PAGE_GAP", "PdfView"]

# 页与页之间的垂直间距（像素）
PAGE_GAP = 12
# 默认 LRU 缓存容量（渲染页数）
DEFAULT_CACHE_SIZE = 30


class PdfView(QGraphicsView):
    """多页连续滚动 PDF 视图。

    Signals:
        pageChanged(int): 当前可见页变化（滚动到某页）
        renderRequested(int): 请求渲染某页
    """

    pageChanged = Signal(int)
    renderRequested = Signal(int, float)  # page_num, zoom

    def __init__(self, parent=None, *, cache_size: int = DEFAULT_CACHE_SIZE) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # 文档状态
        self._page_count: int = 0
        self._zoom: float = 1.5
        self._page_sizes: list[tuple[int, int]] = []  # 各页原始尺寸 (w, h) @ 72dpi
        self._page_y_positions: list[float] = []  # 各页顶部 y 坐标
        self._page_items: dict[int, QGraphicsItem] = {}  # 页码 → 场景项
        self._rendered_cache: LRUCache = LRUCache(capacity=cache_size)
        self._current_page: int | None = None
        self._placeholder_pixmap: QPixmap | None = None

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
    def setup_document(self, page_count: int, page_sizes: list[tuple[int, int]]) -> None:
        """设置文档：页数 + 各页原始尺寸。计算占位布局。

        Args:
            page_count: 总页数
            page_sizes: 每页原始尺寸 (width, height) @ 72dpi 的列表
        """
        self._scene.clear()
        self._page_items.clear()
        self._rendered_cache.clear()
        self._page_count = page_count
        self._page_sizes = list(page_sizes)
        self._current_page = None

        # 计算各页位置并添加占位矩形
        self._page_y_positions = []
        y = 0.0
        for i in range(page_count):
            self._page_y_positions.append(y)
            w, h = self._scaled_size(i)
            placeholder = self._scene.addRect(QRectF(0, y, w, h), pen=Qt.PenStyle.NoPen)
            # 灰色背景占位
            placeholder.setBrush(QBrush(QColor(230, 230, 230)))
            self._page_items[i] = placeholder
            y += h + PAGE_GAP

        # 场景矩形
        if page_count > 0:
            max_w = max(self._scaled_size(i)[0] for i in range(page_count))
            self._scene.setSceneRect(0, 0, max_w, y)
        else:
            self._scene.setSceneRect(0, 0, 0, 0)

        # 渲染初始可见页
        self._request_visible_pages()

    def _scaled_size(self, page_num: int) -> tuple[float, float]:
        """计算某页缩放后的尺寸。"""
        if page_num >= len(self._page_sizes):
            return (595 * self._zoom, 842 * self._zoom)  # A4 默认
        w, h = self._page_sizes[page_num]
        return (w * self._zoom, h * self._zoom)

    # ------------------------------------------------------------------
    # 渲染结果接收（由主窗口从渲染线程信号转发）
    # ------------------------------------------------------------------
    def on_page_rendered(self, page_num: int, zoom: float, data: PixmapData) -> None:
        """渲染线程返回结果后调用：转 QPixmap 并填入对应占位。

        若 zoom 与当前不一致（缩放期间），丢弃过时结果。
        """
        if zoom != self._zoom:
            return  # 过时结果
        if page_num < 0 or page_num >= self._page_count:
            return

        qpixmap = pixmap_to_qpixmap(data)
        self._rendered_cache.put(page_num, qpixmap)
        self._update_page_item(page_num, qpixmap)

    def _update_page_item(self, page_num: int, pixmap: QPixmap) -> None:
        """用渲染好的 pixmap 替换占位项。"""
        if page_num not in self._page_items:
            return
        # 移除旧项，添加 pixmap 项
        old_item = self._page_items[page_num]
        self._scene.removeItem(old_item)
        y = self._page_y_positions[page_num]
        w, h = self._scaled_size(page_num)
        new_item = self._scene.addPixmap(pixmap)
        new_item.setOffset(0, y)
        # 若 pixmap 尺寸与占位不一致，缩放对齐
        if abs(pixmap.width() - w) > 1 or abs(pixmap.height() - h) > 1:
            new_item.setScale(w / pixmap.width() if pixmap.width() else 1.0)
        self._page_items[page_num] = new_item

    # ------------------------------------------------------------------
    # 按需渲染
    # ------------------------------------------------------------------
    def _request_visible_pages(self) -> None:
        """检测可见页并请求渲染（未缓存的部分）。"""
        viewport_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        for page_num in self._visible_pages(viewport_rect):
            if not self._rendered_cache.contains(page_num):
                self.renderRequested.emit(page_num, self._zoom)

    def _visible_pages(self, viewport_rect: QRectF) -> list[int]:
        """返回与视口相交的页码列表。"""
        result: list[int] = []
        for i in range(self._page_count):
            y = self._page_y_positions[i]
            _, h = self._scaled_size(i)
            page_rect = QRectF(0, y, 100, h)  # 宽度不影响可见性判断
            if viewport_rect.intersects(page_rect):
                result.append(i)
        return result

    # ------------------------------------------------------------------
    # 滚动事件：更新当前页 + 触发按需渲染
    # ------------------------------------------------------------------
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._update_current_page()
        self._request_visible_pages()

    def _update_current_page(self) -> None:
        """根据视口中心更新当前页码。"""
        center = self.mapToScene(self.viewport().rect().center())
        for i in range(self._page_count):
            y = self._page_y_positions[i]
            _, h = self._scaled_size(i)
            if y <= center.y() <= y + h:
                if self._current_page != i:
                    self._current_page = i
                    self.pageChanged.emit(i)
                return
        # 视口在所有页之前/之后
        if (
            self._page_count > 0
            and center.y() < self._page_y_positions[0]
            and self._current_page != 0
        ):
            self._current_page = 0
            self.pageChanged.emit(0)

    # ------------------------------------------------------------------
    # 缩放
    # ------------------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        """设置缩放并重新布局。

        缩放后所有缓存失效（尺寸变了），需重新渲染可见页。
        """
        if zoom <= 0:
            return
        # 记住当前页，缩放后跳回
        current = self._current_page or 0
        self._zoom = zoom
        if self._page_count > 0:
            self._rebuild_layout()
            self._rendered_cache.clear()
            # 跳到当前页
            self.jump_to_page(current)
            self._request_visible_pages()

    def _rebuild_layout(self) -> None:
        """zoom 改变后重新计算所有页位置（不重新渲染，只更新占位尺寸）。"""
        y = 0.0
        new_positions: list[float] = []
        for i in range(self._page_count):
            new_positions.append(y)
            _w, h = self._scaled_size(i)
            y += h + PAGE_GAP
        self._page_y_positions = new_positions
        if self._page_count > 0:
            max_w = max(self._scaled_size(i)[0] for i in range(self._page_count))
            self._scene.setSceneRect(0, 0, max_w, y)

    def zoom_in(self, factor: float = 1.25) -> None:
        self.set_zoom(self._zoom * factor)

    def zoom_out(self, factor: float = 0.8) -> None:
        self.set_zoom(self._zoom * factor)

    def fit_to_width(self) -> None:
        """适应视口宽度。"""
        if self._page_count == 0:
            return
        viewport_w = self.viewport().width() - 20
        # 用第一页宽度计算（多数 PDF 各页宽度一致）
        w, _ = self._page_sizes[0] if self._page_sizes else (595, 842)
        if w > 0:
            self.set_zoom(viewport_w / w)

    def reset_zoom(self) -> None:
        self.set_zoom(1.0)

    # ------------------------------------------------------------------
    # 页码跳转
    # ------------------------------------------------------------------
    def jump_to_page(self, page_num: int) -> None:
        """滚动到指定页顶部。"""
        if page_num < 0 or page_num >= self._page_count:
            return
        y = self._page_y_positions[page_num]
        self.centerOn(0, y + self._scaled_size(page_num)[1] / 2)

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
        qpixmap = pixmap_to_qpixmap(data)
        self.show_pixmap(qpixmap, page_num)

    def clear(self) -> None:
        """清空视图。"""
        self._scene.clear()
        self._page_items.clear()
        self._rendered_cache.clear()
        self._page_count = 0
        self._page_sizes = []
        self._page_y_positions = []
        self._current_page = None
        self._scene.setSceneRect(0, 0, 0, 0)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._request_visible_pages()
