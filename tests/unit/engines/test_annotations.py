"""引擎层批注方法测试。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.types import AnnotationType, InkStroke, Point, Rect

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


@pytest.fixture
def work_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "work.pdf"
    shutil.copy(SIMPLE, dst)
    return dst


class TestAddHighlight:
    def test_add_highlight(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = engine.open(work_pdf)
        engine.add_highlight(doc, 0, Rect(72, 90, 300, 110))
        annots = engine.list_annotations(doc, 0)
        assert any(a.is_highlight for a in annots)
        engine.close(doc)

    def test_add_highlight_out_of_range(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        from pdfreader.engines import PageIndexError

        doc = engine.open(work_pdf)
        with pytest.raises(PageIndexError):
            engine.add_highlight(doc, 999, Rect(0, 0, 10, 10))
        engine.close(doc)


class TestAddInk:
    def test_add_ink(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = engine.open(work_pdf)
        stroke = InkStroke(points=(Point(50, 50), Point(150, 80)))
        engine.add_ink(doc, 0, (stroke,))
        annots = engine.list_annotations(doc, 0)
        assert any(a.is_ink for a in annots)
        engine.close(doc)


class TestAddTextAnnot:
    def test_add_text_annot(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = engine.open(work_pdf)
        engine.add_text_annot(doc, 0, Point(200, 200), "便签内容")
        annots = engine.list_annotations(doc, 0)
        notes = [a for a in annots if a.is_text]
        assert len(notes) == 1
        assert notes[0].content == "便签内容"
        engine.close(doc)


class TestListAnnotations:
    def test_list_empty(self, engine: PyMuPdfEngine) -> None:
        doc = engine.open(SIMPLE)
        assert engine.list_annotations(doc, 0) == []
        engine.close(doc)

    def test_list_multiple(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = engine.open(work_pdf)
        engine.add_highlight(doc, 0, Rect(10, 10, 50, 20))
        engine.add_text_annot(doc, 0, Point(100, 100), "x")
        annots = engine.list_annotations(doc, 0)
        assert len(annots) == 2
        engine.close(doc)


class TestDeleteAnnotation:
    def test_delete(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = engine.open(work_pdf)
        engine.add_highlight(doc, 0, Rect(10, 10, 50, 20))
        engine.add_text_annot(doc, 0, Point(100, 100), "x")
        assert len(engine.list_annotations(doc, 0)) == 2
        assert engine.delete_annotation(doc, 0, 0) is True
        assert len(engine.list_annotations(doc, 0)) == 1
        engine.close(doc)

    def test_delete_invalid_index(self, engine: PyMuPdfEngine, work_pdf: Path) -> None:
        doc = engine.open(work_pdf)
        assert engine.delete_annotation(doc, 0, 999) is False
        engine.close(doc)


class TestAnnotationProperties:
    def test_type_name(self) -> None:
        from pdfreader.engines.types import Annotation

        a = Annotation(page_num=0, annot_type=AnnotationType.HIGHLIGHT, rect=Rect(0, 0, 1, 1))
        assert a.type_name == "高亮"
        assert a.is_highlight is True

        b = Annotation(page_num=0, annot_type=AnnotationType.INK, rect=Rect(0, 0, 1, 1))
        assert b.type_name == "手写"
        assert b.is_ink is True


class TestAddFreeText:
    def test_free_text_lists_content_and_survives_save(
        self, engine: PyMuPdfEngine, work_pdf: Path, tmp_path: Path
    ) -> None:
        doc = engine.open(work_pdf)
        box = Rect(100, 300, 300, 340)
        engine.add_free_text(doc, 0, box, "中文文本框 <b>&\n第二行")
        (annot,) = engine.list_annotations(doc, 0)
        assert annot.annot_type == AnnotationType.FREE_TEXT
        assert annot.type_name == "文本框"
        assert annot.content == "中文文本框 <b>&\n第二行"  # 原样保留，HTML 只用于外观
        assert abs(annot.rect.x0 - box.x0) < 2 and abs(annot.rect.y1 - box.y1) < 2
        out = tmp_path / "saved.pdf"
        engine.save(doc, out)
        engine.close(doc)
        again = engine.open(out)
        assert engine.list_annotations(again, 0)[0].content.startswith("中文文本框")
        engine.close(again)

    def test_edit_free_text_rebuilds_in_place_and_undoes(
        self, engine: PyMuPdfEngine, work_pdf: Path
    ) -> None:
        doc = engine.open(work_pdf)
        box = Rect(100, 300, 300, 340)
        engine.add_free_text(doc, 0, box, "旧文字")
        engine.set_annotation_content(doc, 0, 0, "新文字")
        (annot,) = engine.list_annotations(doc, 0)
        assert annot.annot_type == AnnotationType.FREE_TEXT
        assert annot.content == "新文字"
        assert abs(annot.rect.x0 - box.x0) < 2 and abs(annot.rect.y0 - box.y0) < 2
        assert engine.undo(doc) is True  # 一次撤销回到旧文字（删+建在同一个操作里）
        assert engine.list_annotations(doc, 0)[0].content == "旧文字"
        assert engine.undo(doc) is True
        assert engine.list_annotations(doc, 0) == []
        engine.close(doc)
