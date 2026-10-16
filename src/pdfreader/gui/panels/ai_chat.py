"""AI 助读的对话视图：左右两侧的圆角气泡。

::

                          ┌──────────────────────────┐
                          │ ┃ 选中内容 · 第 3 页  ▾   │  ← 引用（折叠，点开看全文）
                          │ 翻译 → 简体中文            │  ← 用户气泡（右，赤陶淡底）
                          └──────────────────────────┘
    ┌──────────────────────────────┐
    │ 回答（Markdown，[p.N] 可点） │  ← AI 气泡（左，卡片底）
    │ 复制 · 存为批注              │
    └──────────────────────────────┘

只负责展示；数据（:class:`Turn`）由 :class:`~pdfreader.gui.panels.ai_panel.AiPanel` 维护。
流式输出时只重排正在生成的那一个气泡。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import Palette
from pdfreader.gui.widgets import motion
from pdfreader.services.ai.context import link_citations

__all__ = ["ChatView", "ContextCard", "Turn", "markdown_html"]

# 用户气泡最多占可用宽度的比例；AI 气泡更宽，长回答读起来不憋屈
_USER_RATIO = 0.84
_AI_RATIO = 0.94
_BUBBLE_PAD = 12  # 气泡左右内边距（与样式表一致）
_QUOTE_SHOW_CHARS = 4000  # 展开引用时最多显示的字数
_STICK_SLACK = 24  # 离底部这么近就算「在底部」，新内容到来时自动跟随


@dataclass
class Turn:
    """一轮对话：用户这边（动作 / 提问 + 引用）与 AI 的回答。"""

    title: str  # 用户气泡正文：提问原文或动作名（「翻译 → 简体中文」「总结全文」）
    quote: str = ""  # 引用的原文（划词翻译 / 解释 / 问 AI），折叠显示
    label: str = ""  # 引用来源（「第 3 页」）
    image: bytes = b""  # 框选区域截图（PNG）
    answer: str = ""
    thinking: bool = False  # 只收到思考内容、正文还没开始
    error: str = ""
    done: bool = False
    cancelled: bool = False
    questions: list[str] = field(default_factory=list)  # 推荐问题（可点击）


def markdown_html(text: str, palette: Palette, font: QFont) -> str:
    """Markdown → 富文本：链接用强调色、``cite:`` 页码做成淡底小标签、标题不过分放大。"""
    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
    base = font.pixelSize() if font.pixelSize() > 0 else round(font.pointSizeF() * 4 / 3)
    links: list[tuple[int, int, bool]] = []
    heads: list[tuple[int, int]] = []
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().headingLevel() > 0:
            heads.append((block.position(), block.length() - 1))
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fmt = frag.charFormat()
            if fmt.isAnchor():
                links.append((frag.position(), frag.length(), fmt.anchorHref().startswith("cite:")))
            it += 1
        block = block.next()
    cursor = QTextCursor(doc)
    heading = QFont(font)
    heading.setPixelSize(base + 1)
    heading.setWeight(QFont.Weight.DemiBold)
    for start, length in heads:
        fmt = QTextCharFormat()
        fmt.setFont(heading)
        fmt.setProperty(QTextFormat.Property.FontSizeAdjustment, 0)
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(fmt)
    for start, length, cite in links:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(palette.accent_text))
        fmt.setFontUnderline(False)
        if cite:
            fmt.setBackground(QColor(palette.accent_soft))
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(fmt)
    return doc.toHtml()


def _quote_info(text: str, label: str) -> str:
    parts = ["选中内容" if text else "框选区域"]
    if label:
        parts.append(label)
    if text:
        parts.append(f"{len(text)} 字")
    return " · ".join(parts)


class _RichLabel(QLabel):
    """可选中、链接可点的换行富文本。"""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )


class _ClickLabel(QLabel):
    """整块可点的标签（推荐问题）。"""

    clicked = Signal()

    def __init__(self, text: str, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(object_name)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _TypingDots(QWidget):
    """三个依次明暗的小圆点（等待回答时）；动效关闭时静止。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 14)
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if motion.enabled():
            self._timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % 6
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = QColor(get_theme().palette.accent)
        for i in range(3):
            lit = self._timer.isActive() and (self._phase // 2) == i
            color.setAlphaF(1.0 if lit or not self._timer.isActive() else 0.35)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(2 + i * 10, 4, 6, 6))
        painter.end()


