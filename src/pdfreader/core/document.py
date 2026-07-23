"""文档领域模型。

:class:`Document` 是核心领域对象，封装一个已打开 PDF 的全部业务状态：
元数据、页集合、修改标记。它通过依赖注入持有 :class:`PdfEngine` 协议，
不直接 import pymupdf，因此可脱离 GUI 独立单元测试。

设计要点：
- 引擎注入：``Document.open(engine, path)`` 工厂方法，使 Document 与具体引擎解耦
- 页懒加载：Page 对象在首次访问时按需创建并绑定引擎
- 修改跟踪：``is_modified`` 标记文档是否有未保存变更（阶段 6 用）
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from pdfreader.core.page import Page
from pdfreader.engines.base import EngineDocument, PdfEngine
from pdfreader.engines.exceptions import PdfEngineError
from pdfreader.engines.types import (
    Annotation,
    DocumentMeta,
    InkStroke,
    PixmapData,
    Point,
    Rect,
    SearchResult,
)

__all__ = ["Document", "DocumentState"]


class DocumentState:
    """文档生命周期状态常量。"""

    CLOSED = "closed"
    READY = "ready"
    MODIFIED = "modified"
    ERROR = "error"


@dataclass
class Document:
    """PDF 文档领域模型。

    通过 ``Document.open(engine, path)`` 创建实例。实例持有引擎与原生文档句柄，
    提供对元数据、页、搜索的访问，并跟踪修改状态。

    Attributes:
        path: 文件路径
        meta: 文档元数据
    """

    path: Path
    meta: DocumentMeta
    _engine: PdfEngine = field(repr=False)
    _handle: EngineDocument | None = field(default=None, repr=False)
    _is_modified: bool = field(default=False, init=False)
    _pages: dict[int, Page] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------
    @classmethod
    def open(
        cls,
        engine: PdfEngine,
        path: Path,
        password: str | None = None,
    ) -> Document:
        """用给定引擎打开 PDF，返回 Document 实例。

        Raises:
            PdfEngineError 及子类：打开失败（见 engines.exceptions）
        """
        handle = engine.open(path, password=password)
        meta = engine.metadata(handle)
        return cls(path=path, meta=meta, _engine=engine, _handle=handle)

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------
    @property
    def page_count(self) -> int:
        """总页数。"""
        return self.meta.page_count

    @property
    def is_modified(self) -> bool:
        """文档是否有未保存的修改。"""
        return self._is_modified

    @property
    def is_closed(self) -> bool:
        """文档是否已关闭。"""
        return self._handle is None

    @property
    def title(self) -> str:
        """文档标题（元数据），无标题时回退到文件名。"""
        return self.meta.title or self.path.stem

    # ------------------------------------------------------------------
    # 页访问
    # ------------------------------------------------------------------
    def get_page(self, page_num: int) -> Page:
        """获取指定页的模型（懒创建并缓存）。

        Raises:
            IndexError: 页码越界
        """
        self._ensure_open()
        if page_num < 0 or page_num >= self.page_count:
            raise IndexError(f"页码越界：{page_num}，有效范围 0..{self.page_count - 1}")
        if page_num not in self._pages:
            assert self._handle is not None  # _ensure_open 保证
            page = Page(page_num=page_num)
            page.bind(self._engine, self._handle)
            self._pages[page_num] = page
        return self._pages[page_num]

    def iter_pages(self) -> Iterator[Page]:
        """遍历所有页。"""
        for i in range(self.page_count):
            yield self.get_page(i)

    def invalidate_page_cache(self, page_num: int | None = None) -> None:
        """使页缓存失效。

        Args:
            page_num: 指定页；None 表示清除全部页缓存（用于整体重载）
        """
        if page_num is None:
            for page in self._pages.values():
                page.invalidate_text_cache()
            self._pages.clear()
        elif page_num in self._pages:
            self._pages[page_num].invalidate_text_cache()

    # ------------------------------------------------------------------
    # 页面尺寸（委托给引擎）
    # ------------------------------------------------------------------
    def get_page_bounds(self, page_num: int) -> Rect:
        """获取指定页的边界框（PDF 点单位）。

        Raises:
            PdfEngineError: 文档已关闭或页码越界
        """
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        return self._engine.page_bounds(self._handle, page_num)

    def get_all_page_bounds(self) -> list[Rect]:
        """获取所有页的边界框列表（用于视图一次性布局）。"""
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        return [self._engine.page_bounds(self._handle, i) for i in range(self.page_count)]

    # ------------------------------------------------------------------
    # 渲染（委托给引擎）
    # ------------------------------------------------------------------
    def render_page(self, page_num: int, zoom: float = 1.0) -> PixmapData:
        """渲染指定页为位图（委托给引擎）。

        Raises:
            PdfEngineError: 文档已关闭或页码越界
        """
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        return self._engine.render_page(self._handle, page_num, zoom)

    # ------------------------------------------------------------------
    # 搜索 / 文本
    # ------------------------------------------------------------------
    def search(self, query: str) -> SearchResult:
        """全文搜索（委托给引擎）。"""
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        return self._engine.search(self._handle, query)

    # ------------------------------------------------------------------
    # 编辑操作（阶段 6）
    # ------------------------------------------------------------------
    def delete_page(self, page_num: int) -> None:
        """删除指定页，并清除该页缓存、更新页数。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.delete_page(self._handle, page_num)
        # 更新元数据页数（DocumentMeta 是 frozen，用 replace 重建）
        self.meta = replace(self.meta, page_count=self.meta.page_count - 1)
        # 页缓存全部失效（页码偏移了）
        self.invalidate_page_cache()
        self.mark_modified()

    def rotate_page(self, page_num: int, degrees: int) -> None:
        """旋转指定页，并使该页渲染缓存失效。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.rotate_page(self._handle, page_num, degrees)
        self.invalidate_page_cache(page_num)
        self.mark_modified()

    def merge(self, other_path: Path) -> int:
        """合并另一个 PDF，返回追加的页数。"""
        self._ensure_open()
        assert self._handle is not None
        added = self._engine.merge(self._handle, other_path)
        # 更新元数据页数
        self.meta = replace(self.meta, page_count=self.meta.page_count + added)
        self.mark_modified()
        return added

    def add_watermark(self, page_num: int, text: str) -> None:
        """给指定页添加水印。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.add_text_watermark(self._handle, page_num, text)
        self.invalidate_page_cache(page_num)
        self.mark_modified()

    def save(self, path: Path) -> None:
        """保存文档到指定路径。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.save(self._handle, path)
        self.mark_saved()

    # ------------------------------------------------------------------
    # 批注操作（阶段 7）
    # ------------------------------------------------------------------
    def add_highlight(self, page_num: int, rect: Rect) -> None:
        """添加高亮批注。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.add_highlight(self._handle, page_num, rect)
        self.invalidate_page_cache(page_num)
        self.mark_modified()

    def add_ink(self, page_num: int, strokes: tuple[InkStroke, ...]) -> None:
        """添加手写笔迹批注。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.add_ink(self._handle, page_num, strokes)
        self.invalidate_page_cache(page_num)
        self.mark_modified()

    def add_text_annot(self, page_num: int, point: Point, text: str) -> None:
        """添加文本便签批注。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.add_text_annot(self._handle, page_num, point, text)
        self.invalidate_page_cache(page_num)
        self.mark_modified()

    def list_annotations(self, page_num: int) -> list[Annotation]:
        """列出指定页的所有批注。"""
        self._ensure_open()
        assert self._handle is not None
        return self._engine.list_annotations(self._handle, page_num)

    def delete_annotation(self, page_num: int, index: int) -> bool:
        """删除指定页的第 index 个批注。"""
        self._ensure_open()
        assert self._handle is not None
        ok = self._engine.delete_annotation(self._handle, page_num, index)
        if ok:
            self.invalidate_page_cache(page_num)
            self.mark_modified()
        return ok

    # ------------------------------------------------------------------
    # 修改状态管理（阶段 6 编辑功能将大量使用）
    # ------------------------------------------------------------------
    def mark_modified(self) -> None:
        """标记文档已修改。"""
        self._is_modified = True

    def mark_saved(self) -> None:
        """标记文档已保存（清除修改标记与页 dirty 标记）。"""
        self._is_modified = False
        for page in self._pages.values():
            page.is_dirty = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close(self) -> None:
        """关闭文档，释放引擎资源。可重复调用。"""
        if self._handle is not None:
            self._engine.close(self._handle)
            self._handle = None
        self._pages.clear()

    def _ensure_open(self) -> None:
        if self._handle is None:
            raise PdfEngineError("文档已关闭，无法操作。")

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
