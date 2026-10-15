"""批注控制器。

仅负责**当前批注模式**的状态与切换。实际的鼠标交互、实时预览与提交
由 :class:`~pdfreader.gui.widgets.pdf_view.PdfView` 自行处理，批注写入经
:class:`~pdfreader.controllers.document_controller.DocumentController`。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

__all__ = ["AnnotationController", "AnnotationMode"]


class AnnotationMode:
    """工具模式常量（含批注与导航工具）。"""

    NONE = "none"  # 浏览 / 文本选择
    PAN = "pan"  # 抓手平移
    HIGHLIGHT = "highlight"  # 高亮
    UNDERLINE = "underline"  # 下划线（拖拽选字）
    INK = "ink"  # 手写
    TEXT = "text"  # 便签
    FREETEXT = "freetext"  # 文本框（文字直接显示在页面上）
    ERASER = "eraser"  # 橡皮擦（点删批注）
    SIGNATURE = "signature"  # 放置签名（单击页面）
    TIMESTAMP = "timestamp"  # 放置日期时间（单击页面）
    CAPTURE = "capture"  # 框选区域（公式 / 图表）交给 AI 解释


class AnnotationController(QObject):
    """批注模式状态机。

    Signals:
        modeChanged(str): 批注模式变化（AnnotationMode 常量）
    """

    modeChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode: str = AnnotationMode.NONE

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_active(self) -> bool:
        """是否处于批注模式（非浏览/选择模式）。"""
        return self._mode != AnnotationMode.NONE

    def set_mode(self, mode: str) -> None:
        """切换批注模式；变化时发 modeChanged。"""
        if self._mode != mode:
            self._mode = mode
            self.modeChanged.emit(mode)
