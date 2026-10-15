"""AI 助读面板（右侧抽屉）。

结构（宽 380）::

    AI 助读                       [⚙] [清空] [×]
    ● DeepSeek · deepseek-chat（或「本机」）
    [总结全文] [推荐问题] [更多动作 ▾] [框选提问]
    ┌ 对话记录（Markdown 渲染；[p.N] 可点击跳页；推荐问题可点击直接提问）┐
    └──────────────────────────────────────────────────────────────────┘
    思考中… / 正在生成…               [复制] [存为批注]
    ┌ 输入问题（Enter 发送，Shift+Enter 换行）            [发送 / 停止] ┐

面板只负责展示与收集输入；请求的组装、隐私确认与线程管理在 :class:`~pdfreader.gui.ai_assistant.AiAssistant`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import get_theme
from pdfreader.services.ai.context import link_citations
from pdfreader.services.ai.prompts import PRESET_ACTIONS

__all__ = ["AI_PANEL_WIDTH", "AiPanel"]

AI_PANEL_WIDTH = 380
# 流式输出时合并刷新的间隔（毫秒）：逐 token 重排整段 Markdown 太费
_RENDER_INTERVAL = 80


@dataclass
class _Turn:
    title: str  # 「你」的提问或动作名
    quote: str = ""  # 引用的原文（划词翻译 / 解释）
    answer: str = ""
    thinking: bool = False  # 只收到思考内容、正文还没开始
    error: str = ""
    done: bool = False
    cancelled: bool = False
    questions: list[str] = field(default_factory=list)  # 推荐问题（可点击）


class _InputBox(QPlainTextEdit):
    """输入框：Enter 发送、Shift+Enter 换行。"""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class AiPanel(QFrame):
    """AI 助读面板。

    Signals:
        askRequested(str): 用户提问
        actionRequested(str): 预设动作（``summary`` / ``suggest`` / 预设动作 key）
        captureRequested(): 进入框选工具
        stopRequested(): 停止生成
        settingsRequested(): 打开模型设置
        citationClicked(int): 点击 ``[p.N]``（1 起页码）
        saveNoteRequested(str): 把最后一条回答存为批注
        clearRequested(): 清空对话
        closeRequested(): 关闭面板
    """

    askRequested = Signal(str)
    actionRequested = Signal(str)
    captureRequested = Signal()
    stopRequested = Signal()
    settingsRequested = Signal()
    citationClicked = Signal(int)
    saveNoteRequested = Signal(str)
    clearRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = get_theme()
        self._turns: list[_Turn] = []
        self._page_count = 0
        self._busy = False
        self._configured = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_RENDER_INTERVAL)
        self._render_timer.timeout.connect(self._render)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # ---- 标题行
        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("AI 助读")
        title.setObjectName("AiTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.settings_btn = self._icon_button("settings", "模型设置", self.settingsRequested.emit)
        self.clear_btn = self._icon_button("delete_page", "清空对话", self.clearRequested.emit)
        self.close_btn = self._icon_button("close", "关闭面板", self.closeRequested.emit)
        for btn in (self.settings_btn, self.clear_btn, self.close_btn):
            head.addWidget(btn)
        root.addLayout(head)

        self.provider_label = QLabel()
        self.provider_label.setObjectName("AiProvider")
        self.provider_label.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.provider_label)

        # ---- 预设动作
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.summary_btn = self._chip("总结全文", lambda: self.actionRequested.emit("summary"))
        self.suggest_btn = self._chip("推荐问题", lambda: self.actionRequested.emit("suggest"))
        self.more_btn = self._chip("更多 ▾", None)
        menu = QMenu(self.more_btn)
        for action in PRESET_ACTIONS:
            menu.addAction(action.title, lambda k=action.key: self.actionRequested.emit(k))
        self.more_btn.setMenu(menu)
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.capture_btn = self._chip("框选提问", self.captureRequested.emit)
        self.capture_btn.setToolTip("框选公式 / 图表，让 AI 解释（需要支持图片的模型）")
        for btn in (self.summary_btn, self.suggest_btn, self.more_btn, self.capture_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        root.addLayout(actions)

        # ---- 对话记录
        self.transcript = QTextBrowser()
        self.transcript.setObjectName("AiTranscript")
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self._on_anchor)
        root.addWidget(self.transcript, stretch=1)

        # ---- 状态 + 对最后一条回答的操作
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.status_label = QLabel()
        self.status_label.setObjectName("AiStatus")
        foot.addWidget(self.status_label, stretch=1)
        self.copy_btn = self._link_button("复制", self._copy_last)
        self.note_btn = self._link_button("存为批注", self._save_last)
        foot.addWidget(self.copy_btn)
        foot.addWidget(self.note_btn)
        root.addLayout(foot)

        # ---- 输入
        box = QFrame()
        box.setObjectName("AiInputBox")
        box_lay = QHBoxLayout(box)
        box_lay.setContentsMargins(8, 6, 6, 6)
        box_lay.setSpacing(6)
        self.input = _InputBox()
        self.input.setObjectName("AiInput")
        self.input.setPlaceholderText("就这篇文档提问…（Enter 发送，Shift+Enter 换行）")
        self.input.setFixedHeight(64)
        self.input.submitted.connect(self._on_send)
        self.send_btn = QToolButton()
        self.send_btn.setObjectName("AiSendButton")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setIconSize(QSize(16, 16))
        self.send_btn.clicked.connect(self._on_send_clicked)
        box_lay.addWidget(self.input, stretch=1)
        box_lay.addWidget(self.send_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addWidget(box)

        self._theme.changed.connect(self._on_theme_changed)
        self._refresh_icons()
        self._update_controls()
        self._render()

    # ------------------------------------------------------------------ 构建辅助
    def _icon_button(self, icon: str, tip: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("HeaderIconButton")
        btn.setToolTip(tip)
        btn.setProperty("iconName", icon)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIconSize(QSize(16, 16))
        btn.clicked.connect(slot)
        return btn

    def _chip(self, text: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("AiChip")
        btn.setText(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if slot is not None:
            btn.clicked.connect(slot)
        return btn

    def _link_button(self, text: str, slot) -> QPushButton:  # type: ignore[no-untyped-def]
        btn = QPushButton(text)
        btn.setObjectName("LinkButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _on_theme_changed(self, _name: str) -> None:
        self._refresh_icons()
        self._render()

    def _refresh_icons(self) -> None:
        for btn in (self.settings_btn, self.clear_btn, self.close_btn):
            btn.setIcon(self._theme.icon(str(btn.property("iconName")), size=16))
        self.send_btn.setIcon(
            self._theme.colored_icon(
                "stop" if self._busy else "send", self._theme.palette.on_accent, size=16
            )
        )

    # ------------------------------------------------------------------ 外部接口
    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def turns(self) -> list[_Turn]:
        return self._turns

    def set_provider_label(self, text: str) -> None:
        self.provider_label.setText(text)

    def set_configured(self, configured: bool) -> None:
        self._configured = configured
        self._update_controls()
        self._render()

    def set_page_count(self, count: int) -> None:
        """有效页数（超出范围的 [p.N] 不做成链接，防止模型编造的页码乱跳）。"""
        self._page_count = count

    def begin_turn(self, title: str, quote: str = "") -> None:
        self._turns.append(_Turn(title=title, quote=quote, thinking=False))
        self._set_busy(True)
        self.status_label.setText("正在请求…")
        self._render()

    def append_reasoning(self, _text: str) -> None:
        turn = self._current()
        if turn is not None and not turn.answer:
            turn.thinking = True
            self.status_label.setText("模型思考中…")
            self._schedule()

    def append_delta(self, text: str) -> None:
        turn = self._current()
        if turn is None:
            return
        turn.thinking = False
        turn.answer += text
        self.status_label.setText("正在生成…")
        self._schedule()

    def finish_turn(self, text: str | None = None, *, questions: list[str] | None = None) -> None:
        turn = self._current()
        if turn is None:
            return
        if text is not None:
            turn.answer = text
        turn.questions = list(questions or [])
        turn.done = True
        turn.thinking = False
        self._set_busy(False)
        self.status_label.setText("")
        self._render()

    def fail_turn(self, message: str) -> None:
        turn = self._current()
        if turn is None:
            return
        turn.error = message
        turn.done = True
        turn.thinking = False
        self._set_busy(False)
        self.status_label.setText("请求失败")
        self._render()

    def cancel_turn(self) -> None:
        turn = self._current()
        if turn is None:
            return
        turn.cancelled = True
        turn.done = True
        turn.thinking = False
        self._set_busy(False)
        self.status_label.setText("已停止")
        self._render()

    def clear(self) -> None:
        self._turns.clear()
        self._set_busy(False)
        self.status_label.setText("")
        self._render()

    def prefill(self, text: str) -> None:
        """把文字放进输入框（右键「问 AI…」引用选中内容），光标置于末尾。"""
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.input.setFocus()

    def last_answer(self) -> str:
        for turn in reversed(self._turns):
            if turn.done and turn.answer and not turn.error:
                return turn.answer
        return ""

    def transcript_markdown(self) -> str:
        return self._markdown()

    # ------------------------------------------------------------------ 内部
    def _current(self) -> _Turn | None:
        if self._turns and not self._turns[-1].done:
            return self._turns[-1]
        return None

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_icons()
        self._update_controls()

    def _update_controls(self) -> None:
        enabled = self._configured and not self._busy
        for btn in (self.summary_btn, self.suggest_btn, self.more_btn, self.capture_btn):
            btn.setEnabled(enabled)
        self.send_btn.setEnabled(self._configured)
        self.send_btn.setToolTip("停止生成" if self._busy else "发送（Enter）")
        has_answer = bool(self.last_answer()) and not self._busy
        self.copy_btn.setEnabled(has_answer)
        self.note_btn.setEnabled(has_answer)
        self.clear_btn.setEnabled(bool(self._turns) and not self._busy)

    def _schedule(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _markdown(self) -> str:
        if not self._configured and not self._turns:
            return (
                "### 让 AI 帮你读论文\n\n"
                "- 划词**翻译 / 解释**（右键选中的文字）\n"
                "- 一键**结构化总结**，要点带 [页码] 可点击回原文\n"
                "- 就全文**提问**，框选**公式 / 图表**让 AI 讲解\n\n"
                "支持 DeepSeek、通义千问、Kimi、智谱、豆包、硅基流动、OpenAI、Gemini、Ollama（本机）"
                "等任何 OpenAI 兼容服务。\n\n"
                "[配置模型 →](action:settings)\n\n"
                "> AI 是**可选的联网功能**：只有你主动发起时，才会把所选内容发送给你配置的服务；"
                "API Key 存在系统凭据管理器里。"
            )
        if not self._turns:
            return (
                "选中文字后右键可**翻译 / 解释**；也可以点上方的「总结全文」「推荐问题」，"
                "或直接在下方提问。回答里的 [p.N] 可点击跳回原文。"
            )
        parts: list[str] = []
        for turn in self._turns:
            parts.append(f"**{turn.title}**")
            if turn.quote:
                quote = turn.quote.strip().replace("\n", " ")
                if len(quote) > 280:
                    quote = quote[:279] + "…"
                parts.append(f"> {quote}")
            if turn.questions:
                parts.append("\n".join(f"- [{q}](ask:{i})" for i, q in enumerate(turn.questions)))
            elif turn.answer:
                parts.append(link_citations(turn.answer, self._page_count))
            if turn.thinking:
                parts.append("*思考中…*")
            elif not turn.done and not turn.answer:
                parts.append("*…*")
            if turn.error:
                parts.append(f"⚠ {turn.error}".replace("\n", "  \n"))
            if turn.cancelled:
                parts.append("*（已停止）*")
            parts.append("---")
        return "\n\n".join(parts)

    def _render(self) -> None:
        bar = self.transcript.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.transcript.setMarkdown(self._markdown())
        if at_bottom or self._busy:
            self.transcript.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())
        self._update_controls()

    def _on_anchor(self, url: QUrl) -> None:
        scheme = url.scheme()
        target = url.path() or url.toString().partition(":")[2]
        if scheme == "cite":
            try:
                self.citationClicked.emit(int(target))
            except ValueError:
                return
        elif scheme == "ask":
            question = self._question(target)
            if question and not self._busy:
                self.askRequested.emit(question)
        elif scheme == "action" and target == "settings":
            self.settingsRequested.emit()

    def _question(self, index: str) -> str:
        for turn in reversed(self._turns):
            if turn.questions:
                try:
                    return turn.questions[int(index)]
                except (ValueError, IndexError):
                    return ""
        return ""

    def _on_send(self) -> None:
        if self._busy or not self._configured:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.askRequested.emit(text)

    def _on_send_clicked(self) -> None:
        if self._busy:
            self.stopRequested.emit()
        else:
            self._on_send()

    def _copy_last(self) -> None:
        text = self.last_answer()
        clipboard = QGuiApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)
            self.status_label.setText("已复制")

    def _save_last(self) -> None:
        text = self.last_answer()
        if text:
            self.saveNoteRequested.emit(text)