class ContextCard(QFrame):
    """折叠的引用卡片：一行摘要，点开看全文（或框选截图）。

    放在用户气泡里展示本轮引用；``closable`` 时放在输入框上方，表示「下一问会带上这段」。
    """

    closed = Signal()

    def __init__(
        self,
        text: str,
        label: str = "",
        image: bytes = b"",
        *,
        closable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AiContext")
        self.setProperty("attached", closable)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text = text.strip()
        self._expanded = False
        self._pixmap = QPixmap()
        if image:
            self._pixmap = QPixmap.fromImage(QImage.fromData(image))

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 8, 0)
        outer.setSpacing(8)
        bar = QFrame()
        bar.setObjectName("AiContextBar")
        bar.setFixedWidth(3)
        outer.addWidget(bar)

        body = QVBoxLayout()
        body.setContentsMargins(0, 7, 0, 7)
        body.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(4)
        self.info = QLabel(_quote_info(self._text, label))
        self.info.setObjectName("AiContextInfo")
        head.addWidget(self.info, stretch=1)
        self.toggle_btn = QToolButton()
        self.toggle_btn.setObjectName("AiContextButton")
        self.toggle_btn.setIconSize(QSize(14, 14))
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle)
        head.addWidget(self.toggle_btn)
        self.close_btn: QToolButton | None = None
        if closable:
            self.close_btn = QToolButton()
            self.close_btn.setObjectName("AiContextButton")
            self.close_btn.setToolTip("不再引用")
            self.close_btn.setIconSize(QSize(13, 13))
            self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.close_btn.clicked.connect(self.closed.emit)
            head.addWidget(self.close_btn)
        body.addLayout(head)

        self.preview = QLabel()
        self.preview.setObjectName("AiContextText")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setMinimumWidth(40)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.preview)

        shown = self._text
        if len(shown) > _QUOTE_SHOW_CHARS:
            shown = shown[: _QUOTE_SHOW_CHARS - 1] + "…"
        self.full = QLabel(shown)
        self.full.setObjectName("AiContextText")
        self.full.setTextFormat(Qt.TextFormat.PlainText)
        self.full.setWordWrap(True)
        self.full.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.full.setCursor(Qt.CursorShape.IBeamCursor)
        body.addWidget(self.full)

        self.picture = QLabel()
        self.picture.setObjectName("AiContextImage")
        body.addWidget(self.picture)
        outer.addLayout(body, stretch=1)

        get_theme().changed.connect(self._refresh_icons)
        self._refresh_icons()
        self._apply()

    @property
    def text(self) -> str:
        return self._text

    @property
    def expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._apply()
        if self._expanded:
            motion.fade_up(self.full if self._text else self.picture, duration=220)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # 展开后的全文可以划选，只有标题行点了才收起
        if event.button() == Qt.MouseButton.LeftButton and (
            not self._expanded or event.position().y() < self.full.y()
        ):
            self.toggle()
        super().mouseReleaseEvent(event)

    def _apply(self) -> None:
        self.toggle_btn.setToolTip("收起" if self._expanded else "展开原文")
        self._refresh_icons()
        has_text = bool(self._text)
        self.preview.setVisible(has_text and not self._expanded)
        self.full.setVisible(has_text and self._expanded)
        self.picture.setVisible(not self._pixmap.isNull() and (self._expanded or not has_text))
        self._update_preview()
        self._update_picture()

    def _refresh_icons(self, _name: str = "") -> None:
        theme = get_theme()
        color = theme.palette.text_muted
        self.toggle_btn.setIcon(
            theme.colored_icon("chevron_up" if self._expanded else "chevron_down", color, size=14)
        )
        if self.close_btn is not None:
            self.close_btn.setIcon(theme.colored_icon("close", color, size=13))

    def _update_preview(self) -> None:
        if not self.preview.isVisible() and self._expanded:
            return
        line = " ".join(self._text.split())
        width = max(40, self.preview.width())
        self.preview.setText(
            self.preview.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, width)
        )

    def _update_picture(self) -> None:
        if self._pixmap.isNull():
            return
        ratio = self.devicePixelRatioF()
        if self._expanded:
            width = max(60, self.width() - 24)
            scaled = self._pixmap.scaledToWidth(
                int(min(width, self._pixmap.width()) * ratio),
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            scaled = self._pixmap.scaledToHeight(
                int(min(44, self._pixmap.height()) * ratio),
                Qt.TransformationMode.SmoothTransformation,
            )
        scaled.setDevicePixelRatio(ratio)
        self.picture.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_preview()
        if self._expanded:
            self._update_picture()


class _UserBubble(QFrame):
    def __init__(self, turn: Turn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiUserBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_BUBBLE_PAD, 9, _BUBBLE_PAD, 9)
        lay.setSpacing(7)
        self.context: ContextCard | None = None
        if turn.quote or turn.image:
            self.context = ContextCard(turn.quote, turn.label, turn.image)
            lay.addWidget(self.context)
        self.text = QLabel(turn.title)
        self.text.setObjectName("AiUserText")
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.text)

    def set_max_width(self, width: int) -> None:
        self.setMaximumWidth(width)
        self.text.setMaximumWidth(width - 2 * _BUBBLE_PAD)
        if self.context is not None:
            # 有引用时气泡撑满，摘要行才有地方显示
            self.setMinimumWidth(width)


