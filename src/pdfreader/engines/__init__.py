"""引擎适配层。

封装 PyMuPDF 的访问，是**整个项目唯一 ``import pymupdf`` 的地方**。
上层（core/services/controllers）只依赖这里定义的稳定接口，
将来更换或升级引擎时只需修改本目录。

公共 API：
- :class:`PyMuPdfEngine` —— 默认引擎实现
- :class:`PdfEngine` —— 引擎协议（用于类型注解/Mock）
- 数据结构与异常见 :mod:`pdfreader.engines.types` / :mod:`pdfreader.engines.exceptions`
"""

from __future__ import annotations

from pdfreader.engines.base import EngineDocument, PdfEngine
from pdfreader.engines.exceptions import (
    CorruptPdfError,
    FileOpenError,
    PageIndexError,
    PasswordRequiredError,
    PdfEngineError,
    RenderError,
    SearchError,
)
from pdfreader.engines.pdf_engine import PyMuPdfDocument, PyMuPdfEngine
from pdfreader.engines.types import (
    DocumentMeta,
    PageText,
    PixmapData,
    Point,
    Rect,
    SearchHit,
    SearchResult,
    TextSpan,
)

__all__ = [
    "CorruptPdfError",
    # 数据结构
    "DocumentMeta",
    "EngineDocument",
    "FileOpenError",
    "PageIndexError",
    "PageText",
    "PasswordRequiredError",
    # 引擎与协议
    "PdfEngine",
    # 异常
    "PdfEngineError",
    "PixmapData",
    "Point",
    "PyMuPdfDocument",
    "PyMuPdfEngine",
    "Rect",
    "RenderError",
    "SearchError",
    "SearchHit",
    "SearchResult",
    "TextSpan",
]
