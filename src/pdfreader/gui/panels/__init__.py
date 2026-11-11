"""面板：左侧边栏（目录 / 批注 / 缩略图）、搜索浮层与右侧多合一学术助读工作台。"""

from __future__ import annotations

from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.gui.panels.search_panel import SearchPanel
from pdfreader.gui.panels.sidebar import SidePanel
from pdfreader.gui.panels.studio_dock import (
    RightStudioDock,
    StudioSegmentedHeader,
    TranslateStudioPane,
)

__all__ = [
    "AiPanel",
    "RightStudioDock",
    "SearchPanel",
    "SidePanel",
    "StudioSegmentedHeader",
    "TranslateStudioPane",
]
