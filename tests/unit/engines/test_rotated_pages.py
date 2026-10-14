"""旋转页（/Rotate ≠ 0）的坐标帧与读取安全性测试。

背景（也是引擎实现里那条约定的由来）：

* 本引擎 ``open()`` 必定开启 PyMuPDF 日志（journal）以支持撤销/重做；
* 而 PyMuPDF 的 ``Page.get_text*`` / ``Page.search_for`` 会**临时把页面 /Rotate 置 0**
  再建 TextPage，这一步本身就是写 PDF 对象，journal 开启时会抛
  ``Can't alter an object other than in an operation``；
* 批注与页面内容流又都按**未旋转帧**存坐标，与渲染位图 / GUI 点击换算（可见帧）不一致。

引擎因此统一约定：**对外一律可见帧坐标**（``page.rect`` 那一套）。本文件把这个约定
用可观测的事实钉住：取词/搜索不崩、坐标落在可见帧、高亮正好压在框住的文字上、
插入的内容落在用户点的位置且保持正立、橡皮擦点得中。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest

from pdfreader.engines import EngineDocument, PyMuPdfEngine
from pdfreader.engines.types import InkStroke, PixmapData, Point, Rect, WordBox

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
ROTATED = SAMPLES / "rotated.pdf"

# 样例第 0 页：400x600 且 /Rotate=90 → 可见帧 600x400
VISIBLE_SIZE = (600.0, 400.0)


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


@pytest.fixture
def work_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "rotated.pdf"
    shutil.copy(ROTATED, dst)
    return dst


@pytest.fixture
def handle(engine: PyMuPdfEngine, work_pdf: Path) -> Iterator[EngineDocument]:
    doc = engine.open(work_pdf)
    yield doc
    engine.close(doc)


# ----------------------------------------------------------------------
# 断言工具
# ----------------------------------------------------------------------
def _diff_bbox(before: PixmapData, after: PixmapData) -> tuple[int, int, int, int] | None:
    """逐个像素比较两张同尺寸位图，返回变化像素的包围盒；完全一致时返回 None。

    只对内容不同的行做逐像素扫描（行相同的用 bytes 直接比较），够快也够准。
    """
    assert (before.width, before.height) == (after.width, after.height)
    x0 = y0 = 10**9
    x1 = y1 = -1
    for y in range(before.height):
        start = y * before.stride
        end = start + before.width * before.n
        row_a = before.samples[start:end]
        row_b = after.samples[start:end]
        if row_a == row_b:
            continue
        y0 = min(y0, y)
        y1 = max(y1, y)
        for i in range(len(row_a)):
            if row_a[i] != row_b[i]:
                x = i // before.n
                x0 = min(x0, x)
                x1 = max(x1, x)
    if x1 < 0:
        return None
    return (x0, y0, x1, y1)


def _overlap_ratio(box: tuple[int, int, int, int], rect: Rect) -> float:
    """像素包围盒与可见帧矩形的重叠面积 / 矩形面积。"""
    x0 = max(box[0], rect.x0)
    y0 = max(box[1], rect.y0)
    x1 = min(box[2], rect.x1)
    y1 = min(box[3], rect.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return ((x1 - x0) * (y1 - y0)) / (rect.width * rect.height)


def _find_word(engine: PyMuPdfEngine, doc: EngineDocument, page_num: int, text: str) -> WordBox:
    for word in engine.get_words(doc, page_num):
        if text in word.text:
            return word
    raise AssertionError(f"第 {page_num} 页没找到词：{text}")


def _make_png(target: Path, width: int, height: int) -> Path:
    """造一张纯色 PNG（尺寸可控，便于断言「图片铺满框且没被转 90°」）。"""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pix.set_rect(pix.irect, (200, 30, 30))
    pix.save(str(target))
    return target


# ----------------------------------------------------------------------
# 读取路径：旋转页上取文本/取词/搜索必须可用
# ----------------------------------------------------------------------
class TestRotatedPageReads:
    def test_extract_text_works(self, engine: PyMuPdfEngine, handle: EngineDocument) -> None:
        """旋转页也能取全文（旧实现在此处抛
        "Can't alter an object other than in an operation"）。"""
        text = engine.extract_text(handle, 0)
        assert "Rotated Page Keyword" in text.full_text
        assert text.spans  # 带坐标的片段同样要能取到

    def test_get_words_works(self, engine: PyMuPdfEngine, handle: EngineDocument) -> None:
        words = engine.get_words(handle, 0)
        assert [w.text for w in words] == ["Rotated", "Page", "Keyword"]

    def test_search_covers_rotated_and_plain_pages(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        """搜索不再因某一页旋转而整体失败。"""
        result = engine.search(handle, "Keyword")  # type: ignore[arg-type]
        assert {hit.page_num for hit in result.hits} == {0, 1}

    def test_iter_pages_text_works(self, engine: PyMuPdfEngine, handle: EngineDocument) -> None:
        texts = [p.full_text for p in engine.iter_pages_text(handle)]  # type: ignore[arg-type]
        assert len(texts) == 2
        assert "Rotated" in texts[0] and "Plain" in texts[1]

    def test_page_bounds_is_visible_frame(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        bounds = engine.page_bounds(handle, 0)
        assert (bounds.width, bounds.height) == VISIBLE_SIZE

    def test_word_coords_are_visible_frame(
        self, engine: PyMuPdfEngine, handle: EngineDocument, work_pdf: Path
    ) -> None:
        """用独立手段校验坐标帧：不旋转时取到的原始词框 × rotation_matrix。

        刻意不复用引擎自己的实现——否则「读、写两边一起错」也能自洽通过，
        而 GUI 仍然是错的。
        """
        external = pymupdf.open(work_pdf)
        try:
            page = external[0]
            raw = pymupdf.Rect(page.get_text("words")[0][:4])
            expected = raw * page.rotation_matrix
        finally:
            external.close()

        word = engine.get_words(handle, 0)[0]  # type: ignore[arg-type]
        assert word.rect.x0 == pytest.approx(expected.x0, abs=1.0)
        assert word.rect.y0 == pytest.approx(expected.y0, abs=1.0)
        assert word.rect.x1 == pytest.approx(expected.x1, abs=1.0)
        assert word.rect.y1 == pytest.approx(expected.y1, abs=1.0)
        # 未旋转帧只有 400 宽，可见帧的 x 会超过它 —— 顺便确认取的是可见帧
        assert word.rect.x0 > 400


# ----------------------------------------------------------------------
# 写入路径：内容必须落在用户（可见帧）指定/点击的位置
# ----------------------------------------------------------------------
class TestRotatedPageWrites:
    def test_highlight_lands_on_framed_words(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        """用词框加高亮（GUI 的实际做法）→ 渲染变化必须压在被框住的文字上。"""
        word = engine.get_words(handle, 0)[0]  # type: ignore[arg-type]
        before = engine.render_page(handle, 0, 1.0)
        engine.add_highlight(handle, 0, word.rect)
        after = engine.render_page(handle, 0, 1.0)

        box = _diff_bbox(before, after)
        assert box is not None, "高亮没有产生任何像素变化"
        assert _overlap_ratio(box, word.rect) > 0.7

    def test_ink_lands_at_visible_stroke(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        """手写笔画按可见帧坐标落点（旋转页上不再被页面旋转搬走）。"""
        stroke = InkStroke(points=(Point(500.0, 200.0), Point(510.0, 240.0), Point(520.0, 280.0)))
        target = Rect(500.0, 200.0, 520.0, 280.0)

        before = engine.render_page(handle, 0, 1.0)
        engine.add_ink(handle, 0, (stroke,))
        after = engine.render_page(handle, 0, 1.0)

        box = _diff_bbox(before, after)
        assert box is not None, "手写没有产生任何像素变化"
        assert _overlap_ratio(box, target) > 0.5

    def test_insert_text_lands_at_clicked_point(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        """时间戳插入点 = 用户点击的可见帧位置，且文字保持横排。"""
        click = Point(300.0, 200.0)
        engine.insert_text(handle, 0, click, "STAMP")

        word = _find_word(engine, handle, 0, "STAMP")
        assert abs(word.rect.x0 - click.x) <= 4
        assert abs(word.rect.y1 - click.y) <= 4
        assert word.rect.width > word.rect.height, "旋转页上插入的文字被转成了竖排"

    def test_insert_image_fills_visible_rect(
        self, engine: PyMuPdfEngine, handle: EngineDocument, tmp_path: Path
    ) -> None:
        """签名图片落在用户框选的可见帧矩形里，按原比例铺满且没被转 90°。"""
        image = _make_png(tmp_path / "sig.png", width=200, height=100)  # 2:1
        target = Rect(120.0, 160.0, 220.0, 210.0)  # 2:1，比例一致 → 正好铺满

        before = engine.render_page(handle, 0, 1.0)
        engine.insert_image(handle, 0, target, image)
        after = engine.render_page(handle, 0, 1.0)

        box = _diff_bbox(before, after)
        assert box is not None, "插入图片没有产生任何像素变化"
        assert abs(box[0] - target.x0) <= 6
        assert abs(box[1] - target.y0) <= 6
        assert abs(box[2] - target.x1) <= 6
        assert abs(box[3] - target.y1) <= 6
        assert (box[2] - box[0]) > (box[3] - box[1]), "图片被页面旋转带着转了 90°"

    def test_watermark_is_upright(self, engine: PyMuPdfEngine, handle: EngineDocument) -> None:
        engine.add_text_watermark(handle, 0, "WATERMARK")
        word = _find_word(engine, handle, 0, "WATERMARK")
        assert word.rect.width > word.rect.height

    def test_note_is_hittable_at_visible_point(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        """橡皮擦用可见帧点查批注必须命中（批注 rect 是未旋转帧，需折算）。"""
        point = Point(300.0, 200.0)
        engine.add_text_annot(handle, 0, point, "note")

        assert engine.annotation_index_at(handle, 0, point) is not None  # type: ignore[arg-type]
        listed = engine.list_annotations(handle, 0)
        assert len(listed) == 1
        # 列举出来的矩形同样是可见帧：贴着刚才点击的位置（便签图标沿左上展开）
        assert abs(listed[0].rect.x0 - point.x) <= 20
        assert abs(listed[0].rect.y1 - point.y) <= 20
        assert engine.delete_annotation(handle, 0, 0) is True  # type: ignore[arg-type]

    def test_undo_after_edit_on_rotated_page(
        self, engine: PyMuPdfEngine, handle: EngineDocument
    ) -> None:
        """旋转页上的编辑同样可撤销，撤销后仍能渲染与取文本。"""
        word = engine.get_words(handle, 0)[0]  # type: ignore[arg-type]
        engine.add_highlight(handle, 0, word.rect)
        assert engine.can_undo(handle) is True

        assert engine.undo(handle) is True
        assert engine.render_page(handle, 0, 1.0).width > 0  # type: ignore[arg-type]
        assert engine.extract_text(handle, 0).full_text.strip()  # type: ignore[arg-type]
