"""OCR 线程测试。

OCR 引擎（RapidOCR）用轻量替身替代：本文件只验证「渲染 → 编码 → 交给 OCR 服务」
这条管线是否连通（回归点：旧实现用错误的 Pixmap 构造参数编码 PNG，
每页都会抛异常并被静默跳过，整本 OCR 无结果）。

多数用例直接调用 ``run()``（与线程内执行的是同一段代码），只有一条端到端用例
真正 ``start()`` 线程，用来验证 Qt 线程接线本身没问题。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.engines import PyMuPdfEngine, RenderError
from pdfreader.engines.exceptions import FileOpenError
from pdfreader.engines.types import PixmapData
from pdfreader.services.ocr import OcrError, OcrResult
from pdfreader.workers import OcrWorker

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class _FakeOcr:
    """OCR 服务替身：记录收到的图片字节，返回固定结果。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[bytes, int]] = []
        self._error = error

    def ocr_image(self, image_bytes: bytes, page_num: int = 0) -> OcrResult:
        self.calls.append((image_bytes, page_num))
        if self._error is not None:
            raise self._error
        return OcrResult(page_num=page_num, text=f"page-{page_num}", blocks=(("x", 0.9),))


def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise RenderError("模拟引擎故障")


@pytest.fixture
def worker(qtbot) -> OcrWorker:  # type: ignore[no-untyped-def]
    w = OcrWorker()
    yield w
    if w.isRunning():
        w.shutdown()
        w.wait(3000)


@pytest.fixture
def fake_ocr(worker: OcrWorker) -> _FakeOcr:
    fake = _FakeOcr()
    worker._ocr = fake  # type: ignore[assignment]
    return fake


def _collect(worker: OcrWorker) -> dict[str, list]:
    """把 worker 的信号收集到列表里，供断言使用。"""
    box: dict[str, list] = {"results": [], "done": [], "errors": [], "progress": []}
    worker.pageOcrCompleted.connect(box["results"].append)
    worker.ocrCompleted.connect(box["done"].append)
    box["cancelled"] = []
    worker.ocrCancelled.connect(box["cancelled"].append)
    worker.ocrFailed.connect(box["errors"].append)
    worker.progress.connect(lambda d, t: box["progress"].append((d, t)))
    return box


