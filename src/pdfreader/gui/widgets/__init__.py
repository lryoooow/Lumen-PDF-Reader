"""主视图控件：PDF 阅读视图、文档标签栏、标题栏 + 功能区、状态栏、文献库首页（浮层与动效见 overlays / motion）。"""

from __future__ import annotations

from pdfreader.gui.widgets.doc_tabs import DocumentTabBar
from pdfreader.gui.widgets.library import LibraryEntry, LibraryHome
from pdfreader.gui.widgets.pdf_view import PdfView
from pdfreader.gui.widgets.ribbon import Ribbon
from pdfreader.gui.widgets.status_bar import StatusBar

__all__ = ["DocumentTabBar", "LibraryEntry", "LibraryHome", "PdfView", "Ribbon", "StatusBar"]
