"""文档控制器。

协调 GUI 与业务层：接收用户动作（打开文件），调用引擎 + core 完成工作，
刷新视图。不含具体渲染/处理实现，避免 GUI 与引擎直接耦合。

阶段 3：打开文件 + 单页渲染。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from pdfreader.core import Document
from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.exceptions import (
    CorruptPdfError,
    FileOpenError,
    PasswordRequiredError,
)

__all__ = ["DocumentController", "OpenResult"]


class OpenResult:
    """打开文件的结果分类（用于 GUI 给出精确反馈）。"""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    CORRUPT = "corrupt"
    NEEDS_PASSWORD = "needs_password"
    UNKNOWN_ERROR = "unknown_error"


class DocumentController(QObject):
    """管理当前打开的文档及其渲染。

    Signals:
        documentOpened(int): 文档成功打开，发送页数
        documentClosed(): 文档已关闭
        openFailed(str, str): 打开失败，发送 (结果分类, 错误消息)
        pageRendered(int): 某页已渲染，发送页码
    """

    documentOpened = Signal(int)  # page_count
    documentClosed = Signal()
    openFailed = Signal(str, str)  # (OpenResult 分类, 消息)
    pageRendered = Signal(int)  # page_num

    def __init__(self, engine=None, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        # 默认使用 PyMuPDF 引擎；可注入用于测试
        self._engine = engine if engine is not None else PyMuPdfEngine()
        self._document: Document | None = None
        self._current_page: int = 0

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def has_document(self) -> bool:
        return self._document is not None and not self._document.is_closed

    @property
    def document(self) -> Document | None:
        return self._document

    @property
    def page_count(self) -> int:
        return self._document.page_count if self._document else 0

    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def title(self) -> str:
        return self._document.title if self._document else ""

    # ------------------------------------------------------------------
    # 打开 / 关闭
    # ------------------------------------------------------------------
    def open_file(self, path: Path, password: str | None = None) -> bool:
        """打开 PDF 文件。

        Returns:
            True 表示成功打开（documentOpened 信号已发）；
            False 表示失败（openFailed 信号已发）。
        """
        try:
            doc = Document.open(self._engine, path, password=password)
        except CorruptPdfError as exc:
            self.openFailed.emit(OpenResult.CORRUPT, str(exc))
            return False
        except PasswordRequiredError as exc:
            self.openFailed.emit(OpenResult.NEEDS_PASSWORD, str(exc))
            return False
        except FileOpenError as exc:
            # FileOpenError 是基类；CorruptPdf/PasswordRequired 已在上面处理。
            # 这里处理普通打开失败（如文件不存在）。
            result = OpenResult.NOT_FOUND if "不存在" in str(exc) else OpenResult.UNKNOWN_ERROR
            self.openFailed.emit(result, str(exc))
            return False
        except Exception as exc:
            self.openFailed.emit(OpenResult.UNKNOWN_ERROR, f"打开失败：{exc}")
            return False

        # 关闭旧文档
        self.close_document()
        self._document = doc
        self._current_page = 0
        self.documentOpened.emit(doc.page_count)
        return True

    def close_document(self) -> None:
        """关闭当前文档（如有）。"""
        if self._document is not None and not self._document.is_closed:
            self._document.close()
        self._document = None
        self._current_page = 0
        self.documentClosed.emit()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render_page(self, page_num: int, zoom: float = 1.0):  # type: ignore[no-untyped-def]
        """渲染指定页，返回 PixmapData。

        Raises:
            RuntimeError: 未打开文档
            IndexError: 页码越界
        """
        if self._document is None or self._document.is_closed:
            raise RuntimeError("未打开文档，无法渲染。")
        # 越界检查
        if page_num < 0 or page_num >= self._document.page_count:
            raise IndexError(f"页码越界：{page_num}，有效范围 0..{self._document.page_count - 1}")
        self._current_page = page_num
        return self._document.render_page(page_num, zoom)

    def get_page_bounds(self):  # type: ignore[no-untyped-def]
        """获取所有页的边界框列表（Rect，用于视图布局）。"""
        if self._document is None or self._document.is_closed:
            return []
        return self._document.get_all_page_bounds()

    def get_page_sizes(self) -> list[tuple[int, int]]:
        """获取所有页的原始尺寸 (width, height) @ 72dpi（用于视图布局）。"""
        if self._document is None or self._document.is_closed:
            return []
        bounds = self._document.get_all_page_bounds()
        return [(int(b.width), int(b.height)) for b in bounds]