class _AiBubble(QFrame):
    linkClicked = Signal(str)
    copyRequested = Signal()
    noteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiBotBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_BUBBLE_PAD, 10, _BUBBLE_PAD, 8)
        lay.setSpacing(6)

        self.waiting = QWidget()
        wait_lay = QHBoxLayout(self.waiting)
        wait_lay.setContentsMargins(0, 2, 0, 2)
        wait_lay.setSpacing(6)
        self.dots = _TypingDots()
        wait_lay.addWidget(self.dots)
        self.wait_label = QLabel("正在请求…")
        self.wait_label.setObjectName("AiWaitText")
        wait_lay.addWidget(self.wait_label)
        wait_lay.addStretch(1)
        lay.addWidget(self.waiting)

        self.body = _RichLabel("AiAnswer")
        self.body.linkActivated.connect(self.linkClicked)
        lay.addWidget(self.body)

        self.questions = QWidget()
        self._q_lay = QVBoxLayout(self.questions)
        self._q_lay.setContentsMargins(0, 0, 0, 0)
        self._q_lay.setSpacing(5)
        lay.addWidget(self.questions)

        self.error = QLabel()
        self.error.setObjectName("AiError")
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.error)

        self.note = QLabel("已停止")
        self.note.setObjectName("AiWaitText")
        lay.addWidget(self.note)

        self.actions = QWidget()
        act_lay = QHBoxLayout(self.actions)
        act_lay.setContentsMargins(0, 2, 0, 0)
        act_lay.setSpacing(2)
        self.copy_btn = self._action("copy", "复制", self.copyRequested.emit)
        self.note_btn = self._action("note", "存为批注", self.noteRequested.emit)
        act_lay.addWidget(self.copy_btn)
        act_lay.addWidget(self.note_btn)
        act_lay.addStretch(1)
        lay.addWidget(self.actions)

        self._question_count = 0
        self._max_width = 0
        self._refresh_icons()
        get_theme().changed.connect(self._refresh_icons)

    def _action(self, icon: str, text: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("AiBubbleAction")
        btn.setText(text)
        btn.setProperty("iconName", icon)
        btn.setIconSize(QSize(13, 13))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _refresh_icons(self, _name: str = "") -> None:
        theme = get_theme()
        for btn in (self.copy_btn, self.note_btn):
            btn.setIcon(
                theme.colored_icon(str(btn.property("iconName")), theme.palette.text_muted, size=13)
            )

    def set_max_width(self, width: int) -> None:
        self._max_width = width
        self.setMaximumWidth(width)
        self.body.setMaximumWidth(width - 2 * _BUBBLE_PAD)
        self._fit()

    def _fit(self) -> None:
        # 推荐问题 / 出错提示需要整宽；纯文字回答随内容收缩
        wide = self._question_count > 0 or bool(self.error.text())
        self.setMinimumWidth(self._max_width if wide and self._max_width else 0)

    def show_turn(self, turn: Turn, page_count: int, *, last: bool, on_ask) -> None:  # type: ignore[no-untyped-def]
        theme = get_theme()
        waiting = not turn.done and not turn.answer
        self.waiting.setVisible(waiting)
        self.wait_label.setText("模型思考中…" if turn.thinking else "正在请求…")
        text = "" if turn.questions else turn.answer
        self.body.setVisible(bool(text))
        if text:
            self.body.ensurePolished()  # 字号来自样式表
            self.body.setText(
                markdown_html(link_citations(text, page_count), theme.palette, self.body.font())
            )
        if len(turn.questions) != self._question_count:
            while self._q_lay.count():
                item = self._q_lay.takeAt(0)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.deleteLater()
            if turn.questions:
                hint = QLabel("可以接着问：")
                hint.setObjectName("AiWaitText")
                self._q_lay.addWidget(hint)
            for i, question in enumerate(turn.questions):
                label = _ClickLabel(question, "AiQuestion")
                label.clicked.connect(lambda i=i: on_ask(i))
                self._q_lay.addWidget(label)
            self._question_count = len(turn.questions)
        self.questions.setVisible(bool(turn.questions))
        self.error.setText(f"⚠ {turn.error}" if turn.error else "")
        self.error.setVisible(bool(turn.error))
        self.note.setVisible(turn.cancelled)
        has_answer = turn.done and bool(turn.answer) and not turn.error
        self.actions.setVisible(has_answer)
        self.note_btn.setVisible(has_answer and last)  # 批注锚在最近一次选区，只给最后一条
        self._fit()

    def flash_copied(self) -> None:
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1500, self, lambda: self.copy_btn.setText("复制"))


