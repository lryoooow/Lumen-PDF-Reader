"""搜索线程测试。

使用 pytest-qt 的 qtbot.waitSignal 等待信号，避免与 pytest-qt 事件循环冲突。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.engines.types import SearchResult
from pdfreader.workers import SearchWorker

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"


@pytest.fixture
def worker(qtbot) -> SearchWorker:  # type: ignore[no-untyped-def]
    w = SearchWorker()
    yield w
    if w.isRunning():
        w.shutdown()
        w.wait(3000)


def _run_and_wait(worker: SearchWorker, qtbot, expect_fail: bool = False) -> None:  # type: ignore[no-untyped-def]
    """启动 worker 并等待完成信号。"""
    signal = worker.searchFailed if expect_fail else worker.searchCompleted
    with qtbot.waitSignal(signal, timeout=10000):
        worker.start()


class TestSearchWorker:
    def test_search_finds_keyword(self, worker: SearchWorker, qtbot) -> None:  # type: ignore[no-untyped-def]
        results: list = []
        worker.searchCompleted.connect(lambda q, r: results.append((q, r)))
        worker.setup(SIMPLE, "PyMuPDF")
        _run_and_wait(worker, qtbot)
        assert len(results) == 1
        query, result = results[0]
        assert query == "PyMuPDF"
        assert isinstance(result, SearchResult)
        assert result.hit_count == 3

    def test_search_chinese(self, worker: SearchWorker, qtbot) -> None:  # type: ignore[no-untyped-def]
        results: list = []
        worker.searchCompleted.connect(lambda q, r: results.append(r))
        worker.setup(SIMPLE, "测试成功")
        _run_and_wait(worker, qtbot)
        assert results[0].hit_count == 3

    def test_search_no_match(self, worker: SearchWorker, qtbot) -> None:  # type: ignore[no-untyped-def]
        results: list = []
        worker.searchCompleted.connect(lambda q, r: results.append(r))
        worker.setup(SIMPLE, "这个关键词不存在XYZ")
        _run_and_wait(worker, qtbot)
        assert results[0].hit_count == 0

    def test_search_empty_query(self, worker: SearchWorker, qtbot) -> None:  # type: ignore[no-untyped-def]
        results: list = []
        worker.searchCompleted.connect(lambda q, r: results.append(r))
        worker.setup(SIMPLE, "")
        _run_and_wait(worker, qtbot)
        assert results[0].hit_count == 0

    def test_search_corrupt_fails(self, worker: SearchWorker, qtbot) -> None:  # type: ignore[no-untyped-def]
        errors: list[str] = []
        worker.searchFailed.connect(lambda m: errors.append(m))
        worker.setup(SAMPLES / "corrupt.pdf", "anything")
        _run_and_wait(worker, qtbot, expect_fail=True)
        assert len(errors) == 1
