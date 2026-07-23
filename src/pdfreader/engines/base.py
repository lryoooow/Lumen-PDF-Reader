"""PDF 引擎抽象接口。

定义引擎层对外提供的**稳定契约**。上层（core/services/controllers）只依赖
:class:`PdfEngine` 协议，不感知具体是 PyMuPDF 还是其他实现。

这样设计的好处：
1. 更换/升级引擎时，上层代码无需改动
2. 上层测试可用 Mock 实现替代真实引擎
3. 引擎 API 变更被限制在实现类内部

阶段 1 仅定义**读取类**操作；编辑/批注类操作在后续阶段补充。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pdfreader.engines.types import (
    DocumentMeta,
    PageText,
    PixmapData,
    SearchResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["EngineDocument", "PdfEngine"]


class EngineDocument(Protocol):
    """引擎层文档句柄协议。

    表示一个已打开的文档。具体实现持有引擎原生文档对象（如 PyMuPDF 的 Document）。
    上层不应直接访问其内部原生对象，仅作为不透明句柄传递。
    """

    @property
    def path(self) -> Path | None: ...

    def close(self) -> None: ...


@runtime_checkable
class PdfEngine(Protocol):
    """PDF 引擎协议：定义引擎层提供的读取能力。

    实现类需提供以下方法。所有方法接收/返回的都是引擎无关的数据结构
    （见 :mod:`pdfreader.engines.types`），原生引擎对象不外泄。
    """

    def open(self, path: Path, password: str | None = None) -> EngineDocument:
        """打开 PDF 文件。

        Args:
            path: PDF 文件路径
            password: 加密 PDF 的用户密码（可选）

        Raises:
            FileOpenError: 文件不存在或无法打开
            PasswordRequiredError: 需要密码但未提供或密码错误
            CorruptPdfError: 文件损坏
        """
        ...

    def metadata(self, doc: EngineDocument) -> DocumentMeta:
        """获取文档元数据。"""
        ...

    def page_count(self, doc: EngineDocument) -> int:
        """获取文档总页数。"""
        ...

    def render_page(self, doc: EngineDocument, page_num: int, zoom: float = 1.0) -> PixmapData:
        """渲染指定页为位图。

        Args:
            doc: 已打开的文档
            page_num: 页码（从 0 开始）
            zoom: 缩放系数（1.0 = 72 DPI 原始尺寸）

        Raises:
            PageIndexError: 页码越界
            RenderError: 渲染失败
        """
        ...

    def extract_text(self, doc: EngineDocument, page_num: int) -> PageText:
        """提取指定页的文本。

        Raises:
            PageIndexError: 页码越界
        """
        ...

    def search(self, doc: EngineDocument, query: str) -> SearchResult:
        """全文搜索。

        返回所有命中（页码 + 矩形）。空查询返回空结果。

        Raises:
            SearchError: 搜索失败
        """
        ...

    def iter_pages_text(self, doc: EngineDocument) -> Iterator[PageText]:
        """遍历所有页的文本（用于全文索引等批量场景）。"""
        ...

    def close(self, doc: EngineDocument) -> None:
        """关闭文档，释放资源。"""
        ...
