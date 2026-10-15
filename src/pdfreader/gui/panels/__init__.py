"""面板：左侧边栏（目录 / 批注 / 缩略图）、搜索浮层与右侧 AI 助读面板。"""

from __future__ import annotations

from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.gui.panels.search_panel import SearchPanel
from pdfreader.gui.panels.sidebar import SidePanel

__all__ = ["AiPanel", "SearchPanel", "SidePanel"]
