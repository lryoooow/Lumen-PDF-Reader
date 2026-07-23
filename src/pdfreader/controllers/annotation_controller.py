"""批注控制器。

协调 GUI 与业务层处理批注操作：切换批注模式、捕获鼠标轨迹、
转换为 PDF 坐标、调用文档创建批注。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QPointF, Signal

from pdfreader.engines.types import InkStroke, Point, Rect

__all__ = ["AnnotationController", "AnnotationMode"]


class AnnotationMode:
    """批注模式常量。"""

    NONE = "none"  # 普通浏览（拖拽滚动）
    HIGHLIGHT = "highlight"  # 高亮（拖选矩形）
    INK = "ink"  # 手写（鼠标轨迹）
    TEXT = "text"  # 便签（点击位置）


@dataclass
class AnnotationState:
    """批注交互状态。"""

    mode: str = AnnotationMode.NONE
    is_drawing: bool = False
    start_point: QPointF | None = None
    current_stroke: list[QPointF] = field(default_factory=list)

    def reset(self) -> None:
        self.is_drawing = False
        self.start_point = None
        self.current_stroke.clear()


class AnnotationController(QObject):
    """批注控制器。

    接收视图层的鼠标事件（经坐标转换后的 PDF 点坐标），
    根据当前模式累积轨迹，完成时发信号请求创建批注。

    Signals:
        highlightRequested(int, Rect): 请求在某页创建高亮 (page_num, rect)
        inkRequested(int, InkStroke): 请求在某页创建手写
        textRequested(int, Point): 请求在某页点击位置创建便签
        modeChanged(str): 批注模式变化
    """

    highlightRequested = Signal(int, object)  # page_num, Rect
    inkRequested = Signal(int, object)  # page_num, InkStroke
    textRequested = Signal(int, object)  # page_num, Point
    modeChanged = Signal(str)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._state = AnnotationState()

    @property
    def mode(self) -> str:
        return self._state.mode

    @property
    def is_active(self) -> bool:
        """是否处于批注模式（非浏览模式）。"""
        return self._state.mode != AnnotationMode.NONE

    def set_mode(self, mode: str) -> None:
        """切换批注模式。"""
        if self._state.mode != mode:
            self._state.reset()
            self._state.mode = mode
            self.modeChanged.emit(mode)

    # ------------------------------------------------------------------
    # 鼠标事件处理（由 PdfView 转发，参数为 PDF 点坐标）
    # ------------------------------------------------------------------
    def on_press(self, page_num: int, pdf_point: QPointF) -> None:
        if self._state.mode == AnnotationMode.NONE:
            return
        self._state.is_drawing = True
        self._state.start_point = pdf_point
        if self._state.mode == AnnotationMode.INK:
            self._state.current_stroke = [pdf_point]
        elif self._state.mode == AnnotationMode.TEXT:
            # 便签：单击即完成
            self.textRequested.emit(page_num, Point(pdf_point.x(), pdf_point.y()))
            self._state.is_drawing = False

    def on_move(self, page_num: int, pdf_point: QPointF) -> None:
        if not self._state.is_drawing:
            return
        if self._state.mode == AnnotationMode.INK:
            self._state.current_stroke.append(pdf_point)

    def on_release(self, page_num: int, pdf_point: QPointF) -> None:
        if not self._state.is_drawing:
            return
        start = self._state.start_point
        if start is None:
            self._state.reset()
            return

        if self._state.mode == AnnotationMode.HIGHLIGHT:
            rect = Rect(
                min(start.x(), pdf_point.x()),
                min(start.y(), pdf_point.y()),
                max(start.x(), pdf_point.x()),
                max(start.y(), pdf_point.y()),
            )
            # 忽略过小的选择（误触）
            if rect.width > 3 and rect.height > 3:
                self.highlightRequested.emit(page_num, rect)
        elif self._state.mode == AnnotationMode.INK:
            if len(self._state.current_stroke) >= 2:
                points = tuple(Point(p.x(), p.y()) for p in self._state.current_stroke)
                self.inkRequested.emit(page_num, InkStroke(points=points))

        self._state.reset()

    def cancel(self) -> None:
        """取消当前绘制。"""
        self._state.reset()
