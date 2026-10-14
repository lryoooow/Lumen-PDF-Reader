"""PyMuPdfEngine 单元测试。

覆盖引擎层所有公开方法，包括正常路径与边界/异常路径。
目标覆盖率 ≥ 90%。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.engines import (
    CorruptPdfError,
    DocumentMeta,
    FileOpenError,
    PageIndexError,
    PageText,
    PasswordRequiredError,
    PdfEngine,
    PixmapData,
    PyMuPdfEngine,
    RenderError,
    SearchResult,
)
from pdfreader.engines.types import Rect

# 样例 PDF 目录
SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"
SCANNED = SAMPLES / "scanned.pdf"
ENCRYPTED = SAMPLES / "encrypted.pdf"
EMPTY = SAMPLES / "empty.pdf"
CORRUPT = SAMPLES / "corrupt.pdf"
ANNOTATED = SAMPLES / "with_annotations.pdf"


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


@pytest.fixture
def doc(engine: PyMuPdfEngine) -> Path:
    """返回 simple_text.pdf 的已打开句柄（用 engine 二次打开）。"""
    return engine.open(SIMPLE)


# ----------------------------------------------------------------------
# 协议符合性
# ----------------------------------------------------------------------
class TestProtocol:
    def test_engine_satisfies_protocol(self, engine: PyMuPdfEngine) -> None:
        """PyMuPdfEngine 实现了 PdfEngine 协议。"""
        assert isinstance(engine, PdfEngine)

    def test_engine_is_stateless_factory(self, engine: PyMuPdfEngine) -> None:
        """引擎本身无状态，可复用。"""
        d1 = engine.open(SIMPLE)
        d2 = engine.open(ANNOTATED)
        assert d1 is not d2
        engine.close(d1)
        engine.close(d2)


# ----------------------------------------------------------------------
# open / close
# ----------------------------------------------------------------------
class TestOpen:
    def test_open_normal(self, engine: PyMuPdfEngine) -> None:
        doc = engine.open(SIMPLE)
        assert doc is not None
        assert doc.path == SIMPLE
        engine.close(doc)

    def test_open_nonexistent_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(FileOpenError):
            engine.open(SIMPLE.with_name("does_not_exist.pdf"))

    def test_open_corrupt_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(CorruptPdfError):
            engine.open(CORRUPT)

    def test_open_encrypted_without_password(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(PasswordRequiredError) as exc_info:
            engine.open(ENCRYPTED)
        assert exc_info.value.needs_password is True

    def test_open_encrypted_with_correct_password(self, engine: PyMuPdfEngine) -> None:
        doc = engine.open(ENCRYPTED, password="secret")
        assert engine.page_count(doc) == 1
        engine.close(doc)

    def test_open_encrypted_with_wrong_password(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(PasswordRequiredError):
            engine.open(ENCRYPTED, password="wrong")

    def test_close_is_idempotent(self, engine: PyMuPdfEngine) -> None:
        doc = engine.open(SIMPLE)
        engine.close(doc)
        # 重复关闭不抛错
        engine.close(doc)


# ----------------------------------------------------------------------
# metadata / page_count
# ----------------------------------------------------------------------
class TestMetadata:
    def test_metadata_returns_documentmeta(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        meta = engine.metadata(doc)
        assert isinstance(meta, DocumentMeta)
        assert meta.page_count == 3
        assert meta.is_encrypted is False
        assert meta.is_pdf is True
        assert "PDF" in meta.format

    def test_metadata_empty_strings_for_missing(self, engine: PyMuPdfEngine) -> None:
        d = engine.open(EMPTY)
        meta = engine.metadata(d)
        # 空白页文档无标题等元数据，应为空串而非 None
        assert meta.title == ""
        assert meta.author == ""
        engine.close(d)

    def test_page_count(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        assert engine.page_count(doc) == 3

    def test_metadata_wrong_doc_type_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(TypeError):
            engine.metadata("not a document")  # type: ignore[arg-type]

    def test_page_count_wrong_doc_type_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(TypeError):
            engine.page_count(None)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# render_page
# ----------------------------------------------------------------------
class TestRender:
    def test_render_default_zoom(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pix = engine.render_page(doc, 0)
        assert isinstance(pix, PixmapData)
        # A4 @ 72dpi ≈ 595x842
        assert pix.width == 595
        assert pix.height == 842
        assert pix.n == 3  # RGB
        assert pix.stride == pix.width * pix.n
        assert len(pix.samples) == pix.stride * pix.height

    def test_render_with_zoom(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pix = engine.render_page(doc, 0, zoom=2.0)
        assert pix.width == 1190
        assert pix.height == 1684

    def test_render_invalid_zoom_raises(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(RenderError):
            engine.render_page(doc, 0, zoom=0)
        with pytest.raises(RenderError):
            engine.render_page(doc, 0, zoom=-1.0)

    def test_render_page_out_of_range(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(PageIndexError) as exc_info:
            engine.render_page(doc, 999)
        assert exc_info.value.page_num == 999
        assert exc_info.value.page_count == 3

    def test_render_negative_page(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(PageIndexError):
            engine.render_page(doc, -1)

    def test_render_samples_is_bytes(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pix = engine.render_page(doc, 0)
        assert isinstance(pix.samples, bytes)

    def test_render_scanned_page(self, engine: PyMuPdfEngine) -> None:
        """扫描件（纯图片页）也能渲染。"""
        d = engine.open(SCANNED)
        pix = engine.render_page(d, 0)
        assert pix.width > 0 and pix.height > 0
        engine.close(d)


# ----------------------------------------------------------------------
# extract_text
# ----------------------------------------------------------------------
class TestExtractText:
    def test_extract_returns_pagetext(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pt = engine.extract_text(doc, 0)
        assert isinstance(pt, PageText)
        assert pt.page_num == 0
        assert "Page 1" in pt.full_text
        assert "PyMuPDF" in pt.full_text

    def test_extract_has_spans_with_rect(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pt = engine.extract_text(doc, 0)
        assert len(pt.spans) > 0
        for span in pt:
            assert isinstance(span.rect, Rect)
            assert span.rect.width > 0
            assert span.rect.height > 0
            assert span.text  # 非空

    def test_extract_chinese_text(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pt = engine.extract_text(doc, 0)
        assert "中文" in pt.full_text or "测试" in pt.full_text

    def test_extract_scanned_empty_text(self, engine: PyMuPdfEngine) -> None:
        """扫描件无文本层，提取应为空字符串（但有 span 为空元组）。"""
        d = engine.open(SCANNED)
        pt = engine.extract_text(d, 0)
        assert pt.full_text == ""
        engine.close(d)

    def test_extract_page_out_of_range(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(PageIndexError):
            engine.extract_text(doc, 100)


# ----------------------------------------------------------------------
# search
# ----------------------------------------------------------------------
class TestSearch:
    def test_search_returns_result(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        result = engine.search(doc, "PyMuPDF")
        assert isinstance(result, SearchResult)
        assert result.query == "PyMuPDF"
        assert result.hit_count == 3  # 每页各一次
        assert set(result.page_numbers) == {0, 1, 2}

    def test_search_hit_has_rect(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        result = engine.search(doc, "PyMuPDF")
        for hit in result.hits:
            assert hit.page_num in {0, 1, 2}
            assert hit.rect.width > 0
            assert hit.rect.height > 0

    def test_search_hit_text_is_line_snippet(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        """命中的 text 是所在行的真实文本（面板据此区分同一页的多次命中）。"""
        result = engine.search(doc, "PyMuPDF")
        assert result.hits
        for hit in result.hits:
            assert "PyMuPDF" in hit.text
            assert "keyword" in hit.text, f"片段应是整行文本，实际：{hit.text!r}"

    def test_search_chinese(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        result = engine.search(doc, "测试成功")
        assert result.hit_count == 3

    def test_search_no_match(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        result = engine.search(doc, "这个关键词肯定不存在XYZ123")
        assert result.hit_count == 0
        assert result.hits == ()

    def test_search_empty_query_returns_empty(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        result = engine.search(doc, "")
        assert result.hit_count == 0

    def test_search_cancel_stops_at_next_page(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        """cancel 逐页检查：一旦返回 True 就停止，只返回已找到的部分结果。"""
        calls: list[int] = []

        def cancel() -> bool:
            calls.append(1)
            return len(calls) > 1  # 第 1 页照常搜，第 2 页开始前取消

        result = engine.search(doc, "PyMuPDF", cancel=cancel)

        assert len(calls) == 2, "应在第二页开始时立即停止，而不是搜完全部页"
        assert set(result.page_numbers) == {0}

    def test_search_cancelled_before_first_page(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        result = engine.search(doc, "PyMuPDF", cancel=lambda: True)
        assert result.hit_count == 0


# ----------------------------------------------------------------------
# iter_pages_text
# ----------------------------------------------------------------------
class TestIterPagesText:
    def test_iter_all_pages(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        pages = list(engine.iter_pages_text(doc))
        assert len(pages) == 3
        assert [p.page_num for p in pages] == [0, 1, 2]
        # 每页都包含关键词
        for p in pages:
            assert "PyMuPDF" in p.full_text

    def test_iter_empty_doc(self, engine: PyMuPdfEngine) -> None:
        d = engine.open(EMPTY)
        pages = list(engine.iter_pages_text(d))
        assert len(pages) == 1
        engine.close(d)


# ----------------------------------------------------------------------
# 数据结构测试（Rect / Point 等）
# ----------------------------------------------------------------------
class TestDataStructures:
    def test_rect_contains_point_inside(self) -> None:
        from pdfreader.engines.types import Point

        r = Rect(0.0, 0.0, 10.0, 10.0)
        assert r.contains(Point(5, 5)) is True
        assert r.contains(Point(0, 0)) is True  # 边界
        assert r.contains(Point(10, 10)) is True  # 边界

    def test_rect_contains_point_outside(self) -> None:
        from pdfreader.engines.types import Point

        r = Rect(0.0, 0.0, 10.0, 10.0)
        assert r.contains(Point(-1, 5)) is False
        assert r.contains(Point(5, 11)) is False

    def test_rect_dimensions(self) -> None:
        r = Rect(1.0, 2.0, 4.0, 8.0)
        assert r.width == 3.0
        assert r.height == 6.0

    def test_search_result_page_numbers_dedup_sorted(self, engine: PyMuPdfEngine, doc) -> None:  # type: ignore[no-untyped-def]
        # 同一页多次命中应去重
        result = engine.search(doc, "Page")
        # page_numbers 应去重且升序
        pns = result.page_numbers
        assert pns == tuple(sorted(set(pns)))


# ----------------------------------------------------------------------
# 异常路径 / 边界（提升覆盖率）
# ----------------------------------------------------------------------
class TestErrorPaths:
    def test_render_wrong_doc_type_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(TypeError):
            engine.render_page("bad", 0)  # type: ignore[arg-type]

    def test_extract_wrong_doc_type_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(TypeError):
            engine.extract_text(123, 0)  # type: ignore[arg-type]

    def test_search_wrong_doc_type_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(TypeError):
            engine.search(None, "x")  # type: ignore[arg-type]

    def test_close_wrong_doc_type_is_noop(self, engine: PyMuPdfEngine) -> None:
        """close 传入非文档对象应静默（不抛错）。"""
        engine.close("not a doc")  # type: ignore[arg-type]
        engine.close(None)  # type: ignore[arg-type]

    def test_iter_pages_text_wrong_doc_type_raises(self, engine: PyMuPdfEngine) -> None:
        with pytest.raises(TypeError):
            list(engine.iter_pages_text("bad"))  # type: ignore[arg-type]

    def test_document_get_page_negative(self, engine: PyMuPdfEngine) -> None:
        """直接测试 PyMuPdfDocument.get_page 的负数页码。"""
        from pdfreader.engines.pdf_engine import PyMuPdfDocument

        d = engine.open(SIMPLE)
        assert isinstance(d, PyMuPdfDocument)
        with pytest.raises(PageIndexError):
            d.get_page(-5)
        engine.close(d)

    def test_document_native_property(self, engine: PyMuPdfEngine) -> None:
        from pdfreader.engines.pdf_engine import PyMuPdfDocument

        d = engine.open(SIMPLE)
        assert isinstance(d, PyMuPdfDocument)
        assert d.native is not None
        assert d.page_count == 3
        engine.close(d)

    def test_document_double_close_via_close(self, engine: PyMuPdfEngine) -> None:
        """PyMuPdfDocument.close 幂等（已关闭再关不抛错）。"""
        d = engine.open(SIMPLE)
        d.close()
        d.close()  # is_closed 守卫，不抛错

    def test_render_handles_engine_internal_error(
        self, engine: PyMuPdfEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """渲染时底层异常转为 RenderError。"""
        import pymupdf

        d = engine.open(SIMPLE)

        original_get_pixmap = pymupdf.Page.get_pixmap

        def raising_get_pixmap(self, *args, **kwargs):
            raise RuntimeError("simulated render failure")

        monkeypatch.setattr(pymupdf.Page, "get_pixmap", raising_get_pixmap)
        try:
            with pytest.raises(RenderError):
                engine.render_page(d, 0)
        finally:
            monkeypatch.setattr(pymupdf.Page, "get_pixmap", original_get_pixmap)
            engine.close(d)

    def test_search_handles_engine_internal_error(
        self, engine: PyMuPdfEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """搜索时底层异常转为 SearchError。"""
        import pymupdf

        from pdfreader.engines import SearchError

        d = engine.open(SIMPLE)
        # 搜索走 TextPage.search（不再经过 Page.search_for，见引擎里的坐标约定说明）
        original_search = pymupdf.TextPage.search

        def raising_search(self, *args, **kwargs):
            raise RuntimeError("simulated search failure")

        monkeypatch.setattr(pymupdf.TextPage, "search", raising_search)
        try:
            with pytest.raises(SearchError):
                engine.search(d, "Page")
        finally:
            monkeypatch.setattr(pymupdf.TextPage, "search", original_search)
            engine.close(d)

    def test_open_generic_exception_becomes_fileopenerror(
        self, engine: PyMuPdfEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """open 遇到非 FileDataError 的异常转为 FileOpenError。"""
        import pymupdf

        original_open = pymupdf.open

        def raising_open(*args, **kwargs):
            raise PermissionError("simulated permission denied")

        monkeypatch.setattr(pymupdf, "open", raising_open)
        try:
            with pytest.raises(FileOpenError):
                engine.open(SIMPLE)
        finally:
            monkeypatch.setattr(pymupdf, "open", original_open)


class TestGetWords:
    def test_returns_words(self, engine: PyMuPdfEngine, doc: Path) -> None:
        words = engine.get_words(doc, 0)
        assert len(words) > 0
        assert words[0].text != ""
        assert words[0].rect.width >= 0
        engine.close(doc)

    def test_reading_order(self, engine: PyMuPdfEngine, doc: Path) -> None:
        """词按 (block, line, word_no) 升序（阅读顺序）。"""
        words = engine.get_words(doc, 0)
        keys = [(w.block, w.line, w.word_no) for w in words]
        assert keys == sorted(keys)
        engine.close(doc)

    def test_out_of_range_raises(self, engine: PyMuPdfEngine, doc: Path) -> None:
        with pytest.raises(PageIndexError):
            engine.get_words(doc, 999)
        engine.close(doc)


# ----------------------------------------------------------------------
# PNG 编码（OCR 图片源）
# ----------------------------------------------------------------------
class TestPixmapToPng:
    def test_roundtrip(self, engine: PyMuPdfEngine) -> None:
        """render_page → pixmap_to_png 得到可解码的 PNG，尺寸与页面一致。"""
        import pymupdf

        d = engine.open(SIMPLE)
        data = engine.render_page(d, 0, 1.0)
        png = engine.pixmap_to_png(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

        # PNG 直接解码为像素图：页面 rect 受 DPI 元数据影响，不用于比对
        decoded = pymupdf.Pixmap(png)
        assert (decoded.width, decoded.height) == (data.width, data.height)
        assert decoded.n == data.n
        engine.close(d)

    def test_padded_stride_is_compacted(self, engine: PyMuPdfEngine) -> None:
        """stride > width*n 时按行走样，结果与紧凑样本一致。"""
        d = engine.open(SIMPLE)
        data = engine.render_page(d, 0, 0.5)
        row_bytes = data.width * data.n
        pad = 7
        padded = b"".join(
            data.samples[i * data.stride : i * data.stride + row_bytes] + b"\x00" * pad
            for i in range(data.height)
        )
        assert len(padded) == (row_bytes + pad) * data.height

        out = engine.pixmap_to_png(
            PixmapData(
                samples=padded,
                width=data.width,
                height=data.height,
                n=data.n,
                stride=row_bytes + pad,
            )
        )
        assert out == engine.pixmap_to_png(data)
        engine.close(d)

    def test_unsupported_channels_raises(self, engine: PyMuPdfEngine) -> None:
        """n=2（未知色彩空间）明确报错，而不是抛 PyMuPDF 的原始异常。"""
        with pytest.raises(RenderError, match="通道数"):
            engine.pixmap_to_png(PixmapData(samples=b"\x00" * 12, width=2, height=2, n=2, stride=4))

    def test_truncated_samples_raises_rendererror(self, engine: PyMuPdfEngine) -> None:
        """样本长度与宽高不符 → RenderError（且消息被截断，不刷屏）。"""
        d = engine.open(SIMPLE)
        data = engine.render_page(d, 0, 1.0)
        broken = PixmapData(
            samples=data.samples[:100],
            width=data.width,
            height=data.height,
            n=data.n,
            stride=data.stride,
        )
        with pytest.raises(RenderError) as exc_info:
            engine.pixmap_to_png(broken)
        assert len(str(exc_info.value)) < 500
        engine.close(d)


# ----------------------------------------------------------------------
# 图片合并为 PDF
# ----------------------------------------------------------------------
class TestImagesToPdf:
    @staticmethod
    def _make_png(engine: PyMuPdfEngine, target: Path) -> Path:
        """用引擎自身的 PNG 编码生成一张真实 PNG 素材。"""
        d = engine.open(SIMPLE)
        try:
            target.write_bytes(engine.pixmap_to_png(engine.render_page(d, 0, 0.3)))
        finally:
            engine.close(d)
        return target

    def test_single_image(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        img = self._make_png(engine, tmp_path / "one.png")
        out = tmp_path / "out.pdf"
        assert engine.images_to_pdf([img], out) == 1

        d = engine.open(out)
        try:
            assert engine.page_count(d) == 1
        finally:
            engine.close(d)

    def test_multiple_images_order(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        imgs = [self._make_png(engine, tmp_path / f"{i}.png") for i in range(3)]
        out = tmp_path / "multi.pdf"
        assert engine.images_to_pdf(imgs, out) == 3

        d = engine.open(out)
        try:
            assert engine.page_count(d) == 3
        finally:
            engine.close(d)

    def test_missing_image_raises(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        with pytest.raises(FileOpenError):
            engine.images_to_pdf([tmp_path / "missing.png"], tmp_path / "out.pdf")

    def test_no_images_raises(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        """空列表 → FileOpenError（PyMuPDF 不允许保存 0 页文档）。"""
        with pytest.raises(FileOpenError):
            engine.images_to_pdf([], tmp_path / "empty.pdf")
