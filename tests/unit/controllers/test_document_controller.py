"""DocumentController 单元测试。

用 FakeEngine 避免依赖真实 pymupdf，专注于控制器逻辑（信号、状态、错误分支）。
真实引擎的端到端测试见 tests/integration/。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from pdfreader.controllers import DocumentController, OpenResult
from pdfreader.engines.base import EngineDocument, PdfEngine
from pdfreader.engines.exceptions import (
    CorruptPdfError,
    FileOpenError,
    PageIndexError,
    PasswordRequiredError,
)
from pdfreader.engines.types import DocumentMeta, PageText, PixmapData, SearchResult


@dataclass
class FakeDoc(EngineDocument):
    path: Path | None = None

    def close(self) -> None:
        pass


class FakeEngine:
    """可控的 FakeEngine，可配置行为以测试错误分支。"""

    def __init__(
        self,
        *,
        page_count: int = 3,
        fail_with: Exception | None = None,
    ) -> None:
        self._page_count = page_count
        self._fail_with = fail_with

    def open(self, path: Path, password: str | None = None) -> FakeDoc:  # type: ignore[override]
        if self._fail_with is not None:
            raise self._fail_with
        return FakeDoc(path=path)

    def close(self, doc: EngineDocument) -> None:  # type: ignore[override]
        pass

    def metadata(self, doc: EngineDocument) -> DocumentMeta:  # type: ignore[override]
        return DocumentMeta(page_count=self._page_count, title="Test")

    def page_count(self, doc: EngineDocument) -> int:  # type: ignore[override]
        return self._page_count

    def render_page(  # type: ignore[override]
        self, doc: EngineDocument, page_num: int, zoom: float = 1.0
    ) -> PixmapData:
        if page_num < 0 or page_num >= self._page_count:
            raise PageIndexError(page_num, self._page_count)
        size = 10
        return PixmapData(
            samples=b"\x00" * (size * size * 3), width=size, height=size, n=3, stride=size * 3
        )

    def extract_text(self, doc: EngineDocument, page_num: int) -> PageText:  # type: ignore[override]
        return PageText(page_num=page_num, full_text=f"page {page_num}", spans=())

    def search(self, doc: EngineDocument, query: str) -> SearchResult:  # type: ignore[override]
        return SearchResult(query=query, hits=())

    def iter_pages_text(self, doc: EngineDocument) -> Iterator[PageText]:  # type: ignore[override]
        for i in range(self._page_count):
            yield self.extract_text(doc, i)


@pytest.fixture
def controller() -> DocumentController:
    engine = FakeEngine(page_count=3)
    return DocumentController(engine=cast(PdfEngine, engine))


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "test.pdf"


# ----------------------------------------------------------------------
# 打开 / 状态
# ----------------------------------------------------------------------
class TestOpen:
    def test_open_success(self, controller: DocumentController, path: Path) -> None:
        opened: list[int] = []
        controller.documentOpened.connect(opened.append)
        result = controller.open_file(path)
        assert result is True
        assert opened == [3]
        assert controller.has_document is True
        assert controller.page_count == 3
        assert controller.title == "Test"

    def test_open_corrupt(self, path: Path) -> None:
        engine = FakeEngine(fail_with=CorruptPdfError("corrupt"))
        ctrl = DocumentController(engine=cast(PdfEngine, engine))
        failures: list[tuple[str, str]] = []
        ctrl.openFailed.connect(lambda r, m: failures.append((r, m)))
        result = ctrl.open_file(path)
        assert result is False
        assert failures[0][0] == OpenResult.CORRUPT

    def test_open_not_found(self, path: Path) -> None:
        engine = FakeEngine(fail_with=FileOpenError("文件不存在：x"))
        ctrl = DocumentController(engine=cast(PdfEngine, engine))
        failures: list[str] = []
        ctrl.openFailed.connect(lambda r, m: failures.append(r))
        ctrl.open_file(path)
        assert failures[0] == OpenResult.NOT_FOUND

    def test_open_needs_password(self, path: Path) -> None:
        engine = FakeEngine(fail_with=PasswordRequiredError("need pw"))
        ctrl = DocumentController(engine=cast(PdfEngine, engine))
        failures: list[str] = []
        ctrl.openFailed.connect(lambda r, m: failures.append(r))
        ctrl.open_file(path)
        assert failures[0] == OpenResult.NEEDS_PASSWORD

    def test_open_unknown_error(self, path: Path) -> None:
        engine = FakeEngine(fail_with=ValueError("unexpected"))
        ctrl = DocumentController(engine=cast(PdfEngine, engine))
        failures: list[str] = []
        ctrl.openFailed.connect(lambda r, m: failures.append(r))
        ctrl.open_file(path)
        assert failures[0] == OpenResult.UNKNOWN_ERROR


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------
class TestRender:
    def test_render_page(self, controller: DocumentController, path: Path) -> None:
        controller.open_file(path)
        data = controller.render_page(0)
        assert data.width == 10
        assert controller.current_page == 0

    def test_render_without_doc_raises(self, controller: DocumentController) -> None:
        with pytest.raises(RuntimeError):
            controller.render_page(0)

    def test_render_out_of_range(self, controller: DocumentController, path: Path) -> None:
        controller.open_file(path)
        with pytest.raises(IndexError):
            controller.render_page(999)


# ----------------------------------------------------------------------
# 关闭
# ----------------------------------------------------------------------
class TestClose:
    def test_close_emits_signal(self, controller: DocumentController, path: Path) -> None:
        controller.open_file(path)
        closed: list[bool] = []
        controller.documentClosed.connect(lambda: closed.append(True))
        controller.close_document()
        assert closed == [True]
        assert controller.has_document is False

    def test_open_replaces_old_document(self, controller: DocumentController, path: Path) -> None:
        controller.open_file(path)
        closed: list[bool] = []
        controller.documentClosed.connect(lambda: closed.append(True))
        # 再次打开会先关闭旧的
        controller.open_file(path)
        assert closed == [True]
