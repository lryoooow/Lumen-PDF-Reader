"""引擎层编辑方法测试。

覆盖增删页/旋转/合并/水印/保存，验证操作正确性与边界条件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.engines import (
    FileOpenError,
    PageIndexError,
    PdfEngineError,
    PyMuPdfEngine,
)

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"
ANNOTATED = SAMPLES / "with_annotations.pdf"


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


@pytest.fixture
def tmp_pdf(tmp_path: Path) -> Path:
    """复制一份样例到临时目录，避免修改原样例。"""
    import shutil

    dst = tmp_path / "work.pdf"
    shutil.copy(SIMPLE, dst)
    return dst


class TestDeletePage:
    def test_delete_page(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        assert engine.page_count(doc) == 3
        engine.delete_page(doc, 0)
        assert engine.page_count(doc) == 2
        engine.close(doc)

    def test_delete_out_of_range(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        with pytest.raises(PageIndexError):
            engine.delete_page(doc, 999)
        engine.close(doc)

    def test_delete_negative(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        with pytest.raises(PageIndexError):
            engine.delete_page(doc, -1)
        engine.close(doc)


class TestRotatePage:
    def test_rotate_90(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        engine.rotate_page(doc, 0, 90)
        engine.close(doc)

    def test_rotate_invalid_degrees(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        with pytest.raises(PdfEngineError):
            engine.rotate_page(doc, 0, 45)  # 非 90 倍数
        engine.close(doc)

    def test_rotate_out_of_range(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        with pytest.raises(PageIndexError):
            engine.rotate_page(doc, 999, 90)
        engine.close(doc)


class TestMerge:
    def test_merge(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        added = engine.merge(doc, ANNOTATED)
        assert added == 1  # annotated 是 1 页
        assert engine.page_count(doc) == 4
        engine.close(doc)

    def test_merge_nonexistent(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        with pytest.raises(FileOpenError):
            engine.merge(doc, tmp_pdf.with_name("nope.pdf"))
        engine.close(doc)


class TestWatermark:
    def test_add_watermark(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        engine.add_text_watermark(doc, 0, "DRAFT")
        text = engine.extract_text(doc, 0).full_text
        assert "DRAFT" in text
        engine.close(doc)

    def test_watermark_out_of_range(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        with pytest.raises(PageIndexError):
            engine.add_text_watermark(doc, 999, "X")
        engine.close(doc)


class TestSave:
    def test_save_and_reload(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        engine.delete_page(doc, 0)
        out = tmp_pdf.with_name("saved.pdf")
        engine.save(doc, out)
        engine.close(doc)
        # 重新打开验证
        doc2 = engine.open(out)
        assert engine.page_count(doc2) == 2
        engine.close(doc2)

    def test_save_preserves_edits(self, engine: PyMuPdfEngine, tmp_pdf: Path) -> None:
        doc = engine.open(tmp_pdf)
        engine.add_text_watermark(doc, 0, "MARK")
        out = tmp_pdf.with_name("marked.pdf")
        engine.save(doc, out)
        engine.close(doc)
        doc2 = engine.open(out)
        text = engine.extract_text(doc2, 0).full_text
        assert "MARK" in text
        engine.close(doc2)


class TestPageBounds:
    def test_page_bounds(self, engine: PyMuPdfEngine) -> None:
        doc = engine.open(SIMPLE)
        rect = engine.page_bounds(doc, 0)
        assert rect.width > 0
        assert rect.height > 0
        engine.close(doc)

    def test_page_bounds_out_of_range(self, engine: PyMuPdfEngine) -> None:
        doc = engine.open(SIMPLE)
        with pytest.raises(PageIndexError):
            engine.page_bounds(doc, 999)
        engine.close(doc)
