"""后台线程层。

将耗时且非线程安全的操作（PyMuPDF 渲染、OCR、搜索、转换）放到独立 QThread。
**铁律**：PyMuPDF 非线程安全，``Document`` 对象的生命周期严格限制在
单个渲染线程内，主线程绝不直接持有它；线程间仅通过 Qt 信号传递不可变结果。
"""

from __future__ import annotations

from pdfreader.workers.ocr_worker import OcrWorker
from pdfreader.workers.render_worker import RenderRequest, RenderWorker
from pdfreader.workers.search_worker import SearchWorker

__all__ = ["OcrWorker", "RenderRequest", "RenderWorker", "SearchWorker"]
