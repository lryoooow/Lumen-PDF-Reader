"""端到端集成测试：打开真实 PDF → 渲染 → 显示。

这是阶段 3 的里程碑验证：第一次证明"引擎→模型→控制器→视图"完整链路能跑通。
用真实 PyMuPDFEngine + 真实样例 PDF。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.controllers import DocumentController, OpenResult
from pdfreader.engines import PyMuPdfEngine
from pdfreader.gui.widgets import PdfView
from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "samples"


@pytest.fixture
def controller() -> DocumentController:
    return DocumentController(engine=PyMuPdfEngine())


class TestOpenRealPdf:
    def test_open_simple_text(self, controller: DocumentController) -> None:
        opened: list[int] = []
        controller.documentOpened.connect(opened.append)
        ok = controller.open_file(SAMPLES / "simple_text.pdf")
        assert ok is True
        assert opened == [3]
        assert controller.has_document is True
        controller.close_document()

    def test_open_corrupt_fails(self, controller: DocumentController) -> None:
        failures: list[str] = []
        controller.openFailed.connect(lambda r, m: failures.append(r))
        controller.open_file(SAMPLES / "corrupt.pdf")
        assert failures[0] == OpenResult.CORRUPT

    def test_open_encrypted_needs_password(self, controller: DocumentController) -> None:
        failures: list[str] = []
        controller.openFailed.connect(lambda r, m: failures.append(r))
        controller.open_file(SAMPLES / "encrypted.pdf")
        assert failures[0] == OpenResult.NEEDS_PASSWORD

    def test_open_encrypted_with_password(self, controller: DocumentController) -> None:
        ok = controller.open_file(SAMPLES / "encrypted.pdf", password="secret")
        assert ok is True
        assert controller.page_count == 1
        controller.close_document()


class TestRenderRealPdf:
    def test_render_first_page(self, controller: DocumentController) -> None:
        controller.open_file(SAMPLES / "simple_text.pdf")
        data = controller.render_page(0, zoom=1.0)
        # A4 @ 72dpi
        assert data.width == 595
        assert data.height == 842
        assert data.n == 3
        controller.close_document()

    def test_render_to_qpixmap(self, controller: DocumentController, qtbot) -> None:  # type: ignore[no-untyped-def]
        """验证渲染数据能成功转为 QPixmap（桥接）。"""
        controller.open_file(SAMPLES / "simple_text.pdf")
        data = controller.render_page(0)
        qpix = pixmap_to_qpixmap(data)
        assert qpix.isNull() is False
        assert qpix.width() == data.width
        controller.close_document()


class TestFullPipeline:
    def test_open_render_display(self, controller: DocumentController, qtbot) -> None:  # type: ignore[no-untyped-def]
        """完整链路：打开 → 取页面尺寸 → 视图布局 → 渲染 → 填入。"""
        view = PdfView()
        qtbot.addWidget(view)

        controller.open_file(SAMPLES / "simple_text.pdf")
        page_sizes = controller.get_page_sizes()
        view.setup_document(controller.page_count, page_sizes)

        # 渲染第 0 页并填入视图
        data = controller.render_page(0, zoom=1.0)
        view.set_zoom(1.0)
        view.on_page_rendered(0, 1.0, data)

        assert view.page_count == 3
        assert view._rendered_cache.contains(0)
        controller.close_document()

    def test_get_page_sizes(self, controller: DocumentController) -> None:
        """控制器能返回各页原始尺寸。"""
        controller.open_file(SAMPLES / "simple_text.pdf")
        sizes = controller.get_page_sizes()
        assert len(sizes) == 3
        # A4 尺寸约 595x842
        for w, h in sizes:
            assert w > 0 and h > 0
        controller.close_document()


class TestRenderWorkerWithView:
    """端到端：渲染线程 + 多页视图协同工作。"""

    def test_worker_renders_to_view(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        from PySide6.QtCore import QEventLoop, QTimer

        from pdfreader.workers import RenderWorker

        view = PdfView()
        qtbot.addWidget(view)
        view.resize(400, 800)
        view.show()
        qtbot.waitExposed(view)

        worker = RenderWorker()
        worker.load_document(SAMPLES / "simple_text.pdf")
        worker.pageRendered.connect(view.on_page_rendered)

        loaded: list[int] = []
        rendered: list[int] = []
        loop = QEventLoop()

        def on_loaded(count):  # type: ignore[no-untyped-def]
            loaded.append(count)
            page_sizes = [(595, 842)] * count
            view.setup_document(count, page_sizes)
            view.set_zoom(1.0)
            # 请求渲染所有页
            for i in range(count):
                worker.request_render(i, 1.0)

        def on_rendered(page_num, zoom, data):  # type: ignore[no-untyped-def]
            rendered.append(page_num)
            if len(rendered) >= 3:
                loop.quit()

        worker.documentLoaded.connect(on_loaded)
        worker.pageRendered.disconnect(view.on_page_rendered)
        worker.pageRendered.connect(on_rendered)
        worker.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()

        try:
            assert loaded == [3]
            assert sorted(rendered) == [0, 1, 2]
            assert view.page_count == 3
        finally:
            worker.shutdown()
            worker.wait(3000)
