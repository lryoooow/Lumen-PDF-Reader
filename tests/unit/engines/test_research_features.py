"""引擎层：科研阅读能力与保存 / 合并回归（中文取词、文字标记、目录链接、水印、保存到原文件）。

R3-xx 为第三轮全面审查的问题编号（见 CHANGELOG「Lumen 0.0.1」）。

每个用例对应一个实测复现过的缺陷或新增能力，断言盯的是用户可感知的结果。
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pymupdf
import pytest

from pdfreader.engines import FileOpenError, PdfEngineError, PyMuPdfEngine
from pdfreader.engines.exceptions import PasswordRequiredError
from pdfreader.engines.pdf_engine import _line_text
from pdfreader.engines.types import AnnotationType, Rect

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


def _make_pdf(path: Path, pages: int = 3) -> Path:
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 72), f"page {i + 1} hello world")
        p.insert_text((72, 100), "alpha beta gamma delta epsilon zeta")
    d.save(str(path))
    d.close()
    return path


def _make_cjk_pdf(path: Path, rotate: int = 0) -> Path:
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 72), "深度学习BERT模型综述", fontname="china-s", fontsize=12)
    if rotate:
        p.set_rotation(rotate)
    d.save(str(path))
    d.close()
    return path


# ---------------------------------------------------------------- R3-11 中文取词
class TestCjkWords:
    def test_cjk_line_split_into_characters(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        """整行中文原本是一个"词"，必须拆到单字，拉丁片段保持整体。"""
        doc = engine.open(_make_cjk_pdf(tmp_path / "c.pdf"))
        words = engine.get_words(doc, 0)
        texts = [w.text for w in words]
        assert texts == ["深", "度", "学", "习", "BERT", "模", "型", "综", "述"]
        # 字框从左到右、互不重叠
        xs = [w.rect.x0 for w in words]
        assert xs == sorted(xs)
        assert all(a.rect.x1 <= b.rect.x0 + 0.5 for a, b in pairwise(words))
        doc.close()

    def test_cjk_split_on_rotated_page(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_cjk_pdf(tmp_path / "r.pdf", rotate=90))
        words = engine.get_words(doc, 0)
        assert [w.text for w in words][:4] == ["深", "度", "学", "习"]
        bounds = engine.page_bounds(doc, 0)
        assert all(0 <= w.rect.x0 <= bounds.x1 and 0 <= w.rect.y0 <= bounds.y1 for w in words)
        doc.close()

    def test_latin_words_unchanged(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "l.pdf", 1))
        texts = [w.text for w in engine.get_words(doc, 0)]
        assert texts[:5] == ["page", "1", "hello", "world", "alpha"]
        doc.close()


# ---------------------------------------------------------------- R3-12 多行一条批注 + 标记种类
class TestMarkup:
    def test_multi_line_highlight_is_one_annotation(
        self, engine: PyMuPdfEngine, tmp_path: Path
    ) -> None:
        doc = engine.open(_make_pdf(tmp_path / "m.pdf", 1))
        engine.add_highlight(
            doc, 0, (Rect(72, 60, 200, 76), Rect(72, 88, 300, 104)), color=(0.5, 0.8, 0.5)
        )
        annots = engine.list_annotations(doc, 0)
        assert len(annots) == 1
        assert annots[0].annot_type == AnnotationType.HIGHLIGHT
        assert annots[0].color is not None and abs(annots[0].color[0] - 0.5) < 0.01
        # 一次撤销整条消失
        assert engine.undo(doc) is True
        assert engine.list_annotations(doc, 0) == []
        doc.close()

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("underline", AnnotationType.UNDERLINE),
            ("strikeout", AnnotationType.STRIKEOUT),
            ("squiggly", AnnotationType.SQUIGGLY),
        ],
    )
    def test_markup_kinds(
        self, engine: PyMuPdfEngine, tmp_path: Path, kind: str, expected: int
    ) -> None:
        doc = engine.open(_make_pdf(tmp_path / f"{kind}.pdf", 1))
        engine.add_markup(doc, 0, Rect(72, 88, 300, 104), kind=kind)
        annots = engine.list_annotations(doc, 0)
        assert [a.annot_type for a in annots] == [expected]
        assert annots[0].is_markup
        doc.close()

    def test_unknown_markup_kind_rejected(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "x.pdf", 1))
        with pytest.raises(PdfEngineError):
            engine.add_markup(doc, 0, Rect(0, 0, 10, 10), kind="bogus")
        doc.close()

    def test_marked_text_and_note(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        """批注列表能给出被高亮的原文；笔记可编辑且可撤销。"""
        doc = engine.open(_make_pdf(tmp_path / "n.pdf", 1))
        words = [w for w in engine.get_words(doc, 0) if w.text in ("beta", "gamma")]
        rect = Rect(words[0].rect.x0, words[0].rect.y0, words[-1].rect.x1, words[-1].rect.y1)
        engine.add_highlight(doc, 0, rect)
        annot = engine.list_annotations(doc, 0)[0]
        assert annot.marked_text == "beta gamma"
        assert annot.index == 0

        engine.set_annotation_content(doc, 0, 0, "关键结论")
        assert engine.list_annotations(doc, 0)[0].content == "关键结论"
        engine.undo(doc)
        assert engine.list_annotations(doc, 0)[0].content == ""
        with pytest.raises(PdfEngineError):
            engine.set_annotation_content(doc, 0, 5, "x")
        doc.close()


# ---------------------------------------------------------------- R3-19 批注类型常量
def test_annotation_type_constants_match_pymupdf() -> None:
    assert AnnotationType.SQUIGGLY == pymupdf.PDF_ANNOT_SQUIGGLY
    assert AnnotationType.SQUARE == pymupdf.PDF_ANNOT_SQUARE
    assert AnnotationType.CIRCLE == pymupdf.PDF_ANNOT_CIRCLE
    assert AnnotationType.STRIKEOUT == pymupdf.PDF_ANNOT_STRIKE_OUT
    assert AnnotationType.UNDERLINE == pymupdf.PDF_ANNOT_UNDERLINE
    assert AnnotationType.STAMP == pymupdf.PDF_ANNOT_STAMP
    assert AnnotationType.FREE_TEXT == pymupdf.PDF_ANNOT_FREE_TEXT


# ---------------------------------------------------------------- R3-18 搜索片段
class TestSearchSnippet:
    def test_snippet_is_whole_line(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "s.pdf", 1))
        hits = engine.search(doc, "gamma").hits
        assert hits[0].text == "alpha beta gamma delta epsilon zeta"
        doc.close()

    def test_two_column_line_does_not_mix_columns(self) -> None:
        """同一纵向位置的另一栏（不同 block）不能混进片段。"""
        words = [
            (50, 100, 80, 110, "left", 0, 0, 0),
            (85, 100, 120, 110, "col", 0, 0, 1),
            (320, 100, 360, 110, "right", 1, 0, 0),
            (365, 100, 400, 110, "side", 1, 0, 1),
        ]
        assert _line_text(words, Rect(320, 100, 360, 110)) == "right side"


# ---------------------------------------------------------------- R3-17 水印
class TestWatermark:
    def test_watermark_is_translucent_and_centered(
        self, engine: PyMuPdfEngine, tmp_path: Path
    ) -> None:
        doc = engine.open(_make_pdf(tmp_path / "w.pdf", 1))
        engine.add_text_watermark(doc, 0, "内部机密文件")
        page = doc.native[0]
        spans = [
            s
            for b in page.get_text("dict")["blocks"]
            for line in b.get("lines", [])
            for s in line["spans"]
            if "机密" in s["text"]
        ]
        x0, _, x1, _ = spans[0]["bbox"]
        left, right = x0, page.rect.width - x1
        assert abs(left - right) < 12, f"中文水印应水平居中：左 {left:.0f} / 右 {right:.0f}"
        content = b"".join(doc.native.xref_stream(x) for x in page.get_contents())
        assert b" gs" in content, "水印必须带透明度（ExtGState），否则会遮住正文"
        doc.close()

    def test_watermark_all_pages_single_undo(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "wa.pdf", 3))
        engine.add_text_watermark(doc, [0, 1, 2], "DRAFT")
        assert all("DRAFT" in engine.extract_text(doc, i).full_text for i in range(3))
        engine.undo(doc)
        assert all("DRAFT" not in engine.extract_text(doc, i).full_text for i in range(3))
        doc.close()


# ---------------------------------------------------------------- R3-06 / R3-07 保存
class TestSaveToOriginal:
    def test_full_save_to_original_routes_to_incremental(
        self, engine: PyMuPdfEngine, tmp_path: Path
    ) -> None:
        """「另存为」选中原文件：旧实现必报 save to original must be incremental。"""
        path = _make_pdf(tmp_path / "o.pdf", 1)
        doc = engine.open(path)
        engine.add_highlight(doc, 0, Rect(72, 60, 200, 76))
        engine.save(doc, path)  # incremental=False
        doc.close()
        check = engine.open(path)
        assert len(engine.list_annotations(check, 0)) == 1
        check.close()

    def test_repaired_file_gives_clear_reason(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        path = _make_pdf(tmp_path / "broken.pdf", 1)
        raw = path.read_bytes()
        idx = raw.rfind(b"startxref")
        path.write_bytes(raw[:idx] + b"startxref\n999999\n%%EOF\n")
        doc = engine.open(path)
        engine.add_highlight(doc, 0, Rect(72, 60, 200, 76))
        with pytest.raises(FileOpenError, match="已被自动修复"):
            engine.save(doc, path, incremental=True)
        # 另存为新文件仍然可以
        engine.save(doc, tmp_path / "fixed.pdf")
        doc.close()
        assert (tmp_path / "fixed.pdf").stat().st_size > 0


# ---------------------------------------------------------------- R3-08 合并失败不丢撤销历史
class TestMergeFailure:
    @pytest.mark.parametrize("other", ["encrypted.pdf", "corrupt.pdf"])
    def test_failed_merge_keeps_undo_history(
        self, engine: PyMuPdfEngine, tmp_path: Path, other: str
    ) -> None:
        doc = engine.open(_make_pdf(tmp_path / "base.pdf", 2))
        engine.add_highlight(doc, 0, Rect(72, 60, 200, 76))
        assert engine.can_undo(doc)
        with pytest.raises(PdfEngineError):
            engine.merge(doc, SAMPLES / other)
        assert engine.can_undo(doc), "合并失败不能白白丢掉撤销历史"
        assert engine.page_count(doc) == 2
        engine.render_page(doc, 0)
        doc.close()

    def test_encrypted_other_reports_password(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "base.pdf", 1))
        with pytest.raises(PasswordRequiredError):
            engine.merge(doc, SAMPLES / "encrypted.pdf")
        doc.close()

    def test_successful_merge_still_works(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "base.pdf", 2))
        added = engine.merge(doc, _make_pdf(tmp_path / "other.pdf", 3))
        assert added == 3
        assert engine.page_count(doc) == 5
        doc.close()


# ---------------------------------------------------------------- 目录与链接
class TestTocAndLinks:
    @pytest.fixture
    def linked_pdf(self, tmp_path: Path) -> Path:
        path = tmp_path / "links.pdf"
        d = pymupdf.open()
        for i in range(3):
            d.new_page(width=400, height=600).insert_text((72, 72), f"page {i + 1}")
        d[0].insert_link(
            {
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(72, 60, 200, 80),
                "page": 2,
                "to": pymupdf.Point(50, 300),
            }
        )
        d[1].insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(72, 60, 200, 80),
                "uri": "https://example.org",
            }
        )
        d.set_toc([[1, "Intro", 1], [2, "Method", 2], [1, "References", 3]])
        d.save(str(path))
        d.close()
        return path

    def test_toc(self, engine: PyMuPdfEngine, linked_pdf: Path) -> None:
        doc = engine.open(linked_pdf)
        toc = engine.get_toc(doc)
        assert [(e.level, e.title, e.page_num) for e in toc] == [
            (1, "Intro", 0),
            (2, "Method", 1),
            (1, "References", 2),
        ]
        doc.close()

    def test_links(self, engine: PyMuPdfEngine, linked_pdf: Path) -> None:
        doc = engine.open(linked_pdf)
        internal = engine.get_links(doc, 0)
        assert len(internal) == 1 and internal[0].is_internal and internal[0].page_num == 2
        assert internal[0].rect == Rect(72, 60, 200, 80)
        external = engine.get_links(doc, 1)
        assert external[0].is_external and external[0].uri == "https://example.org"
        assert engine.get_links(doc, 2) == ()
        doc.close()

    def test_no_toc(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        doc = engine.open(_make_pdf(tmp_path / "plain.pdf", 1))
        assert engine.get_toc(doc) == ()
        doc.close()


def test_open_error_messages_hide_full_path(engine: PyMuPdfEngine, tmp_path: Path) -> None:
    """面向用户的消息只给文件名（完整路径进日志）。"""
    missing = tmp_path / "secret_dir" / "missing.pdf"
    with pytest.raises(FileOpenError) as info:
        engine.open(missing)
    assert "missing.pdf" in str(info.value)
    assert "secret_dir" not in str(info.value)
