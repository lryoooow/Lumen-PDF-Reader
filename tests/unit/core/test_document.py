"""core 层单元测试。

用 FakeEngine（内存实现）替代真实 PyMuPDF，使 core 层测试完全脱离 pymupdf，
从而验证"core 层框架无关"的铁律，同时获得快速、确定性的测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from pdfreader.core import Document, DocumentState, OutlineItem, Page, empty_outline
from pdfreader.engines.base import EngineDocument, PdfEngine
from pdfreader.engines.exceptions import PageIndexError
from pdfreader.engines.types import (
    DocumentMeta,
    PageText,
    PixmapData,
    Rect,
    SearchHit,
    SearchResult,
)


# ----------------------------------------------------------------------
# FakeEngine：内存实现，完全不依赖 pymupdf
# ----------------------------------------------------------------------
@dataclass
class FakeDoc(EngineDocument):
    """FakeEngine 的文档句柄。"""

    path: Path | None = None
    _closed: bool = False

    @property
    def path_(self) -> Path | None:  # type: ignore[override]
        return self.path

    def close(self) -> None:
        self._closed = True


class FakeEngine:
    """PdfEngine 的内存实现，用于 core 层测试。

    维护若干"虚拟页"的文本，可配置页数与内容。完全不依赖 pymupdf。
    """

    def __init__(self, page_count: int = 3, *, encrypted: bool = False) -> None:
        self._page_count = page_count
        self._encrypted = encrypted
        self.opened_paths: list[Path] = []
        self.render_calls: list[int] = []
        self.search_calls: list[str] = []

    def open(self, path: Path, password: str | None = None) -> FakeDoc:  # type: ignore[override]
        self.opened_paths.append(path)
        return FakeDoc(path=path)

    def close(self, doc: EngineDocument) -> None:  # type: ignore[override]
        if isinstance(doc, FakeDoc):
            doc.close()

    def metadata(self, doc: EngineDocument) -> DocumentMeta:  # type: ignore[override]
        return DocumentMeta(
            page_count=self._page_count,
            is_encrypted=self._encrypted,
            title="Fake Title",
            author="Tester",
        )

    def page_count(self, doc: EngineDocument) -> int:  # type: ignore[override]
        return self._page_count

    def render_page(  # type: ignore[override]
        self, doc: EngineDocument, page_num: int, zoom: float = 1.0
    ) -> PixmapData:
        if page_num < 0 or page_num >= self._page_count:
            raise PageIndexError(page_num, self._page_count)
        self.render_calls.append(page_num)
        size = int(100 * zoom)
        return PixmapData(
            samples=b"\x00" * (size * size * 3), width=size, height=size, n=3, stride=size * 3
        )

    def extract_text(self, doc: EngineDocument, page_num: int) -> PageText:  # type: ignore[override]
        if page_num < 0 or page_num >= self._page_count:
            raise PageIndexError(page_num, self._page_count)
        text = f"Page {page_num} content with keyword HELLO."
        return PageText(
            page_num=page_num,
            full_text=text,
            spans=(),
        )

    def search(self, doc: EngineDocument, query: str) -> SearchResult:  # type: ignore[override]
        self.search_calls.append(query)
        if not query:
            return SearchResult(query=query, hits=())
        hits = tuple(
            SearchHit(page_num=i, rect=Rect(0, 0, 10, 10), text=query)
            for i in range(self._page_count)
        )
        return SearchResult(query=query, hits=hits)

    def iter_pages_text(self, doc: EngineDocument):  # type: ignore[override]
        for i in range(self._page_count):
            yield self.extract_text(doc, i)


@pytest.fixture
def fake_engine() -> FakeEngine:
    return FakeEngine(page_count=3)


@pytest.fixture
def fake_path(tmp_path: Path) -> Path:
    return tmp_path / "fake.pdf"


@pytest.fixture
def document(fake_engine: FakeEngine, fake_path: Path) -> Document:
    """用 FakeEngine 打开的 Document。"""
    return Document.open(cast(PdfEngine, fake_engine), fake_path)


# ----------------------------------------------------------------------
# Document 工厂与基本属性
# ----------------------------------------------------------------------
class TestDocumentOpen:
    def test_open_via_factory(self, fake_engine: FakeEngine, fake_path: Path) -> None:
        doc = Document.open(cast(PdfEngine, fake_engine), fake_path)
        assert doc.page_count == 3
        assert doc.path == fake_path

    def test_open_calls_engine_open(self, fake_engine: FakeEngine, fake_path: Path) -> None:
        Document.open(cast(PdfEngine, fake_engine), fake_path)
        assert fake_path in fake_engine.opened_paths

    def test_title_from_metadata(self, document: Document) -> None:
        assert document.title == "Fake Title"

    def test_title_fallback_to_filename(self, fake_engine: FakeEngine, tmp_path: Path) -> None:
        path = tmp_path / "mydoc.pdf"
        # 让元数据 title 为空，应回退到文件名
        path.write_bytes(b"")  # 占位，FakeEngine 不读真实内容

        class NoTitleEngine(FakeEngine):
            def metadata(self, doc: EngineDocument) -> DocumentMeta:
                return DocumentMeta(page_count=1, title="")

        doc = Document.open(cast(PdfEngine, NoTitleEngine()), path)
        assert doc.title == "mydoc"

    def test_is_modified_default_false(self, document: Document) -> None:
        assert document.is_modified is False


# ----------------------------------------------------------------------
# 页访问
# ----------------------------------------------------------------------
class TestPageAccess:
    def test_get_page_returns_page(self, document: Document) -> None:
        page = document.get_page(1)
        assert isinstance(page, Page)
        assert page.page_num == 1

    def test_get_page_caches(self, document: Document) -> None:
        p1 = document.get_page(0)
        p2 = document.get_page(0)
        assert p1 is p2  # 同一对象（缓存）

    def test_get_page_out_of_range(self, document: Document) -> None:
        with pytest.raises(IndexError):
            document.get_page(999)
        with pytest.raises(IndexError):
            document.get_page(-1)

    def test_iter_pages(self, document: Document) -> None:
        pages = list(document.iter_pages())
        assert len(pages) == 3
        assert [p.page_num for p in pages] == [0, 1, 2]

    def test_page_text_lazy_load(self, document: Document) -> None:
        page = document.get_page(0)
        pt = page.text
        assert "HELLO" in pt.full_text

    def test_page_text_cached(self, document: Document) -> None:
        page = document.get_page(0)
        pt1 = page.text
        pt2 = page.text
        assert pt1 is pt2  # cached_property 命中缓存

    def test_invalidate_page_cache(self, document: Document) -> None:
        page = document.get_page(0)
        _ = page.text  # 触发缓存
        document.invalidate_page_cache(0)
        # 清除后同一页对象的文本缓存失效，再次访问会重新提取
        assert "text" not in page.__dict__
        # 重新访问能正常取到文本
        pt = page.text
        assert "HELLO" in pt.full_text

    def test_invalidate_all_pages(self, document: Document) -> None:
        document.get_page(0)
        document.get_page(1)
        assert len(document._pages) == 2
        document.invalidate_page_cache()  # 清全部
        assert len(document._pages) == 0


# ----------------------------------------------------------------------
# 搜索
# ----------------------------------------------------------------------
class TestSearch:
    def test_search_delegates_to_engine(self, document: Document, fake_engine: FakeEngine) -> None:
        result = document.search("HELLO")
        assert result.hit_count == 3
        assert "HELLO" in fake_engine.search_calls

    def test_search_empty(self, document: Document) -> None:
        result = document.search("")
        assert result.hit_count == 0


# ----------------------------------------------------------------------
# 修改状态
# ----------------------------------------------------------------------
class TestModification:
    def test_mark_modified(self, document: Document) -> None:
        document.mark_modified()
        assert document.is_modified is True

    def test_mark_saved_clears_flag(self, document: Document) -> None:
        document.mark_modified()
        assert document.is_modified is True
        document.mark_saved()
        assert document.is_modified is False

    def test_mark_saved_clears_page_dirty(self, document: Document) -> None:
        page = document.get_page(0)
        page.is_dirty = True
        document.mark_saved()
        assert page.is_dirty is False


# ----------------------------------------------------------------------
# 生命周期 / 关闭
# ----------------------------------------------------------------------
class TestLifecycle:
    def test_close_releases_handle(self, document: Document) -> None:
        document.close()
        assert document.is_closed is True

    def test_close_is_idempotent(self, document: Document) -> None:
        document.close()
        document.close()  # 不抛错

    def test_close_clears_page_cache(self, document: Document) -> None:
        document.get_page(0)
        assert len(document._pages) == 1
        document.close()
        assert len(document._pages) == 0

    def test_context_manager(self, fake_engine: FakeEngine, fake_path: Path) -> None:
        with Document.open(cast(PdfEngine, fake_engine), fake_path) as doc:
            assert doc.page_count == 3
        assert doc.is_closed is True

    def test_operation_after_close_raises(self, document: Document) -> None:
        document.close()
        from pdfreader.engines.exceptions import PdfEngineError

        with pytest.raises(PdfEngineError):
            document.search("x")


# ----------------------------------------------------------------------
# Page 模型独立测试
# ----------------------------------------------------------------------
class TestPageModel:
    def test_unbound_page_text_raises(self) -> None:
        page = Page(page_num=0)
        with pytest.raises(RuntimeError):
            _ = page.text

    def test_aspect_ratio(self) -> None:
        page = Page(page_num=0, width=200, height=100)
        assert page.aspect_ratio == 2.0

    def test_aspect_ratio_zero_height(self) -> None:
        page = Page(page_num=0, width=100, height=0)
        assert page.aspect_ratio == 0.0

    def test_bind_returns_self(self, fake_engine: FakeEngine, fake_path: Path) -> None:
        doc = fake_engine.open(fake_path)
        page = Page(page_num=0)
        result = page.bind(cast(PdfEngine, fake_engine), doc)
        assert result is page

    def test_invalidate_text_cache(self, fake_engine: FakeEngine, fake_path: Path) -> None:
        doc = fake_engine.open(fake_path)
        page = Page(page_num=0).bind(cast(PdfEngine, fake_engine), doc)
        _ = page.text
        page.invalidate_text_cache()
        # 再次访问应重新提取
        pt = page.text
        assert pt.full_text.startswith("Page 0")


# ----------------------------------------------------------------------
# Outline 模型
# ----------------------------------------------------------------------
class TestOutline:
    def test_outline_item_defaults(self) -> None:
        item = OutlineItem(title="Chapter 1")
        assert item.page_num is None
        assert item.level == 0
        assert item.children == []

    def test_add_child_sets_level(self) -> None:
        root = OutlineItem(title="Root", level=0)
        child = root.add_child(OutlineItem(title="Child"))
        assert child in root.children
        assert child.level == 1

    def test_walk_depth_first(self) -> None:
        root = OutlineItem(title="A")
        b = root.add_child(OutlineItem(title="B"))
        b.add_child(OutlineItem(title="C"))
        titles = [item.title for item in root.walk()]
        assert titles == ["A", "B", "C"]

    def test_empty_outline(self) -> None:
        outline = empty_outline()
        assert outline.title == "(无大纲)"


# ----------------------------------------------------------------------
# DocumentState 常量
# ----------------------------------------------------------------------
class TestDocumentState:
    def test_state_constants(self) -> None:
        assert DocumentState.READY == "ready"
        assert DocumentState.MODIFIED == "modified"
        assert DocumentState.CLOSED == "closed"
        assert DocumentState.ERROR == "error"
