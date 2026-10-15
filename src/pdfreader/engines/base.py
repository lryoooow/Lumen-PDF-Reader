"""PDF 引擎抽象接口。

定义引擎层对外提供的**稳定契约**。上层（core/services/controllers）只依赖
:class:`PdfEngine` 协议，不感知具体是 PyMuPDF 还是其他实现。

这样设计的好处：
1. 更换/升级引擎时，上层代码无需改动
2. 上层测试可用 Mock 实现替代真实引擎
3. 引擎 API 变更被限制在实现类内部

坐标约定：所有矩形 / 点一律是**可见帧**坐标（``page_bounds`` 所在坐标系，已应用 /Rotate）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pdfreader.engines.types import (
    Annotation,
    DocumentMeta,
    InkStroke,
    LinkInfo,
    PageText,
    PixmapData,
    Point,
    Rect,
    SearchResult,
    TocEntry,
    WordBox,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

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

    def page_bounds(self, doc: EngineDocument, page_num: int) -> Rect:
        """获取指定页的边界框（尺寸），返回 **可见帧**（已应用 /Rotate）的 Rect。

        Raises:
            PageIndexError: 页码越界
        """
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

    def pixmap_to_png(self, data: PixmapData) -> bytes:
        """把渲染结果编码为 PNG 字节。

        供不依赖 GUI（Qt）的消费方使用，例如 OCR 需要图片字节而非原始像素。
        """
        ...

    def images_to_pdf(self, image_paths: Sequence[Path], output_path: Path) -> int:
        """把若干图片合成一个 PDF（每张图一页），返回页数。

        Raises:
            FileOpenError: 写入失败
        """
        ...

    def get_words(self, doc: EngineDocument, page_num: int) -> tuple[WordBox, ...]:
        """获取指定页的词及其坐标（按阅读顺序，用于文本选择/复制）。

        坐标一律是**可见帧**坐标：与 ``page_bounds`` / ``render_page`` 同一套
        （已应用 /Rotate），上层可直接与鼠标位置比较。

        Raises:
            PageIndexError: 页码越界
        """
        ...

    def search(
        self,
        doc: EngineDocument,
        query: str,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> SearchResult:
        """全文搜索。

        返回所有命中（页码 + 矩形，矩形为**可见帧**坐标）。空查询返回空结果。

        Args:
            cancel: 可选取消回调，逐页调用；返回 True 时立刻停止并返回**已找到的
                部分结果**（供后台线程"取消后不再使用结果"的语义使用）。

        Raises:
            SearchError: 搜索失败
        """
        ...

    def iter_pages_text(self, doc: EngineDocument) -> Iterator[PageText]:
        """遍历所有页的文本（用于全文索引等批量场景）。"""
        ...

    # ------------------------------------------------------------------
    # 编辑操作（阶段 6）
    # ------------------------------------------------------------------
    def delete_page(self, doc: EngineDocument, page_num: int) -> None:
        """删除指定页。

        Raises:
            PageIndexError: 页码越界
        """
        ...

    def rotate_page(self, doc: EngineDocument, page_num: int, degrees: int) -> None:
        """旋转指定页（degrees 为 90/180/270，可叠加）。

        Raises:
            PageIndexError: 页码越界
        """
        ...

    def merge(self, doc: EngineDocument, other_path: Path) -> int:
        """将另一个 PDF 的所有页追加到当前文档末尾。

        Returns:
            追加的页数
        """
        ...

    def add_text_watermark(
        self,
        doc: EngineDocument,
        page_num: int | Sequence[int],
        text: str,
        *,
        opacity: float = ...,
    ) -> None:
        """给一页或多页添加居中半透明文字水印（多页为一次可撤销操作）。

        Raises:
            PageIndexError: 页码越界
        """
        ...

    def save(self, doc: EngineDocument, path: Path, *, incremental: bool = False) -> None:
        """保存文档。

        目标是打开时的原文件时一律增量保存（无论 ``incremental``）；
        原文件在打开时被修复过、无法增量保存时抛 ``FileOpenError`` 并说明原因。

        Args:
            incremental: 调用方意图「存回原文件」

        Raises:
            FileOpenError: 保存失败
        """
        ...

    def close(self, doc: EngineDocument) -> None:
        """关闭文档，释放资源。"""
        ...

    # ------------------------------------------------------------------
    # 批注操作（阶段 7）
    # ------------------------------------------------------------------
    def add_highlight(
        self,
        doc: EngineDocument,
        page_num: int,
        rect: Rect | Sequence[Rect],
        *,
        color: tuple[float, float, float] | None = None,
    ) -> None:
        """添加高亮；多个矩形（多行）合成**一条**批注。

        Raises:
            PageIndexError: 页码越界
        """
        ...

    def add_markup(
        self,
        doc: EngineDocument,
        page_num: int,
        rects: Rect | Sequence[Rect],
        *,
        kind: str = "highlight",
        color: tuple[float, float, float] | None = None,
    ) -> None:
        """添加文字标记：``kind`` 为 highlight / underline / strikeout / squiggly。"""
        ...

    def set_annotation_content(
        self, doc: EngineDocument, page_num: int, index: int, text: str
    ) -> None:
        """修改第 index 个批注的笔记内容（可撤销）。"""
        ...

    def add_ink(self, doc: EngineDocument, page_num: int, strokes: tuple[InkStroke, ...]) -> None:
        """给指定页添加手写笔迹批注（笔画坐标为可见帧坐标）。"""
        ...

    def add_text_annot(self, doc: EngineDocument, page_num: int, point: Point, text: str) -> None:
        """给指定页添加文本便签批注（``point`` 为可见帧坐标）。"""
        ...

    def add_free_text(
        self,
        doc: EngineDocument,
        page_num: int,
        rect: Rect,
        text: str,
        *,
        fill: tuple[float, float, float] | None = None,
    ) -> None:
        """在可见帧矩形处添加文本框（FreeText）批注，正文直接显示在页面上。"""
        ...

    def list_annotations(self, doc: EngineDocument, page_num: int) -> list[Annotation]:
        """列出指定页的所有批注（可见帧坐标；文字标记类附带被标记的原文）。"""
        ...

    def delete_annotation(self, doc: EngineDocument, page_num: int, index: int) -> bool:
        """删除指定页的第 index 个批注。序号不存在返回 False，引擎出错抛 PdfEngineError。"""
        ...

    # ------------------------------------------------------------------
    # 目录与链接
    # ------------------------------------------------------------------
    def get_toc(self, doc: EngineDocument) -> tuple[TocEntry, ...]:
        """文档目录（书签 / 大纲）；没有目录时返回空元组。"""
        ...

    def get_links(self, doc: EngineDocument, page_num: int) -> tuple[LinkInfo, ...]:
        """页面上的内部跳转与外部网址链接（热区为可见帧坐标）。"""
        ...

    def annotation_index_at(self, doc: EngineDocument, page_num: int, point: Point) -> int | None:
        """返回点（可见帧坐标）命中的最上层批注索引；无则 None。"""
        ...

    def insert_image(
        self, doc: EngineDocument, page_num: int, rect: Rect, image_path: Path
    ) -> None:
        """在页面矩形处插入图片（``rect`` 为可见帧坐标；签名 / 图片）。"""
        ...

    def insert_text(
        self, doc: EngineDocument, page_num: int, point: Point, text: str, *, fontsize: int = 12
    ) -> None:
        """在页面指定点插入文字（``point`` 为可见帧坐标；日期 / 时间戳）。"""
        ...

    # ------------------------------------------------------------------
    # 撤销 / 重做
    # ------------------------------------------------------------------
    def undo(self, doc: EngineDocument) -> bool:
        """撤销上一步。"""
        ...

    def redo(self, doc: EngineDocument) -> bool:
        """重做。"""
        ...

    def can_undo(self, doc: EngineDocument) -> bool:
        """是否有可撤销的操作。"""
        ...

    def can_redo(self, doc: EngineDocument) -> bool:
        """是否有可重做的操作。"""
        ...

    def history_position(self, doc: EngineDocument) -> int:
        """当前在撤销历史中的位置（0 = 打开时/已回到最初的状态）。

        上层用它判断「撤销回上次保存的状态后文档是否还算脏」；不支持历史
        （如合并后重建的文档、非 PDF）时返回 0。
        """
        ...
