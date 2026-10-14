"""格式转换后台线程测试（R2-P1-5）。

转换本身用 `ConvertService`（txt 导出只走引擎，不依赖 pdf2docx/pdfplumber），
另外用假服务覆盖成功/失败/未知结果等分支。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.services.converter import ConvertError, ConvertResult
from pdfreader.workers import ConvertWorker

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"


@pytest.fixture
def worker(qtbot) -> ConvertWorker:  # type: ignore[no-untyped-def]
    w = ConvertWorker()
    yield w
    if w.isRunning():
        w.wait(10000)


class TestConvertWorkerDirectRun:
    def test_run_without_setup_fails(self, worker: ConvertWorker) -> None:
        errors: list[str] = []
        worker.convertFailed.connect(errors.append)
        worker.run()
        assert errors == ["未指定转换任务"]

    def test_run_reports_service_error(self, worker: ConvertWorker, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise ConvertError("不支持的格式:x")

        monkeypatch.setattr(worker._service, "convert", boom)
        errors: list[str] = []
        worker.convertFailed.connect(errors.append)
        worker.setup(SIMPLE, tmp_path / "out", "x")
        worker.run()
        assert errors == ["不支持的格式:x"]

    def test_run_reports_unexpected_result(
        self, worker: ConvertWorker, monkeypatch, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(worker._service, "convert", lambda *a, **k: object())
        errors: list[str] = []
        worker.convertFailed.connect(errors.append)
        worker.setup(SIMPLE, tmp_path / "out", "txt")
        worker.run()
        assert errors == ["转换返回了意外的结果"]


class TestConvertWorkerThread:
    def test_txt_export_end_to_end(self, worker: ConvertWorker, qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """真实转换：PDF→TXT 在后台线程跑完并带回输出路径。"""
        results: list[ConvertResult] = []
        worker.convertFinished.connect(results.append)
        worker.setup(SIMPLE, tmp_path / "out", "txt")

        with qtbot.waitSignal(worker.convertFinished, timeout=30000):
            worker.start()
        worker.wait(5000)

        assert len(results) == 1
        assert results[0].output_path.exists()
        assert "PyMuPDF" in results[0].output_path.read_text(encoding="utf-8")

    def test_unknown_format_reports_failure(self, worker: ConvertWorker, qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
        errors: list[str] = []
        worker.convertFailed.connect(errors.append)
        worker.setup(SIMPLE, tmp_path / "out", "no-such-format")

        with qtbot.waitSignal(worker.convertFailed, timeout=10000):
            worker.start()
        assert "不支持的格式" in errors[0]