class TestOcrPage:
    def test_png_bytes_reach_ocr_service(self, worker: OcrWorker, fake_ocr: _FakeOcr) -> None:
        """单页 OCR：渲染结果被编码为 PNG 后交给 OCR 服务。"""
        doc = worker._engine.open(SIMPLE)
        try:
            result = worker._ocr_page(doc, 0)
        finally:
            worker._engine.close(doc)

        assert result is not None, "PNG 编码失败会让整本 OCR 静默无结果"
        assert result.page_num == 0
        assert len(fake_ocr.calls) == 1
        img_bytes, page_num = fake_ocr.calls[0]
        assert page_num == 0
        assert img_bytes[:8] == PNG_MAGIC
        assert len(img_bytes) > 1000  # 至少是一张真实位图，不是空字节

    def test_render_failure_skips_page(
        self, worker: OcrWorker, fake_ocr: _FakeOcr, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:  # type: ignore[no-untyped-def]
        """单页渲染失败 → 返回 None 跳过该页，且留下日志（不再静默）。"""
        doc = worker._engine.open(SIMPLE)
        try:
            monkeypatch.setattr(worker._engine, "render_page", _boom)
            with caplog.at_level("WARNING", logger="pdfreader"):
                assert worker._ocr_page(doc, 2) is None
        finally:
            worker._engine.close(doc)
        assert fake_ocr.calls == []
        assert any("第 3 页" in record.message for record in caplog.records)

    def test_encode_failure_is_not_swallowed(
        self, worker: OcrWorker, fake_ocr: _FakeOcr, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """编码失败与具体页面无关，应整体报错，而不是「跳过所有页后报成功」。"""
        doc = worker._engine.open(SIMPLE)
        try:
            monkeypatch.setattr(worker._engine, "pixmap_to_png", _boom)
            with pytest.raises(RenderError):
                worker._ocr_page(doc, 0)
        finally:
            worker._engine.close(doc)


class TestRunWithRealThread:
    def test_ocrs_all_pages(self, worker: OcrWorker, fake_ocr: _FakeOcr, qtbot) -> None:  # type: ignore[no-untyped-def]
        """端到端：在 QThread 中跑完整个文档，逐页发结果。"""
        box = _collect(worker)
        worker.setup(SIMPLE)
        with qtbot.waitSignal(worker.ocrCompleted, timeout=30000):
            worker.start()
        worker.wait(3000)

        assert [r.page_num for r in box["results"]] == [0, 1, 2]
        assert box["done"] == [3]
        assert box["progress"][-1] == (3, 3)

    def test_corrupt_file_fails(self, worker: OcrWorker, qtbot) -> None:  # type: ignore[no-untyped-def]
        box = _collect(worker)
        worker.setup(SAMPLES / "corrupt.pdf")
        with qtbot.waitSignal(worker.ocrFailed, timeout=10000):
            worker.start()
        assert len(box["errors"]) == 1


class TestRun:
    def test_page_nums_subset(self, worker: OcrWorker, fake_ocr: _FakeOcr) -> None:
        """只 OCR 指定页。"""
        box = _collect(worker)
        worker.setup(SIMPLE, page_nums=[1])
        worker.run()

        assert [r.page_num for r in box["results"]] == [1]
        assert box["done"] == [1]
        assert box["errors"] == []

    def test_skipped_page_keeps_going(
        self, worker: OcrWorker, fake_ocr: _FakeOcr, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """某一页渲染失败只跳过该页，其余页照常 OCR，进度照走。"""
        real_render = worker._engine.render_page

        def flaky(doc, page_num, zoom=1.0):  # type: ignore[no-untyped-def]
            if page_num == 1:
                raise RenderError("坏页")
            return real_render(doc, page_num, zoom)

        monkeypatch.setattr(worker._engine, "render_page", flaky)
        box = _collect(worker)
        worker.setup(SIMPLE)
        worker.run()

        assert [r.page_num for r in box["results"]] == [0, 2]
        assert box["done"] == [3]
        assert box["progress"] == [(1, 3), (2, 3), (3, 3)]

    def test_missing_path_fails(self, worker: OcrWorker) -> None:
        box = _collect(worker)
        worker.run()
        assert box["errors"] == ["未指定文档路径"]
        assert box["done"] == []

    def test_engine_open_error_passthrough(
        self, worker: OcrWorker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """引擎的打开错误（如密码错误）原文透出，不再包一层。"""

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileOpenError("需要密码")

        monkeypatch.setattr(worker._engine, "open", boom)
        box = _collect(worker)
        worker.setup(SIMPLE)
        worker.run()
        assert box["errors"] == ["需要密码"]
        assert box["done"] == []

    def test_ocr_unavailable_fails(
        self, worker: OcrWorker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未安装 OCR 依赖时给出明确提示。"""
        import pdfreader.workers.ocr_worker as mod

        monkeypatch.setattr(mod, "OCR_AVAILABLE", False)
        box = _collect(worker)
        worker.setup(SIMPLE)
        worker.run()
        assert "OCR 功能不可用" in box["errors"][0]
        assert box["done"] == []

    def test_open_failure_is_reported(
        self, worker: OcrWorker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非引擎异常的打开失败也要变成 ocrFailed，而不是崩线程。"""

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("模拟打开失败")

        monkeypatch.setattr(worker._engine, "open", boom)
        box = _collect(worker)
        worker.setup(SIMPLE)
        worker.run()
        assert "打开文档失败" in box["errors"][0]
        assert box["done"] == []

    def test_ocr_error_aborts_job(self, worker: OcrWorker) -> None:
        """OCR 服务故障 → ocrFailed，且不再报"全部完成"。"""
        worker._ocr = _FakeOcr(OcrError("OCR 引擎炸了"))  # type: ignore[assignment]
        box = _collect(worker)
        worker.setup(SIMPLE, page_nums=[0])
        worker.run()
        assert box["errors"] == ["OCR 引擎炸了"]
        assert box["done"] == []

    def test_unexpected_error_aborts_job(self, worker: OcrWorker) -> None:
        """未知异常也要转成 ocrFailed。"""
        worker._ocr = _FakeOcr(ValueError("意外"))  # type: ignore[assignment]
        box = _collect(worker)
        worker.setup(SIMPLE, page_nums=[0])
        worker.run()
        assert "OCR 失败" in box["errors"][0]
        assert box["done"] == []

    def test_encode_failure_emits_ocr_failed(
        self, worker: OcrWorker, fake_ocr: _FakeOcr, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """编码失败时 run() 发 ocrFailed，而不是发 ocrCompleted(0 结果)。"""
        monkeypatch.setattr(worker._engine, "pixmap_to_png", _boom)
        box = _collect(worker)
        worker.setup(SIMPLE, page_nums=[0])
        worker.run()
        assert "模拟引擎故障" in box["errors"][0]
        assert box["done"] == []

    def test_shutdown_reports_cancelled_not_done(
        self, worker: OcrWorker, fake_ocr: _FakeOcr
    ) -> None:
        """取消要发 ocrCancelled，不能发 ocrCompleted —— 否则界面会弹「OCR 完成」。"""
        box = _collect(worker)
        worker.setup(SIMPLE)
        worker.shutdown()
        worker.run()
        assert box["done"] == []
        assert box["cancelled"] == [0]
        assert fake_ocr.calls == []

    def test_cancel_midway_reports_processed(
        self, worker: OcrWorker, fake_ocr: _FakeOcr, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跑到一半被取消：发 cancelled 且带上已完成页数。"""
        real_ocr_page = worker._ocr_page

        def cancel_after_first(doc, page_num):  # type: ignore[no-untyped-def]
            result = real_ocr_page(doc, page_num)
            if page_num == 0:
                worker.shutdown()  # 第 1 页做完后用户按了取消
            return result

        monkeypatch.setattr(worker, "_ocr_page", cancel_after_first)
        box = _collect(worker)
        worker.setup(SIMPLE)
        worker.run()

        assert [r.page_num for r in box["results"]] == [0]
        assert box["done"] == []
        assert box["cancelled"] == [1]


class TestOcrZoomCap:
    """送 OCR 的位图长边上限：大页面不再整页送进 OCR（内存尖峰）。"""

    def test_zoom_limits_long_edge(self, worker: OcrWorker) -> None:
        from pdfreader.workers.ocr_worker import OCR_MAX_EDGE

        doc = worker._engine.open(SIMPLE)
        try:
            zoom = worker._page_zoom(doc, 0)
            data = worker._engine.render_page(doc, 0, zoom)
        finally:
            worker._engine.close(doc)

        assert zoom < 200 / 72.0, "A4 页在 200 DPI 下长边超过上限，应被压下来"
        assert max(data.width, data.height) <= OCR_MAX_EDGE + 1

    def test_zoom_default_when_bounds_unavailable(
        self, worker: OcrWorker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pdfreader.engines import PdfEngineError

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise PdfEngineError("拿不到尺寸")

        monkeypatch.setattr(worker._engine, "page_bounds", boom)
        doc = worker._engine.open(SIMPLE)
        try:
            assert worker._page_zoom(doc, 0) == 200 / 72.0
        finally:
            worker._engine.close(doc)


def test_engine_png_encode_used_by_worker() -> None:
    """引擎的 PNG 编码对 OCR DPI 下的渲染结果同样可用（管线地基）。"""
    engine = PyMuPdfEngine()
    doc = engine.open(SIMPLE)
    try:
        data: PixmapData = engine.render_page(doc, 0, 200 / 72.0)
        assert engine.pixmap_to_png(data)[:8] == PNG_MAGIC
    finally:
        engine.close(doc)
