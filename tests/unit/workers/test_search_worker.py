"""搜索线程测试。

使用 pytest-qt 的 qtbot.waitSignal 等待信号，避免与 pytest-qt 事件循环冲突。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.engines import FileOpenError, SearchError
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


class TestSearchWorkerCancel:
    """取消语义：search 逐页检查 cancel；被取消后一个结果都不发。"""

    def test_run_cancelled_before_start_emits_nothing(self, worker: SearchWorker) -> None:
        got: list = []
        worker.searchCompleted.connect(lambda q, r: got.append((q, r)))
        worker.searchFailed.connect(lambda m: got.append(m))
        worker.setup(SIMPLE, "PyMuPDF")
        worker.shutdown()
        worker.run()
        assert got == []

    def test_cancel_callback_handed_to_engine(self, worker: SearchWorker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        seen: dict = {}

        def fake_search(doc, query, *, cancel=None):  # type: ignore[no-untyped-def]
            seen["cancel"] = cancel
            seen["before"] = cancel() if cancel else None
            worker.shutdown()  # 模拟"搜索途中用户发起新搜索"
            seen["after"] = cancel() if cancel else None
            return SearchResult(query=query, hits=())

        monkeypatch.setattr(worker._engine, "search", fake_search)
        worker.setup(SIMPLE, "x")
        worker.run()

        assert callable(seen["cancel"])
        assert seen["before"] is False  # 未取消时不应提前中断
        assert seen["after"] is True

    def test_cancelled_result_not_emitted(self, worker: SearchWorker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        got: list = []
        worker.searchCompleted.connect(lambda q, r: got.append((q, r)))

        def fake_search(doc, query, *, cancel=None):  # type: ignore[no-untyped-def]
            worker.shutdown()
            return SearchResult(query=query, hits=())

        monkeypatch.setattr(worker._engine, "search", fake_search)
        worker.setup(SIMPLE, "x")
        worker.run()
        assert got == []


class TestSearchWorkerDirectRun:
    """直接调用 run()（与线程内是同一段代码），覆盖各条错误分支。"""

    def test_run_emits_result(self, worker: SearchWorker) -> None:
        got: list = []
        worker.searchCompleted.connect(lambda q, r: got.append((q, r)))
        worker.setup(SIMPLE, "PyMuPDF")
        worker.run()
        assert got[0][0] == "PyMuPDF"
        assert got[0][1].hit_count == 3

    def test_run_without_path(self, worker: SearchWorker) -> None:
        errors: list[str] = []
        worker.searchFailed.connect(errors.append)
        worker.run()
        assert errors == ["未指定文档路径"]

    def test_run_engine_open_error(self, worker: SearchWorker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileOpenError("需要密码")

        monkeypatch.setattr(worker._engine, "open", boom)
        errors: list[str] = []
        worker.searchFailed.connect(errors.append)
        worker.setup(SIMPLE, "x")
        worker.run()
        assert errors == ["需要密码"]

    def test_run_unexpected_open_error(self, worker: SearchWorker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("磁盘掉线")

        monkeypatch.setattr(worker._engine, "open", boom)
        errors: list[str] = []
        worker.searchFailed.connect(errors.append)
        worker.setup(SIMPLE, "x")
        worker.run()
        assert "打开文档失败" in errors[0]

    def test_run_search_error(self, worker: SearchWorker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise SearchError("搜索炸了")

        monkeypatch.setattr(worker._engine, "search", boom)
        errors: list[str] = []
        worker.searchFailed.connect(errors.append)
        worker.setup(SIMPLE, "x")
        worker.run()
        assert errors == ["搜索炸了"]

    def test_run_unexpected_search_error(self, worker: SearchWorker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("怪事")

        monkeypatch.setattr(worker._engine, "search", boom)
        errors: list[str] = []
        worker.searchFailed.connect(errors.append)
        worker.setup(SIMPLE, "x")
        worker.run()
        assert "搜索失败" in errors[0]
