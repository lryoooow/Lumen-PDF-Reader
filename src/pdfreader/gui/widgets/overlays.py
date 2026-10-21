"""阅读区浮层（Lumen 设计稿）。

* :class:`ToolHint` —— 顶部居中的深色胶囊：「● 拖拽选字以高亮  [Esc]」，切换批注工具时落下淡入。
* :class:`CitePopover` —— 点击正文内部链接（如参考文献 [1]、图表、章节引用）时弹出的预览卡：
  「参考文献 [1]」+ 目标位置的原文摘录 +「跳转到条目 →」。内容来自文档真实的链接目标，
  不跳转也能先看一眼引用的是什么，看完点空白处即关闭。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["CitePopover", "ToolHint"]

# 各批注工具的提示文案（与设计稿一致）
TOOL_HINTS = {
    "highlight": "拖拽选字以高亮",
    "underline": "拖拽选字以添加下划线",
    "ink": "按住拖动手写",
    "text": "单击页面放置便签",
    "freetext": "单击页面放置文本框",
    "eraser": "单击批注删除",
    "signature": "点击页面放置签名",
    "timestamp": "点击页面放置时间",
    "capture": "拖框选取公式 / 图表，交给 AI 解释",
    "capture_translate": "拖框选取要翻译的文字（截图识别）",
}


class ToolHint(QLabel):
    """顶部居中的工具提示胶囊（父控件为阅读区容器）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ToolHint")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def show_tool(self, mode: str, color: str | None = None) -> None:
        """显示某工具的提示；选择 / 抓手等无提示的工具则隐藏。"""
        text = TOOL_HINTS.get(mode)
        if text is None:
            self.hide()
            return
        dot = (
            f'<span style="color:{color}; font-size:14px;">●</span>&nbsp;&nbsp;'
            if color and mode == "highlight"
            else ""
        )
        kbd = (
            '&nbsp;&nbsp;<span style="background-color:rgba(255,255,255,0.18);'
            ' font-size:10px;">&nbsp;Esc&nbsp;</span>'
        )
        self.setText(f"{dot}{text}{kbd}")
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        motion.drop_in(self, duration=TOKENS.anim_drop)  # 每次换工具都重播（设计稿 key={tool}）

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.move((parent.width() - self.width()) // 2, 16)


class CitePopover(QFrame):
    """链接预览卡（弹出式窗口，点击外部自动关闭）。

    Signals:
        jumpRequested(): 点击「跳转到条目 →」
    """

    jumpRequested = Signal()

    WIDTH = 320

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("CitePopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFixedWidth(self.WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("CiteTitle")
        self.body = QLabel()
        self.body.setObjectName("CiteBody")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.jump_btn = QPushButton("跳转到条目 →")
        self.jump_btn.setObjectName("LinkButton")
        self.jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_btn.clicked.connect(self._on_jump)
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        lay.addSpacing(4)
        lay.addWidget(self.jump_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        # 绑定方法（而非 lambda）：本对象销毁时 PySide6 会自动断开与全局主题单例的连接
        get_theme().changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _name: str) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def show_at(self, global_pos: QPoint, title: str, body: str) -> None:
        """在鼠标附近（设计稿：左 -40、下 +14）弹出，越界时收回屏幕内。"""
        self.title.setText(title)
        self.body.setText(body or "（目标位置没有可提取的文字）")
        self.adjustSize()
        pos = global_pos + QPoint(-40, 14)
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            area: QRect = screen.availableGeometry()
            pos.setX(max(area.left() + 8, min(pos.x(), area.right() - self.width() - 8)))
            if pos.y() + self.height() > area.bottom() - 8:
                pos.setY(global_pos.y() - self.height() - 14)
        end = pos
        self.move(end)
        self.show()
        if motion.enabled():
            self.setWindowOpacity(0.0)
            motion.animate_property(self, b"windowOpacity", 0.0, 1.0, TOKENS.anim_drop, owner=self)
            motion.animate_property(
                self, b"pos", end - QPoint(0, 10), end, TOKENS.anim_drop, owner=self.body
            )

    def _on_jump(self) -> None:
        self.hide()
        self.jumpRequested.emit()
