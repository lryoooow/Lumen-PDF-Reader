"""业务核心层（框架无关的纯 Python 模型）。

定义 :class:`Document`、:class:`Page`、:class:`OutlineItem` 等领域对象。
**铁律**：本层绝不 ``import PySide6`` 或 ``import pymupdf``，
通过依赖注入持有 :class:`~pdfreader.engines.base.PdfEngine` 接口，
从而可脱离 GUI 独立单元测试。
"""

from __future__ import annotations

from pdfreader.core.document import Document, DocumentState
from pdfreader.core.outline import OutlineItem, empty_outline
from pdfreader.core.page import Page

__all__ = [
    "Document",
    "DocumentState",
    "OutlineItem",
    "Page",
    "empty_outline",
]
