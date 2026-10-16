"""多标签页：一个标签 = 一份打开的文档及只属于它的全部状态。

每个标签各自持有文档控制器（含撤销日志）、阅读视图（含渲染 / 取词 / 链接缓存、选区、
定时器与动画）、AI 面板与对话，以及导航历史、OCR 结果——结构上就不会串到别的文档。
功能区、侧边栏、搜索浮层、状态栏由主窗口共用，切换标签时按当前标签重新填充。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdfreader.controllers import DocumentController
    from pdfreader.gui.ai_assistant import AiAssistant
    from pdfreader.gui.panels.ai_panel import AiPanel
    from pdfreader.gui.widgets.pdf_view import PdfView
    from pdfreader.workers import OcrWorker

__all__ = ["DocumentTab"]


class DocumentTab:
    """一个文档标签的全部状态（视图与 AI 由主窗口创建后挂上来）。"""

    def __init__(self, controller: DocumentController, view: PdfView, panel: AiPanel) -> None:
        self.controller = controller
        self.view = view
        self.panel = panel
        self.ai: AiAssistant  # 主窗口随后创建（回调要引用本标签）
        # 导航历史（链接 / 目录 / 搜索结果跳转后可「后退」）：(页, 页内偏移)
        self.back_stack: list[tuple[int, float]] = []
        self.forward_stack: list[tuple[int, float]] = []
        self.ocr_worker: OcrWorker | None = None
        self.ocr_results: dict[int, str] = {}
        self.annotations_stale = True
        self.annotation_count: int | None = None  # 最近一次全文扫描到的批注数（文献库卡片用）

    @property
    def has_document(self) -> bool:
        return self.controller.has_document

    @property
    def path(self) -> Path | None:
        doc = self.controller.document
        return doc.path if doc is not None else None

    def label(self) -> str:
        """标签页上显示的名字（有未保存改动时带圆点）。"""
        doc = self.controller.document
        if doc is None:
            return ""
        return f"{doc.path.name} •" if doc.is_modified else doc.path.name

    def is_file(self, path: Path) -> bool:
        """是否打开的就是这个文件（大小写 / 相对路径不敏感）。"""
        mine = self.path
        return mine is not None and _norm(mine) == _norm(path)


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))
