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
        """完整链路：打开 → 渲染 → 显示到 PdfView。"""
        view = PdfView()
        qtbot.addWidget(view)

        controller.open_file(SAMPLES / "simple_text.pdf")
        data = controller.render_page(0, zoom=1.0)
        view.show_pixmap_data(data, page_num=0)

        assert view.current_page_num == 0
        assert view._scene.items() != []  # 场景中有内容
        controller.close_document()
