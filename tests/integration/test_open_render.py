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


class TestSaveAndAnnotate:
    """批注 / 保存 / 修改状态的端到端行为（真实引擎）。"""

    def test_annotation_via_controller_marks_modified(self, controller: DocumentController) -> None:
        from pdfreader.engines.types import Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        assert controller.is_modified is False
        assert controller.add_highlight(0, Rect(40, 40, 300, 70)) is True
        assert controller.is_modified is True
        controller.close_document()

    def test_save_copy_keeps_modified_and_writes(
        self, controller: DocumentController, tmp_path: Path
    ) -> None:
        from pdfreader.engines.types import Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        controller.add_highlight(0, Rect(40, 40, 300, 70))
        out = tmp_path / "copy.pdf"
        assert controller.save_copy(out) is True
        assert out.exists()
        assert controller.is_modified is True  # 副本不清除修改标记
        controller.close_document()

    def test_save_as_updates_path_and_clears_modified(
        self, controller: DocumentController, tmp_path: Path
    ) -> None:
        from pdfreader.engines.types import Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        controller.add_highlight(0, Rect(40, 40, 300, 70))
        out = tmp_path / "saved.pdf"
        assert controller.save_as(out) is True
        assert controller.is_modified is False
        assert controller.document is not None and controller.document.path == out
        controller.close_document()

    def test_save_in_place_incremental(
        self, controller: DocumentController, tmp_path: Path
    ) -> None:
        import shutil

        from pdfreader.engines.types import Rect

        work = tmp_path / "work.pdf"
        shutil.copy(SAMPLES / "simple_text.pdf", work)
        controller.open_file(work)
        controller.add_highlight(0, Rect(40, 40, 300, 70))
        assert controller.save_in_place() is True  # 增量存回原文件
        assert controller.is_modified is False
        controller.close_document()

    def test_save_in_place_after_save_as(
        self, controller: DocumentController, tmp_path: Path
    ) -> None:
        """另存为之后再「保存」必须成功（原 P0-1：增量保存报 needs original file）。"""
        import shutil

        from pdfreader.engines.types import Rect

        source = tmp_path / "source.pdf"
        shutil.copy(SAMPLES / "simple_text.pdf", source)
        controller.open_file(source)
        out = tmp_path / "saved_as.pdf"
        controller.add_highlight(0, Rect(40, 40, 300, 70))
        assert controller.save_as(out) is True

        controller.add_highlight(0, Rect(40, 100, 300, 130))
        assert controller.save_in_place() is True  # 目标已不是原文件 → 整份保存
        assert controller.is_modified is False

        # 批注确实落到新文件里
        reread = DocumentController()
        assert reread.open_file(out) is True
        assert len(reread.list_annotations(0)) == 2
        reread.close_document()
        controller.close_document()

    def test_edit_failure_records_error(self, controller: DocumentController) -> None:
        # 无文档时删除页失败，且记录具体原因（不再静默）
        assert controller.delete_page(0) is False
        assert controller.last_error != ""


class TestUndoRedoAndInsert:
    """撤销/重做、橡皮擦删除、插入图片/文字（真实引擎 + 日志）。"""

    def test_undo_redo_annotation(self, controller: DocumentController) -> None:
        from pdfreader.engines.types import Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        assert controller.can_undo() is False
        before = controller.render_page(0, 1.0).samples
        controller.add_highlight(0, Rect(40, 40, 300, 70))
        assert controller.can_undo() is True
        assert controller.render_page(0, 1.0).samples != before
        assert controller.undo() is True
        assert controller.render_page(0, 1.0).samples == before  # 撤销复原
        assert controller.can_redo() is True
        assert controller.redo() is True
        assert controller.render_page(0, 1.0).samples != before  # 重做恢复
        controller.close_document()

    def test_eraser_hit_and_delete(self, controller: DocumentController) -> None:
        from pdfreader.engines.types import Point, Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        controller.add_highlight(0, Rect(100, 100, 200, 118))
        anns = controller.list_annotations(0)
        assert len(anns) == 1
        r = anns[0].rect
        idx = controller.annotation_index_at(0, Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
        assert idx == 0
        assert controller.delete_annotation(0, idx) is True
        assert controller.list_annotations(0) == []
        controller.close_document()

    def test_insert_text_and_image_mark_modified(
        self, controller: DocumentController, tmp_path: Path
    ) -> None:
        from PySide6.QtGui import QImage

        from pdfreader.engines.types import Point, Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        before = controller.render_page(0, 1.0).samples
        assert controller.insert_text(0, Point(50, 400), "2026-07-24") is True
        assert controller.is_modified is True
        img = QImage(24, 24, QImage.Format.Format_ARGB32)
        img.fill(0xFF2266EE)
        sig = tmp_path / "sig.png"
        img.save(str(sig))
        assert controller.insert_image(0, Rect(50, 300, 150, 340), sig) is True
        assert controller.render_page(0, 1.0).samples != before
        controller.close_document()

    def test_merge_still_works_with_journalling(self, controller: DocumentController) -> None:
        from pdfreader.engines.types import Rect

        controller.open_file(SAMPLES / "simple_text.pdf")
        controller.add_highlight(0, Rect(40, 40, 100, 60))  # 先启用日志
        added = controller.merge(SAMPLES / "with_tables.pdf")  # 合并仍须可用
        assert added > 0
        controller.close_document()


class TestSearchIntegration:
    """端到端搜索集成测试。"""

    def test_search_via_worker(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        from pdfreader.workers import SearchWorker

        worker = SearchWorker()
        worker.setup(SAMPLES / "simple_text.pdf", "PyMuPDF")
        results: list = []
        worker.searchCompleted.connect(lambda q, r: results.append((q, r)))
        with qtbot.waitSignal(worker.searchCompleted, timeout=10000):
            worker.start()
        worker.wait(3000)
        query, result = results[0]
        assert query == "PyMuPDF"
        assert result.hit_count == 3
        assert set(result.page_numbers) == {0, 1, 2}
