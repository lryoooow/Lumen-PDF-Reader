"""搜索后台线程。

全文搜索是耗时操作（大文档逐页搜索），放到独立 QThread 避免卡 UI。
与渲染线程同理：Document 在线程内，主线程通过信号收发不可变结果。

注意：搜索线程与渲染线程**各自独立打开文档**（PyMuPDF 非线程安全，
不能共享 Document）。开销可接受——搜索频率远低于渲染。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.exceptions import PdfEngineError

__all__ = ["SearchWorker"]


class SearchWorker(QThread):
    """全文搜索后台线程。

    Signals:
        searchCompleted(str, SearchResult): 查询词 + 结果
        searchFailed(str): 错误消息
    """

    searchCompleted = Signal(str, object)  # query, SearchResult
    searchFailed = Signal(str)  # message

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._engine = PyMuPdfEngine()
        self._path: Path | None = None
        self._password: str | None = None
        self._query: str = ""

    def setup(self, path: Path, query: str, password: str | None = None) -> None:
        """配置搜索任务（主线程调用）。"""
        self._path = path
        self._query = query
        self._password = password

    def shutdown(self) -> None:
        """标记线程应退出（配合 wait()）。"""
        # 搜索线程不维护可中断的运行循环，此方法仅为接口一致性
        pass

    def run(self) -> None:
        """线程入口：打开文档 → 搜索 → 发送结果。"""
        path = self._path
        if path is None:
            self.searchFailed.emit("未指定文档路径")
            return

        try:
            doc = self._engine.open(path, password=self._password)
        except PdfEngineError as exc:
            self.searchFailed.emit(str(exc))
            return
        except Exception as exc:
            self.searchFailed.emit(f"打开文档失败：{exc}")
            return

        try:
            result = self._engine.search(doc, self._query)
            self.searchCompleted.emit(self._query, result)
        except PdfEngineError as exc:
            self.searchFailed.emit(str(exc))
        except Exception as exc:
            self.searchFailed.emit(f"搜索失败：{exc}")
        finally:
            with contextlib.suppress(Exception):
                self._engine.close(doc)
