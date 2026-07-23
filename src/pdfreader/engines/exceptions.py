"""引擎层异常体系。

所有 PDF 引擎相关错误都继承自 :class:`PdfEngineError`，
上层可按异常类型精确处理（如加密文件提示输入密码、损坏文件提示无法打开），
而不必关心底层是 PyMuPDF 还是其他引擎。
"""

from __future__ import annotations

__all__ = [
    "CorruptPdfError",
    "FileOpenError",
    "PageIndexError",
    "PasswordRequiredError",
    "PdfEngineError",
    "RenderError",
    "SearchError",
]


class PdfEngineError(Exception):
    """所有 PDF 引擎错误的基类。"""


class FileOpenError(PdfEngineError):
    """文件打开失败（路径不存在、权限不足等）。"""


class PasswordRequiredError(FileOpenError):
    """PDF 需要密码才能打开。

    属性 ``needs_password`` 表示该文档是否仍处于加密锁定状态。
    """

    def __init__(self, message: str = "PDF 需要密码", *, needs_password: bool = True) -> None:
        super().__init__(message)
        self.needs_password = needs_password


class CorruptPdfError(FileOpenError):
    """PDF 文件已损坏或不是合法 PDF。"""


class PageIndexError(PdfEngineError):
    """页码越界（请求了不存在的页）。"""

    def __init__(self, page_num: int, page_count: int) -> None:
        super().__init__(
            f"页码越界：请求第 {page_num} 页，但文档共 {page_count} 页（有效范围 0..{page_count - 1}）"
        )
        self.page_num = page_num
        self.page_count = page_count


class RenderError(PdfEngineError):
    """页面渲染失败。"""


class SearchError(PdfEngineError):
    """文本搜索失败。"""
