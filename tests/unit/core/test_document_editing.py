"""core 层编辑操作测试。

用真实 PyMuPDF 引擎验证编辑操作（删除/旋转/合并/水印/保存）
及其副作用（页数更新、缓存失效、修改标记）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pdfreader.core import Document
from pdfreader.engines import PyMuPdfEngine

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"
ANNOTATED = SAMPLES / "with_annotations.pdf"


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


@pytest.fixture
def work_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "work.pdf"
    shutil.copy(SIMPLE, dst)
    return dst


class TestDeletePage:
    def test_delete_updates_count_and_modified(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        assert doc.page_count == 3
        assert doc.is_modified is False
        doc.delete_page(0)
        assert doc.page_count == 2
        assert doc.is_modified is True
        doc.close()

    def test_delete_out_of_range(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        from pdfreader.engines import PageIndexError

        doc = Document.open(engine, work_pdf)
        with pytest.raises(PageIndexError):
            doc.delete_page(999)
        doc.close()


class TestRotate:
    def test_rotate_marks_modified(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        doc.rotate_page(0, 90)
        assert doc.is_modified is True
        doc.close()


class TestMerge:
    def test_merge_increases_count(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        added = doc.merge(ANNOTATED)
        assert added == 1
        assert doc.page_count == 4
        assert doc.is_modified is True
        doc.close()


class TestWatermark:
    def test_watermark_added(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        doc.add_watermark(0, "DRAFT")
        assert doc.is_modified is True
        assert "DRAFT" in doc.get_page(0).text.full_text
        doc.close()


class TestSave:
    def test_save_clears_modified(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        doc.delete_page(0)
        assert doc.is_modified is True
        out = work_pdf.with_name("out.pdf")
        doc.save(out)
        assert doc.is_modified is False
        doc.close()

    def test_save_persists_edits(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        doc.delete_page(0)
        out = work_pdf.with_name("persist.pdf")
        doc.save(out)
        doc.close()
        # 重开验证删除持久化
        doc2 = Document.open(engine, out)
        assert doc2.page_count == 2
        doc2.close()


class TestCacheInvalidation:
    def test_delete_invalidates_page_cache(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        # 访问第0页触发缓存
        _ = doc.get_page(0)
        assert len(doc._pages) == 1
        doc.delete_page(0)
        # 缓存应被清空
        assert len(doc._pages) == 0
        doc.close()

    def test_rotate_invalidates_single_page(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = Document.open(engine, work_pdf)
        page = doc.get_page(0)
        _ = page.text  # 触发文本缓存
        doc.rotate_page(0, 90)
        # 旋转后该页文本缓存应失效
        assert "text" not in page.__dict__
        doc.close()