class _EmptyState(QFrame):
    """没有对话时：图标 + 一句话说明；未配置时附「配置模型」与隐私说明。"""

    settingsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiEmpty")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 28, 12, 12)
        lay.setSpacing(8)
        self.icon = QLabel()
        self.icon.setObjectName("AiEmptyIcon")
        self.icon.setFixedSize(48, 48)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel("让 AI 帮你读论文")
        self.title.setObjectName("AiEmptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title)
        self.text = QLabel()
        self.text.setObjectName("AiEmptyText")
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.text)
        self.setup_btn = QToolButton()
        self.setup_btn.setObjectName("AiChip")
        self.setup_btn.setText("配置模型 →")
        self.setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_btn.clicked.connect(self.settingsRequested.emit)
        lay.addWidget(self.setup_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.privacy = QLabel(
            "AI 是可选的联网功能：只有你主动发起时，才会把所选内容发送给你配置的服务；"
            "API Key 存在系统凭据管理器里。"
        )
        self.privacy.setObjectName("AiEmptyNote")
        self.privacy.setWordWrap(True)
        self.privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.privacy)
        lay.addStretch(1)
        get_theme().changed.connect(self._refresh_icon)
        self._refresh_icon()

    def _refresh_icon(self, _name: str = "") -> None:
        theme = get_theme()
        self.icon.setPixmap(theme.colored_icon("ai", theme.palette.accent, size=22).pixmap(22, 22))

    def set_configured(self, configured: bool) -> None:
        if configured:
            self.text.setText(
                "选中文字后右键可翻译、解释或引用提问；\n"
                "也可以点上方的「总结全文」「推荐问题」，或直接在下方提问。\n"
                "回答里的页码可点击跳回原文。"
            )
        else:
            self.text.setText(
                "划词翻译 / 解释、一键结构化总结、就全文提问、框选公式与图表讲解。\n"
                "支持 DeepSeek、通义千问、Kimi、智谱、豆包、硅基流动、OpenAI、Gemini、"
                "Ollama（本机）等任何 OpenAI 兼容服务。"
            )
        self.setup_btn.setVisible(not configured)
        self.privacy.setVisible(not configured)


