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

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pdfreader.core.page import Page
from pdfreader.engines.base import EngineDocument, PdfEngine
from pdfreader.engines.exceptions import PdfEngineError
from pdfreader.engines.types import (
    Annotation,
    DocumentMeta,
    InkStroke,
    LinkInfo,
    PixmapData,
    Point,
    Rect,
    SearchResult,
    TocEntry,
    WordBox,
)

__all__ = ["Document"]


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
    _saved_position: int = field(default=0, init=False, repr=False)
    _pages: dict[int, Page] = field(default_factory=dict, init=False, repr=False)
    # 打开时使用的密码（仅存内存）：后台线程（搜索 / OCR / 导出）重新打开文件时需要它
    _password: str | None = field(default=None, repr=False)

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
        doc = cls(path=path, meta=meta, _engine=engine, _handle=handle, _password=password)
        # 刚打开时磁盘内容 == 当前状态，历史位置即"已保存位置"
        doc._saved_position = doc._history_position()
        return doc

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
    def password(self) -> str | None:
        """打开时使用的密码（未加密文档为 None）。"""
        return self._password

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

    def _sync_page_count(self) -> None:
        """把 ``meta.page_count`` 与引擎实际页数对齐。

        页数会在编辑动作与撤销/重做中变化，而 ``DocumentMeta`` 是 frozen，
        因此统一以引擎真实页数重建 meta，避免两侧各算一次导致不一致
        （典型场景：删除页 → 撤销，引擎已恢复页数而 meta 仍是删除后的值）。
        """
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        actual = self._engine.page_count(self._handle)
        if actual != self.meta.page_count:
            self.meta = replace(self.meta, page_count=actual)

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
    def search(self, query: str, *, cancel: Callable[[], bool] | None = None) -> SearchResult:
        """全文搜索（委托给引擎）；``cancel`` 供后台线程中途放弃。"""
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        return self._engine.search(self._handle, query, cancel=cancel)

    def get_words(self, page_num: int) -> tuple[WordBox, ...]:
        """获取指定页的词及坐标（用于文本选择/复制）。"""
        self._ensure_open()
        assert self._handle is not None  # _ensure_open 保证
        return self._engine.get_words(self._handle, page_num)

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------
    def _edit(self, fn: Callable[[EngineDocument], object], page_num: int | None = None) -> None:
        """执行一次修改：调引擎 → 失效缓存 → 标脏，并维护「保存点」的有效性。

        在保存点**之前**的历史位置上做新编辑，会截断包含保存点的重做分支——此后再也
        回不到与磁盘一致的状态。旧实现只比较「位置 == 保存位置」，于是「保存 → 撤销 →
        新编辑 → 撤销 → 重做」会被误判为已保存，关闭时不提示，改动直接丢失。
        """
        self._ensure_open()
        assert self._handle is not None
        before = self._history_position()
        fn(self._handle)
        if 0 <= before < self._saved_position:
            self._saved_position = -1
        self._sync_page_count()
        if page_num is None:
            self.invalidate_page_cache()
        else:
            self.invalidate_page_cache(page_num)
        self.mark_modified()

    def delete_page(self, page_num: int) -> None:
        """删除指定页（不允许删掉最后一页：0 页的 PDF 无法保存）。"""
        self._ensure_open()
        if self.page_count <= 1:
            raise PdfEngineError("不能删除文档中唯一的一页")
        self._edit(lambda h: self._engine.delete_page(h, page_num))

    def rotate_page(self, page_num: int, degrees: int) -> None:
        """旋转指定页。"""
        self._edit(lambda h: self._engine.rotate_page(h, page_num, degrees), page_num)

    def merge(self, other_path: Path) -> int:
        """合并另一个 PDF，返回追加的页数。"""
        self._ensure_open()
        assert self._handle is not None
        added = self._engine.merge(self._handle, other_path)
        self._sync_page_count()
        self.invalidate_page_cache()
        # 合并会重建底层文档、撤销历史失效：位置不再可比，一律按"脏"处理
        self._saved_position = -1
        self.mark_modified()
        return added

    def add_watermark(self, page_num: int | Sequence[int], text: str) -> None:
        """给一页或多页添加水印（多页为一次可撤销操作）。"""
        target = page_num if isinstance(page_num, int) else None
        self._edit(lambda h: self._engine.add_text_watermark(h, page_num, text), target)

    def save(self, path: Path, *, incremental: bool = False) -> None:
        """保存文档到指定路径。incremental=True 用于存回原文件（追加变更）。"""
        self._ensure_open()
        assert self._handle is not None
        self._engine.save(self._handle, path, incremental=incremental)
        self.mark_saved()

    def save_copy(self, path: Path) -> None:
        """把当前内存内容另存一份副本到 path，**不改变**修改状态。

        用于把含未保存批注/编辑的内存文档喂给后台线程（搜索 / OCR / 导出），
        避免它们从磁盘上的旧文件读取。
        """
        self._ensure_open()
        assert self._handle is not None
        self._engine.save(self._handle, path)

    # ------------------------------------------------------------------
    # 批注操作
    # ------------------------------------------------------------------
    def add_highlight(
        self,
        page_num: int,
        rect: Rect | Sequence[Rect],
        *,
        color: tuple[float, float, float] | None = None,
    ) -> None:
        """添加高亮（多行矩形合成一条批注）。"""
        self._edit(lambda h: self._engine.add_highlight(h, page_num, rect, color=color), page_num)

    def add_markup(
        self,
        page_num: int,
        rects: Rect | Sequence[Rect],
        *,
        kind: str = "highlight",
        color: tuple[float, float, float] | None = None,
    ) -> None:
        """添加文字标记（高亮 / 下划线 / 删除线 / 波浪线）。"""
        self._edit(
            lambda h: self._engine.add_markup(h, page_num, rects, kind=kind, color=color),
            page_num,
        )

    def add_ink(self, page_num: int, strokes: tuple[InkStroke, ...]) -> None:
        """添加手写笔迹批注。"""
        self._edit(lambda h: self._engine.add_ink(h, page_num, strokes), page_num)

    def add_text_annot(self, page_num: int, point: Point, text: str) -> None:
        """添加文本便签批注。"""
        self._edit(lambda h: self._engine.add_text_annot(h, page_num, point, text), page_num)

    def set_annotation_content(self, page_num: int, index: int, text: str) -> None:
        """修改批注的笔记内容。"""
        self._edit(
            lambda h: self._engine.set_annotation_content(h, page_num, index, text), page_num
        )

    def list_annotations(self, page_num: int) -> list[Annotation]:
        """列出指定页的所有批注。"""
        self._ensure_open()
        assert self._handle is not None
        return self._engine.list_annotations(self._handle, page_num)

    def delete_annotation(self, page_num: int, index: int) -> bool:
        """删除指定页的第 index 个批注；序号不存在返回 False。"""
        existing = self.list_annotations(page_num)
        if not 0 <= index < len(existing):
            return False
        self._edit(lambda h: self._engine.delete_annotation(h, page_num, index), page_num)
        return True

    def annotation_index_at(self, page_num: int, point: Point) -> int | None:
        """返回点命中的最上层批注索引（用于橡皮擦/右键删除）。"""
        self._ensure_open()
        assert self._handle is not None
        return self._engine.annotation_index_at(self._handle, page_num, point)

    def insert_image(self, page_num: int, rect: Rect, image_path: Path) -> None:
        """在页面矩形处插入图片（签名 / 图片）。"""
        self._edit(lambda h: self._engine.insert_image(h, page_num, rect, image_path), page_num)

    def insert_text(self, page_num: int, point: Point, text: str, *, fontsize: int = 12) -> None:
        """在页面指定点插入文字（日期 / 时间戳）。"""
        self._edit(
            lambda h: self._engine.insert_text(h, page_num, point, text, fontsize=fontsize),
            page_num,
        )

    # ------------------------------------------------------------------
    # 目录与链接（只读）
    # ------------------------------------------------------------------
    def get_toc(self) -> tuple[TocEntry, ...]:
        """文档目录（书签 / 大纲）。"""
        self._ensure_open()
        assert self._handle is not None
        return self._engine.get_toc(self._handle)

    def get_links(self, page_num: int) -> tuple[LinkInfo, ...]:
        """某页的链接（内部跳转 / 外部网址）。"""
        self._ensure_open()
        assert self._handle is not None
        return self._engine.get_links(self._handle, page_num)

    # ------------------------------------------------------------------
    # 撤销 / 重做（委托引擎日志）
    # ------------------------------------------------------------------
    def undo(self) -> bool:
        self._ensure_open()
        assert self._handle is not None
        ok = self._engine.undo(self._handle)
        if ok:
            # 撤销可能恢复被删除的页，页数必须跟着引擎走
            self._sync_page_count()
            self.invalidate_page_cache()
            self._refresh_modified_from_history()
        return ok

    def redo(self) -> bool:
        self._ensure_open()
        assert self._handle is not None
        ok = self._engine.redo(self._handle)
        if ok:
            self._sync_page_count()
            self.invalidate_page_cache()
            self._refresh_modified_from_history()
        return ok

    def can_undo(self) -> bool:
        return self._handle is not None and self._engine.can_undo(self._handle)

    def can_redo(self) -> bool:
        return self._handle is not None and self._engine.can_redo(self._handle)

    # ------------------------------------------------------------------
    # 修改状态管理（阶段 6 编辑功能将大量使用）
    # ------------------------------------------------------------------
    def mark_modified(self) -> None:
        """标记文档已修改。"""
        self._is_modified = True

    def mark_saved(self) -> None:
        """标记文档已保存（清除修改标记与页 dirty 标记）。"""
        self._is_modified = False
        # 记住「磁盘内容对应历史中的哪一步」，撤销回到这一步就不算脏
        self._saved_position = self._history_position()
        for page in self._pages.values():
            page.is_dirty = False

    def _history_position(self) -> int:
        """当前撤销历史位置（引擎不支持时返回 0）。"""
        if self._handle is None:
            return 0
        return self._engine.history_position(self._handle)

    def _refresh_modified_from_history(self) -> None:
        """撤销/重做后按「是否回到上次保存的位置」决定脏标记。

        原先无论撤到哪一步都直接标脏：用户改一笔再撤销（内容与磁盘完全一致）
        关闭时仍会弹保存框。``_saved_position`` 为 -1 表示历史不可比
        （如合并重建过底层文档），此时保持"脏"。
        """
        if self._saved_position < 0:
            self._is_modified = True
            return
        self._is_modified = self._history_position() != self._saved_position

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close(self) -> None:
        """关闭文档，释放引擎资源。可重复调用。"""
        if self._handle is not None:
            self._engine.close(self._handle)
            self._handle = None
        self._pages.clear()
        self._password = None

    def _ensure_open(self) -> None:
        if self._handle is None:
            raise PdfEngineError("文档已关闭，无法操作。")

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
