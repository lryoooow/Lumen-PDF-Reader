"""划词翻译浮窗。

::

    选中的文字……………………  [译]          ← TranslatePill：选区右下角的小按钮
    ┌────────────────────────────────────┐
    │ 划词翻译 → 简体中文     [连] [钉] [×] │  ← 标题栏可拖动；连翻 / 钉住 / 关闭
    │ Attention is all you need…  · 2 段  │  ← 原文（一行，悬停看全文）
    │ 注意力就是你所需要的一切。            │  ← 译文（Markdown，流式）
    │ 复制 · 在 AI 面板继续                │
    └────────────────────────────────────┘

选中的是单个英文单词时，浮窗改为词卡（本机词典，不联网）::

    │ attention  /əˈtenʃən/          ☆ 收藏 │
    │ n. 注意，注意力                        │
    │ 复数 attentions · 四级 · 考研          │
    │ 结合本句解释（AI） →                   │

两者都是阅读视图的子控件（浮在页面上、不抢焦点、不随页面滚动）；
何时出现、发什么请求由 :class:`~pdfreader.gui.selection_translator.SelectionTranslator` 决定，
这里只管显示、拖动与按钮。
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.panels.ai_chat import markdown_html
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion
from pdfreader.services.ai.textfix import tidy_markdown
from pdfreader.services.dictionary import WordEntry

__all__ = ["TranslatePill", "TranslatePopup", "WordCard"]

_MARGIN = 8  # 浮窗离视图边缘至少留这么多
_GAP = 8  # 浮窗与选区的间距
_SOURCE_CHARS = 120  # 原文行最多显示的字数（全文在悬停提示里）
_TOOLTIP_CHARS = 1500
# 释义行开头的词性 / 学科标注（「vt.」「[计]」），显示成淡色
_SENSE_HEAD = re.compile(r"^((?:[a-z]+\.\s*)+|\[[^\]]{1,6}\]\s*)")


def _clamp(pos: QPoint, size: QSize, area: QRect) -> QPoint:
    x = max(area.left() + _MARGIN, min(pos.x(), area.right() - size.width() - _MARGIN))
    y = max(area.top() + _MARGIN, min(pos.y(), area.bottom() - size.height() - _MARGIN))
    return QPoint(x, y)


class TranslatePill(QToolButton):
    """选区右下角的「译」按钮（点击才翻译，避免每次选字都把内容发出去）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TranslatePill")
        self.setText("译")
        self.setToolTip("翻译选中的文字")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(13, 13))
        self._refresh_icon()
        get_theme().changed.connect(self._refresh_icon)
        self.hide()

    def _refresh_icon(self, _name: str = "") -> None:
        theme = get_theme()
        self.setIcon(theme.colored_icon("translate", theme.palette.accent_text, size=13))

    def show_near(self, anchor: QRect) -> None:
        """贴在选区末尾（最后一个词）的右下方；超出视图时收回来。"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        self.move(_clamp(anchor.bottomRight() + QPoint(4, 4), self.size(), parent.rect()))
        self.show()
        self.raise_()
        motion.pop_in(self)


class _Header(QWidget):
    """标题栏：按住拖动整个浮窗。"""

    dragged = Signal(QPoint)  # 本次拖动的位移

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._press: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is not None:
            now = event.globalPosition().toPoint()
            self.dragged.emit(now - self._press)
            self._press = now
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press = None
        super().mouseReleaseEvent(event)


class WordCard(QWidget):
    """词卡：单词 + 音标 + 收藏、释义、词形与考试标签、「结合本句解释」。

    Signals:
        saveToggled(bool): 点了收藏（True = 要加入单词本）
        explainRequested(): 「结合本句解释（AI）」
    """

    saveToggled = Signal(bool)
    explainRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.word = QLabel()
        self.word.setObjectName("WordTitle")
        self.word.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.phonetic = QLabel()
        self.phonetic.setObjectName("WordPhonetic")
        head.addWidget(self.word)
        head.addWidget(self.phonetic)
        head.addStretch(1)
        self.save_btn = QToolButton()
        self.save_btn.setObjectName("AiBubbleAction")
        self.save_btn.setCheckable(True)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.save_btn.setIconSize(QSize(13, 13))
        self.save_btn.clicked.connect(self._on_save_clicked)
        head.addWidget(self.save_btn)
        lay.addLayout(head)
        self.note = QLabel()
        self.note.setObjectName("WordNote")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.senses = QLabel()
        self.senses.setObjectName("WordSenses")
        self.senses.setWordWrap(True)
        self.senses.setTextFormat(Qt.TextFormat.RichText)
        self.senses.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.senses)
        self.meta = QLabel()
        self.meta.setObjectName("WordNote")
        self.meta.setWordWrap(True)
        lay.addWidget(self.meta)
        self.explain_btn = QPushButton("结合本句解释（AI） →")
        self.explain_btn.setObjectName("LinkButton")
        self.explain_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.explain_btn.clicked.connect(self.explainRequested)
        lay.addWidget(self.explain_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._entry: WordEntry | None = None
        self._refresh()
        get_theme().changed.connect(self._refresh)

    @property
    def entry(self) -> WordEntry | None:
        return self._entry

    def set_entry(self, entry: WordEntry, *, saved: bool, can_explain: bool) -> None:
        self._entry = entry
        self.word.setText(entry.word)
        self.phonetic.setText(f"/{entry.phonetic}/" if entry.phonetic else "")
        self.note.setText(entry.note)
        self.note.setHidden(not entry.note)
        parts = [f"{name} {form}" for name, form in entry.forms[:4]] + list(entry.tags[:4])
        self.meta.setText("  ·  ".join(parts))
        self.meta.setHidden(not parts)
        self.explain_btn.setHidden(not can_explain)
        self.set_saved(saved)

    def set_saved(self, saved: bool) -> None:
        self.save_btn.setChecked(saved)
        self.save_btn.setText("已收藏" if saved else "收藏")
        self.save_btn.setToolTip("从单词本移除" if saved else "加入单词本")
        self._refresh()

    def _on_save_clicked(self) -> None:
        self.saveToggled.emit(bool(self.save_btn.isChecked()))

    def _refresh(self, _name: str = "") -> None:
        theme = get_theme()
        palette = theme.palette
        color = palette.accent_text if self.save_btn.isChecked() else palette.text_muted
        self.save_btn.setIcon(theme.colored_icon("bookmark", color, size=13))
        if self._entry is None:
            return
        rows = []
        for line in self._entry.senses:
            head = _SENSE_HEAD.match(line)
            prefix, rest = (head.group(1), line[head.end() :]) if head else ("", line)
            muted = (
                f'<span style="color:{palette.text_muted};">{html.escape(prefix)}</span>'
                if prefix
                else ""
            )
            rows.append(muted + html.escape(rest))
        self.senses.setText("<br>".join(rows))


class TranslatePopup(QFrame):
    """译文浮窗。

    Signals:
        closed(): 点了 ×
        pinnedChanged(bool): 钉住 / 取消钉住
        continuousToggled(bool): 连翻开 / 关
        captureRequested(): 乱码提示里的「框选识别」
        panelRequested(): 「在 AI 面板继续」
        wordSaveToggled(bool): 词卡上点了收藏 / 取消收藏
        wordExplainRequested(): 词卡上点了「结合本句解释」
    """

    closed = Signal()
    pinnedChanged = Signal(bool)
    continuousToggled = Signal(bool)
    captureRequested = Signal()
    panelRequested = Signal()
    wordSaveToggled = Signal(bool)
    wordExplainRequested = Signal()

    WIDTH = 360
    BODY_MAX_HEIGHT = 260

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TranslatePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 10)
        lay.setSpacing(6)

        header = _Header()
        header.dragged.connect(self._drag)
        head = QHBoxLayout(header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(2)
        self.title = QLabel("划词翻译")
        self.title.setObjectName("TranslateTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.chain_btn = self._tool("chain", "连翻：接着选的文字拼在一起翻译（跨栏 / 跨页的句子）")
        self.chain_btn.setCheckable(True)
        self.chain_btn.toggled.connect(self.continuousToggled)
        self.pin_btn = self._tool("pin", "钉住：点别处时不收起")
        self.pin_btn.setCheckable(True)
        self.pin_btn.toggled.connect(self.pinnedChanged)
        for btn in (self.chain_btn, self.pin_btn):
            btn.toggled.connect(lambda _on: self._refresh_icons())
        self.close_btn = self._tool("close", "关闭")
        self.close_btn.clicked.connect(self._on_close)
        for btn in (self.chain_btn, self.pin_btn, self.close_btn):
            head.addWidget(btn)
        lay.addWidget(header)

        self.source = QLabel()
        self.source.setObjectName("TranslateSource")
        self.source.setWordWrap(True)
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.source)

        self.word_card = WordCard()
        self.word_card.saveToggled.connect(self.wordSaveToggled)
        self.word_card.explainRequested.connect(self.wordExplainRequested)
        lay.addWidget(self.word_card)

        self.waiting = QLabel("正在翻译…")
        self.waiting.setObjectName("AiWaitText")
        lay.addWidget(self.waiting)

        self.body = QLabel()
        self.body.setObjectName("TranslateText")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TranslateScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll)

        self.notice = QLabel()
        self.notice.setObjectName("TranslateNotice")
        self.notice.setWordWrap(True)
        lay.addWidget(self.notice)
        self.capture_btn = QPushButton("框选这段文字，截图识别后翻译 →")
        self.capture_btn.setObjectName("LinkButton")
        self.capture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.capture_btn.clicked.connect(self.captureRequested)
        lay.addWidget(self.capture_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self.actions = QWidget()
        act = QHBoxLayout(self.actions)
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(2)
        self.copy_btn = self._action("copy", "复制", self.copy_translation)
        self.panel_btn = self._action("ai", "在 AI 面板继续", self.panelRequested.emit)
        act.addWidget(self.copy_btn)
        act.addWidget(self.panel_btn)
        act.addStretch(1)
        lay.addWidget(self.actions)

        self._answer = ""
        self._language = "简体中文"
        self._word_mode = False  # 显示词卡（AI 解释接在词卡下面）
        self._moved = False  # 用户拖过：之后的翻译不再跟着选区挪
        self._refresh_icons()
        get_theme().changed.connect(self._refresh_icons)
        self._show_state()
        self.hide()

    # ------------------------------------------------------------------ 状态
    @property
    def pinned(self) -> bool:
        return bool(self.pin_btn.isChecked())

    @property
    def continuous(self) -> bool:
        return bool(self.chain_btn.isChecked())

    def set_continuous(self, on: bool) -> None:
        """同步连翻开关（不发 continuousToggled）。"""
        self.chain_btn.blockSignals(True)
        self.chain_btn.setChecked(on)
        self.chain_btn.blockSignals(False)
        self._refresh_icons()

    @property
    def answer(self) -> str:
        return self._answer

    def set_language(self, language: str) -> None:
        self._language = language
        self.title.setText(f"划词翻译 → {language}")

    @property
    def word_mode(self) -> bool:
        return self._word_mode

    # ------------------------------------------------------------------ 内容
    def begin(self, source: str, segments: int = 1) -> None:
        """开始一次翻译：显示原文与「正在翻译…」。"""
        self._answer = ""
        self._word_mode = False
        self.set_language(self._language)
        self._set_source(source, segments)
        self.notice.clear()
        self.waiting.setText("正在翻译…")
        self._show_state(waiting=True)

    def show_word(self, entry: WordEntry, *, saved: bool, can_explain: bool) -> None:
        """单词：显示词卡（本机词典，瞬间出结果）。"""
        self._answer = ""
        self._word_mode = True
        self.title.setText("查词")
        self._set_source("")
        self.body.clear()
        self.notice.clear()
        self.word_card.set_entry(entry, saved=saved, can_explain=can_explain)
        self._show_state()

    def begin_word(self, word: str, *, keep_card: bool) -> None:
        """让 AI 解释单词：``keep_card`` 时接在词卡下面，否则（词典没收录）只显示单词。"""
        self._answer = ""
        self._word_mode = keep_card
        self.title.setText("查词")
        self._set_source("" if keep_card else word)
        self.notice.clear()
        self.waiting.setText("AI 正在解释…")
        if keep_card:
            self.word_card.explain_btn.hide()
        self._show_state(waiting=True)

    def set_word_saved(self, saved: bool) -> None:
        self.word_card.set_saved(saved)

    def _set_source(self, source: str, segments: int = 1) -> None:
        clipped = source if len(source) <= _SOURCE_CHARS else source[: _SOURCE_CHARS - 1] + "…"
        suffix = f"  · 已拼接 {segments} 段" if segments > 1 else ""
        self.source.setText(clipped + suffix if source else "")
        self.source.setHidden(not source)
        self.source.setToolTip(source[:_TOOLTIP_CHARS] if len(source) > _SOURCE_CHARS else "")

    def append(self, delta: str) -> None:
        self._answer += delta
        self._render()

    def finish(self, text: str) -> None:
        self._answer = text
        self._render(done=True)

    def fail(self, message: str) -> None:
        self.notice.setText(f"⚠️ {message}")
        self._show_state(done=bool(self._answer))

    def show_garbled(self, source: str) -> None:
        """选中的文字像乱码：不发送，提示改用截图识别（原文行显示那段乱码本身）。"""
        self._answer = ""
        self._word_mode = False
        self._set_source(source)
        self.body.clear()
        self.notice.setText(
            "⚠️ 选中的文字像是乱码（这份 PDF 的字体缺少文字映射），翻译出来也没有意义。"
        )
        self._show_state(capture=True)

    def show_message(self, text: str) -> None:
        self._answer = ""
        self._word_mode = False
        self.body.clear()
        self.notice.setText(text)
        self._show_state()

    def copy_translation(self) -> None:
        text = tidy_markdown(self._answer).strip()
        clipboard = QGuiApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)
            self.copy_btn.setText("已复制")

    def _render(self, *, done: bool = False) -> None:
        if self._answer:
            self.body.ensurePolished()
            self.body.setText(
                markdown_html(tidy_markdown(self._answer), get_theme().palette, self.body.font())
            )
        self._show_state(waiting=not self._answer, done=done)

    def _show_state(
        self, *, waiting: bool = False, done: bool = False, capture: bool = False
    ) -> None:
        # setHidden 而不是 setVisible：浮窗自己还没显示时也要记下各部分的显隐，_fit 据此算高度
        self.waiting.setHidden(not waiting)
        self.word_card.setHidden(not self._word_mode)
        has_text = bool(self._answer)
        self.scroll.setHidden(not has_text)
        self.notice.setHidden(not self.notice.text())
        self.capture_btn.setHidden(not capture)
        self.actions.setHidden(not (done and has_text))
        self.copy_btn.setText("复制")
        self._fit()

    def _fit(self) -> None:
        """按内容定高：换行的标签要用 heightForWidth 算，adjustSize 会把它们压成一行。"""
        margins = self.layout().contentsMargins()
        inner = self.WIDTH - margins.left() - margins.right()
        if not self.scroll.isHidden():
            width = inner - self.scroll.verticalScrollBar().sizeHint().width()
            height = self.body.heightForWidth(width)
            if height <= self.BODY_MAX_HEIGHT:
                width = inner  # 不需要滚动条：整宽显示
                height = self.body.heightForWidth(width)
            self.scroll.setFixedHeight(min(height, self.BODY_MAX_HEIGHT) + 2)
        lay = self.layout()
        lay.invalidate()
        lay.activate()
        self.setFixedHeight(lay.totalHeightForWidth(self.WIDTH))
        parent = self.parentWidget()
        if parent is not None and self.isVisible():
            self.move(_clamp(self.pos(), self.size(), parent.rect()))

    # ------------------------------------------------------------------ 位置
    def place_near(self, selection: QRect | None) -> None:
        """显示在选区下方（放不下就放上方）；钉住或被拖动过时保持原位。"""
        parent = self.parentWidget()
        if parent is None:
            return
        area = parent.rect()
        was_visible = self.isVisible()
        self._fit()
        if selection is not None and not (was_visible and (self.pinned or self._moved)):
            below = QPoint(selection.left(), selection.bottom() + _GAP)
            if below.y() + self.height() > area.bottom() - _MARGIN:
                below = QPoint(selection.left(), selection.top() - self.height() - _GAP)
            self.move(_clamp(below, self.size(), area))
        elif not was_visible and selection is None:
            self.move(_clamp(QPoint(area.width() - self.width(), _MARGIN), self.size(), area))
        self.show()
        self.raise_()
        if not was_visible:
            motion.drop_in(self, duration=TOKENS.anim_drop)

    def dismiss(self) -> None:
        """收起（下次出现重新跟随选区）。"""
        self._moved = False
        self.hide()

    def _drag(self, delta: QPoint) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self._moved = True
        self.move(_clamp(self.pos() + delta, self.size(), parent.rect()))

    def _on_close(self) -> None:
        self.dismiss()
        self.closed.emit()

    # ------------------------------------------------------------------ 控件
    def _tool(self, icon: str, tip: str) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("TranslateTool")
        btn.setProperty("iconName", icon)
        btn.setToolTip(tip)
        btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _action(self, icon: str, text: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("AiBubbleAction")
        btn.setText(text)
        btn.setProperty("iconName", icon)
        btn.setIconSize(QSize(13, 13))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _refresh_icons(self, _name: str = "") -> None:
        theme = get_theme()
        muted, accent = theme.palette.text_muted, theme.palette.accent_text
        for btn in (self.chain_btn, self.pin_btn, self.close_btn, self.copy_btn, self.panel_btn):
            size = 14 if btn.objectName() == "TranslateTool" else 13
            color = accent if btn.isChecked() else muted  # 钉住 / 连翻开着时用强调色
            btn.setIcon(theme.colored_icon(str(btn.property("iconName")), color, size=size))
        self.style().unpolish(self)
        self.style().polish(self)
