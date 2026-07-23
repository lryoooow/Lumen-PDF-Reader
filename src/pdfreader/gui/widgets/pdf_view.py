"""PDF 单页渲染视图。

阶段 3：基于 QGraphicsView 显示单页 PDF。
渲染在**主线程**完成（阶段 0-3 先跑通链路；阶段 4 引入渲染线程优化性能）。

后续阶段扩展：
- 阶段 4：连续滚动（QGraphicsScene 多页 + 按需渲染 + LRU 缓存）
- 阶段 5：搜索高亮、文本选择
- 阶段 7：批注叠层
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

__all__ = ["PdfView"]


class PdfView(QGraphicsView):
    """单页 PDF 显示视图。

    接收 :class:`PixmapData` 或 :class:`QPixmap`，居中显示并适配窗口。
    """

    pageRendered = Signal(int)  # 页码已渲染

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # 视图配置：抗锯齿、平滑缩放、可拖拽
        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._current_page_num: int | None = None

    @property
    def current_page_num(self) -> int | None:
        """当前显示的页码（0-based），未显示时为 None。"""
        return self._current_page_num

    def show_pixmap(self, pixmap: QPixmap, page_num: int) -> None:
        """显示一个 QPixmap（页码为 page_num）。"""
        self._scene.clear()
        self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self._current_page_num = page_num
        self.pageRendered.emit(page_num)

    def show_pixmap_data(self, data, page_num: int) -> None:  # type: ignore[no-untyped-def]
        """从 PixmapData 渲染并显示（便捷方法）。"""
        qpixmap = pixmap_to_qpixmap(data)
        self.show_pixmap(qpixmap, page_num)

    def clear(self) -> None:
        """清空显示。"""
        self._scene.clear()
        self._current_page_num = None

    def zoom_in(self, factor: float = 1.25) -> None:
        """放大。"""
        self.scale(factor, factor)

    def zoom_out(self, factor: float = 0.8) -> None:
        """缩小。"""
        self.scale(factor, factor)

    def fit_to_width(self) -> None:
        """适应宽度。"""
        if self._scene.items():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def reset_zoom(self) -> None:
        """重置缩放为 100%。"""
        self.resetTransform()
