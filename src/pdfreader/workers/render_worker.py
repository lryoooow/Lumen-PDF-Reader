"""渲染后台线程。

将 PyMuPDF 的渲染放到独立 QThread，解决两个问题：
1. **避免阻塞 UI**：渲染耗时操作不卡主线程
2. **PyMuPDF 非线程安全**：Document 对象严格限制在本线程内，
   主线程绝不直接持有或操作它。线程间只通过 Qt 信号传递**不可变结果**。

架构：
    [主线程]                    [RenderWorker (QThread)]
       │                              │
       │── requestRender(page,zoom) ──→ 入队
       │                              │ 持有 Document，渲染
       │←── pageRendered(PixmapData) ── 信号回传（不可变）
       │                              │
       │── requestCancel() ───────────→ 清空待处理队列

注意：PixmapData 只含 bytes，是线程安全的不可变数据。
主线程收到后自行转 QPixmap（QPixmap 有线程亲和性，必须在主线程构造）。
"""

from __future__ import annotations

import contextlib
from collections import deque
from pathlib import Path
from threading import Lock

from PySide6.QtCore import QThread, Signal, Slot

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.base import EngineDocument
from pdfreader.engines.exceptions import PdfEngineError

__all__ = ["RenderRequest", "RenderWorker"]


class RenderRequest:
    """单次渲染请求（页码 + 缩放）。

    带递增序号，用于取消过时请求：序号小于当前最小序号的请求被丢弃。
    """

    __slots__ = ("page_num", "seq", "zoom")

    def __init__(self, seq: int, page_num: int, zoom: float) -> None:
        self.seq = seq
        self.page_num = page_num
        self.zoom = zoom

    def __repr__(self) -> str:
        return f"RenderRequest(seq={self.seq}, page={self.page_num}, zoom={self.zoom})"


class RenderWorker(QThread):
    """渲染后台线程。

    在独立线程持有 Document，按队列渲染页面。
    主线程通过 :meth:`request_render` 投递请求，通过 :attr:`pageRendered` 收结果。

    Signals:
        pageRendered(int, float, PixmapData): 页码、zoom、渲染结果
        renderFailed(int, str): 页码、错误消息
        documentLoaded(int): 文档加载完成，发送页数
        documentLoadFailed(str): 加载失败消息
    """

    pageRendered = Signal(int, float, object)  # page_num, zoom, PixmapData
    renderFailed = Signal(int, str)  # page_num, message
    documentLoaded = Signal(int)  # page_count
    documentLoadFailed = Signal(str)  # message

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._engine = PyMuPdfEngine()
        self._doc: EngineDocument | None = None  # 仅在本线程内访问

        # 任务队列（线程间共享，用锁保护）
        self._queue: deque[RenderRequest] = deque()
        self._lock = Lock()
        self._next_seq = 0
        self._running = True

        # 待加载文档（主线程设置，线程在 run() 中打开）
        self._pending_path: Path | None = None
        self._pending_password: str | None = None

    # ------------------------------------------------------------------
    # 主线程 API
    # ------------------------------------------------------------------
    def load_document(self, path: Path, password: str | None = None) -> None:
        """请求加载文档（在主线程调用，实际打开在线程内执行）。"""
        self._pending_path = path
        self._pending_password = password

    @Slot(int, float)
    def request_render(self, page_num: int, zoom: float) -> None:
        """请求渲染指定页（主线程调用）。"""
        with self._lock:
            self._next_seq += 1
            req = RenderRequest(self._next_seq, page_num, zoom)
            self._queue.append(req)

    @Slot()
    def request_cancel(self) -> None:
        """清空待处理队列（快速滚动时丢弃过时请求）。"""
        with self._lock:
            self._queue.clear()

    def shutdown(self) -> None:
        """通知线程退出（主线程调用，配合 wait()）。"""
        self._running = False
        with self._lock:
            self._queue.clear()

    # ------------------------------------------------------------------
    # 线程主循环
    # ------------------------------------------------------------------
    def run(self) -> None:
        """线程入口。加载文档（如有）后循环处理渲染请求。"""
        # 1. 加载待处理文档（若主线程已设置）
        if self._pending_path is not None:
            self._load_document()

        # 2. 渲染循环
        while self._running:
            req = self._next_request()
            if req is None:
                self.msleep(10)  # 空闲时短暂休眠，避免忙等
                continue
            self._process_request(req)

        # 3. 清理
        self._close_document()

    def _load_document(self) -> None:
        """在线程内打开文档（PyMuPDF 非线程安全，Document 只在此处创建）。"""
        path = self._pending_path
        assert path is not None
        try:
            doc = self._engine.open(path, password=self._pending_password)
            self._doc = doc
            count = self._engine.page_count(doc)
            self.documentLoaded.emit(count)
        except PdfEngineError as exc:
            self.documentLoadFailed.emit(str(exc))
        except Exception as exc:
            self.documentLoadFailed.emit(f"加载失败：{exc}")

    def _close_document(self) -> None:
        if self._doc is not None:
            with contextlib.suppress(Exception):
                self._engine.close(self._doc)
            self._doc = None

    def _next_request(self) -> RenderRequest | None:
        with self._lock:
            if self._queue:
                return self._queue.popleft()
            return None

    def _process_request(self, req: RenderRequest) -> None:
        if self._doc is None:
            self.renderFailed.emit(req.page_num, "文档未加载")
            return
        try:
            data = self._engine.render_page(self._doc, req.page_num, req.zoom)
            self.pageRendered.emit(req.page_num, req.zoom, data)
        except PdfEngineError as exc:
            self.renderFailed.emit(req.page_num, str(exc))
        except Exception as exc:
            self.renderFailed.emit(req.page_num, f"渲染失败：{exc}")
