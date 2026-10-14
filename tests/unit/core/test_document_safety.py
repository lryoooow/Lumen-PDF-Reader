"""文档安全相关回归：脏标记分叉、禁止删到 0 页、密码、控制器只读查询与另存为。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdfreader.controllers import DocumentController, OpenResult
from pdfreader.core import Document
from pdfreader.engines import PdfEngineError, PyMuPdfEngine
from pdfreader.engines.types import Point, Rect

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
ENCRYPTED = SAMPLES / "encrypted.pdf"
PASSWORD = "secret"


def _make_pdf(path: Path, pages: int = 3) -> Path:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"page {i + 1} hello world")
    d.save(str(path))
    d.close()
    return path


class TestDirtyFlagAfterBranching:
    def test_new_edit_before_save_point_invalidates_it(self, tmp_path: Path) -> None:
        """R3-04：保存 → 撤销 → 新编辑 → 撤销 → 重做，内容已≠磁盘，必须仍是脏的。"""
        path = _make_pdf(tmp_path / "b.pdf")
        doc = Document.open(PyMuPdfEngine(), path)
        doc.add_highlight(0, Rect(72, 60, 200, 80))
        doc.add_highlight(0, Rect(72, 90, 200, 110))
        doc.save(path, incremental=True)
        assert doc.is_modified is False
        doc.undo()
        doc.add_text_annot(1, Point(100, 100), "新分支")
        doc.undo()
        doc.redo()
        assert doc.is_modified is True
        doc.close()

    def test_undo_back_to_save_point_still_clean(self, tmp_path: Path) -> None:
        """不分叉时，撤销回到保存点仍判为干净（不能矫枉过正）。"""
        path = _make_pdf(tmp_path / "c.pdf")
        doc = Document.open(PyMuPdfEngine(), path)
        doc.add_highlight(0, Rect(72, 60, 200, 80))
        doc.save(path, incremental=True)
        doc.add_highlight(0, Rect(72, 90, 200, 110))
        assert doc.is_modified is True
        doc.undo()
        assert doc.is_modified is False
        doc.close()


class TestDeleteLastPage:
    def test_cannot_delete_only_page(self, tmp_path: Path) -> None:
        """R3-05：0 页的 PDF 无法保存，必须在删除时拦住。"""
        doc = Document.open(PyMuPdfEngine(), _make_pdf(tmp_path / "one.pdf", 1))
        with pytest.raises(PdfEngineError, match="唯一的一页"):
            doc.delete_page(0)
        assert doc.page_count == 1
        assert doc.is_modified is False
        doc.close()

    def test_controller_reports_reason(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        ctrl = DocumentController()
        assert ctrl.open_file(_make_pdf(tmp_path / "one.pdf", 1))
        assert ctrl.delete_page(0) is False
        assert "唯一的一页" in ctrl.last_error
        ctrl.close_document()


class TestPassword:
    def test_document_remembers_password_until_closed(self) -> None:
        doc = Document.open(PyMuPdfEngine(), ENCRYPTED, password=PASSWORD)
        assert doc.password == PASSWORD
        doc.close()
        assert doc.password is None

    def test_controller_classifies_missing_and_wrong_password(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        ctrl = DocumentController()
        kinds: list[str] = []
        ctrl.openFailed.connect(lambda kind, _msg: kinds.append(kind))
        assert ctrl.open_file(ENCRYPTED) is False
        assert ctrl.open_file(ENCRYPTED, "wrong") is False
        assert kinds == [OpenResult.NEEDS_PASSWORD, OpenResult.WRONG_PASSWORD]
        assert ctrl.open_file(ENCRYPTED, PASSWORD) is True
        assert ctrl.password == PASSWORD
        ctrl.close_document()
        assert ctrl.password is None


class TestControllerQueries:
    def test_list_all_annotations_and_note_edit(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        ctrl = DocumentController()
        ctrl.open_file(_make_pdf(tmp_path / "a.pdf", 3))
        assert ctrl.add_markup(0, (Rect(72, 60, 200, 80),), kind="underline")
        assert ctrl.add_text_annot(2, Point(100, 100), "备注")
        annots = ctrl.list_all_annotations()
        assert [(a.page_num, a.type_name) for a in annots] == [(0, "下划线"), (2, "便签")]
        assert ctrl.set_annotation_content(0, 0, "想法")
        assert ctrl.list_annotations(0)[0].content == "想法"
        assert ctrl.delete_annotation(2, 0) is True
        assert ctrl.delete_annotation(2, 0) is False
        assert ctrl.last_error == "该批注已不存在"
        ctrl.close_document()

    def test_save_as_to_original_path(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        """R3-06：「另存为」选中正在打开的原文件也能保存成功。"""
        path = _make_pdf(tmp_path / "same.pdf", 1)
        ctrl = DocumentController()
        ctrl.open_file(path)
        ctrl.add_highlight(0, Rect(72, 60, 200, 80))
        assert ctrl.save_as(path) is True, ctrl.last_error
        assert ctrl.is_modified is False
        ctrl.close_document()

    def test_failed_merge_reports_reason(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        ctrl = DocumentController()
        ctrl.open_file(_make_pdf(tmp_path / "m.pdf", 1))
        assert ctrl.merge(SAMPLES / "encrypted.pdf") == 0
        assert "已加密" in ctrl.last_error
        ctrl.close_document()

    def test_queries_without_document_are_safe(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        ctrl = DocumentController()
        assert ctrl.get_toc() == ()
        assert ctrl.get_links(0) == ()
        assert ctrl.list_all_annotations() == []
        assert ctrl.password is None
