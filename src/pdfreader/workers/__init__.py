"""后台线程层。

将耗时且非线程安全的操作（OCR、搜索、格式转换）与 AI 网络请求放到独立 QThread，避免卡 UI；
线程间仅通过 Qt 信号传递不可变结果。

注：页面渲染改为在主线程直接渲染受控文档（见 MainWindow._render_page），
以保证批注/编辑所见即所得，故不再有独立渲染线程。
"""

from __future__ import annotations

from pdfreader.workers.ai_worker import AiWorker
from pdfreader.workers.convert_worker import ConvertWorker
from pdfreader.workers.ocr_worker import ImageOcrWorker, OcrWorker
from pdfreader.workers.search_worker import SearchWorker

__all__ = [
    "AiWorker",
    "ConvertWorker",
    "ImageOcrWorker",
    "OcrWorker",
    "SearchWorker",
]
