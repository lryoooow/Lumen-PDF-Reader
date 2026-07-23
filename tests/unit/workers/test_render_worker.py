"""渲染线程测试。

验证 RenderWorker 能：
1. 在独立线程加载文档
2. 异步渲染页面并通过信号返回结果
3. 正确处理错误（损坏文件、越界页码）
4. 取消机制工作
5. 线程安全（PyMuPDF 非线程安全约束被遵守）
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from pdfreader.engines.types import PixmapData
from pdfreader.workers import RenderWorker

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"
CORRUPT = SAMPLES / "corrupt.pdf"


def _wait_signal(signal, timeout_ms=3000):  # type: ignore[no-untyped-def]
    """阻塞等待信号，返回 (是否触发, 参数列表)。"""
    loop = QEventLoop()
    results: list = []

    def on_signal(*args):  # type: ignore[no-untyped-def]
        results.append(args)
        loop.quit()

    signal.connect(on_signal)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    signal.disconnect(on_signal)
    return (len(results) > 0, results[0] if results else ())


class TestLoadDocument:
    def test_load_and_render(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(SIMPLE)

        # 启动线程，等待文档加载信号
        triggered, args = _wait_signal_with_start(worker, "documentLoaded")
        try:
            assert triggered, "文档加载超时"
            page_count = args[0]
            assert page_count == 3
        finally:
            _shutdown(worker)

    def test_load_corrupt_fails(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(CORRUPT)

        triggered, args = _wait_signal_with_start(worker, "documentLoadFailed")
        try:
            assert triggered, "应触发加载失败信号"
            assert "损坏" in args[0] or "无法" in args[0]
        finally:
            _shutdown(worker)


class TestRender:
    def test_render_single_page(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(SIMPLE)

        triggered, _ = _wait_signal_with_start(worker, "documentLoaded")
        assert triggered

        # 请求渲染第 0 页
        worker.request_render(0, 1.0)
        triggered, args = _wait_signal(worker.pageRendered)
        try:
            assert triggered, "渲染超时"
            page_num, zoom, data = args
            assert page_num == 0
            assert zoom == 1.0
            assert isinstance(data, PixmapData)
            assert data.width == 595
        finally:
            _shutdown(worker)

    def test_render_multiple_pages(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(SIMPLE)
        _wait_signal_with_start(worker, "documentLoaded")

        results: list[int] = []
        loop = QEventLoop()

        def on_rendered(page_num, zoom, data):  # type: ignore[no-untyped-def]
            results.append(page_num)
            if len(results) >= 3:
                loop.quit()

        worker.pageRendered.connect(on_rendered)
        worker.start()
        time.sleep(0.2)  # 等文档加载
        worker.request_render(0, 1.0)
        worker.request_render(1, 1.0)
        worker.request_render(2, 1.0)

        QTimer.singleShot(3000, loop.quit)
        loop.exec()

        try:
            assert sorted(results) == [0, 1, 2]
        finally:
            worker.pageRendered.disconnect(on_rendered)
            _shutdown(worker)

    def test_render_out_of_range_reports_failure(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(SIMPLE)
        _wait_signal_with_start(worker, "documentLoaded")

        worker.request_render(999, 1.0)
        triggered, args = _wait_signal(worker.renderFailed)
        try:
            assert triggered, "应触发渲染失败信号"
            page_num, message = args
            assert page_num == 999
            assert "越界" in message
        finally:
            _shutdown(worker)


class TestCancel:
    def test_cancel_clears_queue(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(SIMPLE)
        _wait_signal_with_start(worker, "documentLoaded")

        # 投递多个请求后立即取消
        for i in range(5):
            worker.request_render(i, 1.0)
        worker.request_cancel()

        # 队列应被清空（通过观察：短时间内最多渲染 1 个——已被弹出处理的那个）
        time.sleep(0.1)
        assert len(worker._queue) == 0
        _shutdown(worker)


class TestCleanup:
    def test_shutdown_stops_thread(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        worker = RenderWorker()
        worker.load_document(SIMPLE)
        worker.start()
        time.sleep(0.3)  # 等加载
        assert worker.isRunning()

        worker.shutdown()
        worker.wait(3000)
        assert worker.isRunning() is False


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------
def _wait_signal_with_start(worker: RenderWorker, signal_name: str, timeout_ms=3000):  # type: ignore[no-untyped-def]
    """启动 worker 并等待指定信号。"""
    signal = getattr(worker, signal_name)
    loop = QEventLoop()
    results: list = []

    def on_signal(*args):  # type: ignore[no-untyped-def]
        results.append(args)
        loop.quit()

    signal.connect(on_signal)
    worker.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    with contextlib.suppress(RuntimeError):
        signal.disconnect(on_signal)
    return (len(results) > 0, results[0] if results else ())


def _shutdown(worker: RenderWorker) -> None:
    """安全关闭 worker。"""
    worker.shutdown()
    worker.wait(3000)
