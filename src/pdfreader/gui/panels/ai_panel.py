"""AI 助读面板（右侧抽屉）。

结构（宽 380）::

    AI 助读                       [⚙] [清空] [×]
    ● DeepSeek · deepseek-chat（或「本机」）
    [总结全文] [推荐问题] [更多动作 ▾] [框选提问]
    ┌ 对话（气泡：用户在右、AI 在左；引用原文折叠在用户气泡里，见 ai_chat）┐
    └──────────────────────────────────────────────────────────────────┘
    ┃ 选中内容 · 第 3 页 · 128 字  ▾ ×   ← 「问 AI…」引用的原文（随下一问发送）
    ┌ 输入问题（Enter 发送，Shift+Enter 换行）            [发送 / 停止] ┐

面板只负责展示与收集输入；请求的组装、隐私确认与线程管理在 :class:`~pdfreader.gui.ai_assistant.AiAssistant`。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.panels.ai_chat import ChatView, ContextCard, Turn
from pdfreader.gui.theme import get_theme
from pdfreader.gui.widgets import motion
from pdfreader.services.ai.context import link_citations
from pdfreader.services.ai.prompts import PRESET_ACTIONS
from pdfreader.services.ai.textfix import tidy_markdown

__all__ = ["AI_PANEL_MAX_WIDTH", "AI_PANEL_MIN_WIDTH", "AI_PANEL_WIDTH", "AiPanel"]

AI_PANEL_WIDTH = 380  # 默认宽度；用户可拖动调整
AI_PANEL_MIN_WIDTH = 320
AI_PANEL_MAX_WIDTH = 760
# 流式输出时合并刷新的间隔（毫秒）：逐 token 重排整段 Markdown 太费
_RENDER_INTERVAL = 80
_PLACEHOLDER = "就这篇文档提问…（Enter 发送，Shift+Enter 换行）"


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
        actionRequested(str): 预设动作（``summary`` / ``suggest`` / ``tldr`` /
            ``speedread`` / 预设动作 key）
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
        self._turns: list[Turn] = []
        self._attachment: ContextCard | None = None
        self._attachment_label = ""
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
        menu.addAction("TL;DR 速览", lambda: self.actionRequested.emit("tldr"))
        menu.addAction("速读高亮（当前页）", lambda: self.actionRequested.emit("speedread"))
        menu.addSeparator()
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

        # ---- 对话（气泡）
        self.chat = ChatView()
        self.chat.linkClicked.connect(lambda href: self._on_anchor(QUrl(href)))
        self.chat.questionClicked.connect(lambda i: self._on_anchor(QUrl(f"ask:{i}")))
        self.chat.copyRequested.connect(self._copy_turn)
        self.chat.noteRequested.connect(self._save_turn)
        self.chat.settingsRequested.connect(self.settingsRequested)
        root.addWidget(self.chat, stretch=1)

        # ---- 「问 AI…」引用的原文（折叠卡片，随下一问发送）
        self._attach_slot = QVBoxLayout()
        self._attach_slot.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self._attach_slot)

        # ---- 输入
        box = QFrame()
        box.setObjectName("AiInputBox")
        box_lay = QHBoxLayout(box)
        box_lay.setContentsMargins(8, 6, 6, 6)
        box_lay.setSpacing(6)
        self.input = _InputBox()
        self.input.setObjectName("AiInput")
        self.input.setPlaceholderText(_PLACEHOLDER)
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
        self.chat.set_configured(False)
        self._update_controls()

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

    def _on_theme_changed(self, _name: str) -> None:
        self._refresh_icons()
        self.chat.refresh(self._turns, self._page_count)

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
    def turns(self) -> list[Turn]:
        return self._turns

    def set_provider_label(self, text: str) -> None:
        self.provider_label.setText(text)

    def set_configured(self, configured: bool) -> None:
        self._configured = configured
        self.chat.set_configured(configured)
        self._update_controls()

    def set_page_count(self, count: int) -> None:
        """有效页数（超出范围的 [p.N] 不做成链接，防止模型编造的页码乱跳）。"""
        self._page_count = count

    def set_preparing(self, preparing: bool) -> None:
        """全文请求的取词阶段也允许停止，不创建尚未发送的对话记录。"""
        self._set_busy(preparing)

    def begin_turn(
        self, title: str, quote: str = "", *, label: str = "", image: bytes = b""
    ) -> None:
        """新一轮：``title`` 是用户气泡里的话，``quote`` / ``image`` 是折叠显示的引用。"""
        self._render_timer.stop()
        turn = Turn(title=title, quote=quote, label=label, image=image)
        self._turns.append(turn)
        self._set_busy(True)
        self.chat.add_turn(turn, self._page_count)

    def append_reasoning(self, _text: str) -> None:
        turn = self._current()
        if turn is not None and not turn.answer and not turn.thinking:
            turn.thinking = True
            self._schedule()

    def append_delta(self, text: str) -> None:
        turn = self._current()
        if turn is None:
            return
        turn.thinking = False
        turn.answer += text
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
        self._render()

    def fail_turn(self, message: str) -> None:
        turn = self._current()
        if turn is None:
            return
        turn.error = message
        turn.done = True
        turn.thinking = False
        self._set_busy(False)
        self._render()

    def cancel_turn(self) -> None:
        turn = self._current()
        if turn is None:
            return
        turn.cancelled = True
        turn.done = True
        turn.thinking = False
        self._set_busy(False)
        self._render()

    def clear(self) -> None:
        self._render_timer.stop()
        self._turns.clear()
        self._set_busy(False)
        self.chat.clear()
        self.clear_attachment()
        self._update_controls()

    def prefill(self, text: str) -> None:
        """把文字放进输入框，光标置于末尾。"""
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.input.setFocus()

    # ------------------------------------------------------------------ 引用（问 AI…）
    def attach_context(self, text: str, label: str = "") -> None:
        """右键「问 AI…」：选中的原文折叠在输入框上方，随下一次提问一起发送。"""
        self.clear_attachment()
        text = text.strip()
        if text:
            card = ContextCard(text, label, closable=True)
            card.closed.connect(self.clear_attachment)
            self._attach_slot.addWidget(card)
            self._attachment = card
            self._attachment_label = label
            self.input.setPlaceholderText("针对引用的内容提问…（Enter 发送）")
            motion.fade_up(card, duration=220)
        self.input.setFocus()

    def attachment(self) -> tuple[str, str]:
        """当前引用（原文, 来源）；没有时为两个空串。"""
        if self._attachment is None:
            return "", ""
        return self._attachment.text, self._attachment_label

    def clear_attachment(self) -> None:
        card, self._attachment = self._attachment, None
        self._attachment_label = ""
        if card is not None:
            self._attach_slot.removeWidget(card)
            card.deleteLater()
        self.input.setPlaceholderText(_PLACEHOLDER)

    def last_answer(self) -> str:
        for turn in reversed(self._turns):
            if turn.done and turn.answer and not turn.error:
                return turn.answer
        return ""

    def transcript_markdown(self) -> str:
        """整段对话的 Markdown（动作 / 提问、引用、回答与推荐问题）。"""
        parts: list[str] = []
        for turn in self._turns:
            parts.append(f"**{turn.title}**")
            if turn.quote:
                parts.append("> " + turn.quote.strip().replace("\n", "\n> "))
            if turn.questions:
                parts.append("\n".join(f"- {q}" for q in turn.questions))
            elif turn.answer:
                parts.append(link_citations(turn.answer, self._page_count))
            if turn.error:
                parts.append(f"⚠ {turn.error}")
        return "\n\n".join(parts)

    def transcript_text(self) -> str:
        """当前可见内容的纯文本（空状态说明或全部气泡）。"""
        return self.chat.plain_text()

    # ------------------------------------------------------------------ 内部
    def _current(self) -> Turn | None:
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
        self.clear_btn.setEnabled(bool(self._turns) and not self._busy)

    def _schedule(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        """重排最后一轮（流式输出只动正在生成的那个气泡）。"""
        self._render_timer.stop()
        if self._turns:
            index = len(self._turns) - 1
            self.chat.show_turn(index, self._turns[index], self._page_count, last=True)
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

    def _copy_turn(self, index: int) -> None:
        text = tidy_markdown(self._turns[index].answer) if 0 <= index < len(self._turns) else ""
        clipboard = QGuiApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)
            self.chat.flash_copied(index)

    def _save_turn(self, index: int) -> None:
        if 0 <= index < len(self._turns) and self._turns[index].answer and not self._busy:
            self.saveNoteRequested.emit(tidy_markdown(self._turns[index].answer))