class ChatView(QScrollArea):
    """气泡列表。新内容到来时，若用户停在底部就自动跟随。

    Signals:
        linkClicked(str): 回答里的链接（``cite:N``）
        questionClicked(int): 最近一组推荐问题里的第 N 个
        copyRequested(int) / noteRequested(int): 第 N 轮回答的「复制」「存为批注」
        settingsRequested(): 空状态里的「配置模型」
    """

    linkClicked = Signal(str)
    questionClicked = Signal(int)
    copyRequested = Signal(int)
    noteRequested = Signal(int)
    settingsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiChat")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("AiChatBody")
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(2, 4, 6, 8)
        self._lay.setSpacing(12)
        self.empty = _EmptyState()
        self.empty.settingsRequested.connect(self.settingsRequested)
        self._lay.addWidget(self.empty)
        self._lay.addStretch(1)
        self.setWidget(body)
        self._rows: list[tuple[_UserBubble, _AiBubble, QWidget, QWidget]] = []
        self._stick = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range)
        bar.valueChanged.connect(self._on_scrolled)

    # ------------------------------------------------------------------ 接口
    @property
    def bubbles(self) -> list[tuple[_UserBubble, _AiBubble]]:
        return [(u, a) for u, a, _r1, _r2 in self._rows]

    def set_configured(self, configured: bool) -> None:
        self.empty.set_configured(configured)

    def add_turn(self, turn: Turn, page_count: int) -> None:
        user = _UserBubble(turn)
        ai = _AiBubble()
        index = len(self._rows)
        ai.linkClicked.connect(self.linkClicked)
        ai.copyRequested.connect(lambda: self.copyRequested.emit(index))
        ai.noteRequested.connect(lambda: self.noteRequested.emit(index))
        user_row = self._row(user, right=True)
        ai_row = self._row(ai, right=False)
        at = self._lay.count() - 1  # 末尾的伸缩项之前
        self._lay.insertWidget(at, user_row)
        self._lay.insertWidget(at + 1, ai_row)
        self._rows.append((user, ai, user_row, ai_row))
        self.empty.setVisible(False)
        self._apply_widths()
        self.show_turn(index, turn, page_count, last=True)
        if index > 0:  # 「存为批注」只留给最新一条
            self._rows[index - 1][1].note_btn.setVisible(False)
        self._stick = True
        motion.fade_up(user)
        motion.fade_up(ai, delay=90)

    def show_turn(self, index: int, turn: Turn, page_count: int, *, last: bool) -> None:
        if 0 <= index < len(self._rows):
            self._rows[index][1].show_turn(
                turn, page_count, last=last, on_ask=self.questionClicked.emit
            )

    def refresh(self, turns: list[Turn], page_count: int) -> None:
        """主题切换后重新着色全部回答。"""
        for i, turn in enumerate(turns):
            self.show_turn(i, turn, page_count, last=i == len(turns) - 1)

    def clear(self) -> None:
        for _u, _a, user_row, ai_row in self._rows:
            for row in (user_row, ai_row):
                self._lay.removeWidget(row)
                row.deleteLater()
        self._rows.clear()
        self.empty.setVisible(True)
        self._stick = True

    def flash_copied(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self._rows[index][1].flash_copied()

    def plain_text(self) -> str:
        """可见内容的纯文本（测试与无障碍用）。"""
        if not self._rows:
            parts = [self.empty.title.text(), self.empty.text.text()]
            if self.empty.setup_btn.isVisibleTo(self.empty):
                parts.append(self.empty.setup_btn.text())
            return "\n".join(parts)
        lines: list[str] = []
        for user, ai, _r1, _r2 in self._rows:
            if user.context is not None:
                lines.append(user.context.text)
            lines.append(user.text.text())
            for widget in (ai.wait_label, ai.error, ai.note):
                if widget.isVisibleTo(ai) and widget.text():
                    lines.append(widget.text())
            if ai.body.isVisibleTo(ai):
                doc = QTextDocument()
                doc.setHtml(ai.body.text())
                lines.append(doc.toPlainText())
        return "\n".join(lines)

    # ------------------------------------------------------------------ 内部
    def _row(self, bubble: QWidget, *, right: bool) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if right:
            lay.addStretch(1)
        lay.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignTop)
        if not right:
            lay.addStretch(1)
        return row

    def _apply_widths(self) -> None:
        width = self.viewport().width() - 8
        for user, ai, _r1, _r2 in self._rows:
            user.set_max_width(max(120, int(width * _USER_RATIO)))
            ai.set_max_width(max(140, int(width * _AI_RATIO)))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_widths()

    def _on_range(self, _lo: int, hi: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(hi)

    def _on_scrolled(self, value: int) -> None:
        self._stick = value >= self.verticalScrollBar().maximum() - _STICK_SLACK
