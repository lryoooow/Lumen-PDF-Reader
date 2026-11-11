"""Lumen Pure 右侧多合一学术助读坞（划词翻译 · AI 导读 · 伴随笔记）。

借鉴成熟学术阅读器的「右栏多态协同」思路，按 Lumen Pure 设计重构：
* 顶部采用带弹性滑块（OutExpo）的三段胶囊切换器：``翻译`` / ``AI 导读`` / ``伴随笔记``；
* **翻译页**：实时同步当前选区原文、流式学术译文、离线词典释义与学科术语库对照，支持连翻拼接、
  去换行复制、存入引文库与一键转入全文段落对照；
* **AI 导读页**：承载各文档标签独立的 :class:`~pdfreader.gui.panels.ai_panel.AiPanel` 对话；
* **伴随笔记页**：无缝联动主窗口 Markdown 伴随笔记停靠区，保持草稿与来源锚点完整。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.panels.ai_chat import markdown_html
from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion
from pdfreader.gui.widgets.translate_popup import WordCard
from pdfreader.services.ai.textfix import tidy_markdown
from pdfreader.utils.text import clean_copied_text

if TYPE_CHECKING:
    from pdfreader.gui.selection_translator import SelectionTranslator

__all__ = ["RightStudioDock", "StudioSegmentedHeader", "TranslateStudioPane"]


class StudioSegmentedHeader(QFrame):
    """右侧助读坞顶部分段栏：弹性滑块切换 ``翻译 | AI 导读 | 伴随笔记`` + 右侧关闭按钮。"""

    tabSelected = Signal(int)
    closeRequested = Signal()

    def __init__(
        self,
        *,
        initial: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RightStudioHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(42)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(6)

        self._seg = QFrame(self)
        self._seg.setObjectName("Segmented")
        self._seg.setFixedHeight(28)
        self._thumb = QFrame(self._seg)
        self._thumb.setObjectName("SegmentedThumb")
        self._thumb_x = 2.0
        self._thumb_w = 72.0

        seg_lay = QHBoxLayout(self._seg)
        seg_lay.setContentsMargins(2, 2, 2, 2)
        seg_lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[QToolButton] = []
        for idx, title in enumerate(("翻译", "AI 导读", "伴随笔记")):
            btn = QToolButton(self._seg)
            btn.setObjectName("SegmentButton")
            btn.setText(title)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            if idx == initial:
                btn.setChecked(True)
            self._group.addButton(btn, idx)
            seg_lay.addWidget(btn, 1)
            self.buttons.append(btn)
        self._group.idClicked.connect(self._on_clicked)
        lay.addWidget(self._seg, stretch=1)

        self.close_btn = QToolButton(self)
        self.close_btn.setObjectName("HeaderIconButton")
        self.close_btn.setToolTip("收起右侧工作台")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.clicked.connect(self.closeRequested.emit)
        lay.addWidget(self.close_btn)

        self._refresh_icons()
        get_theme().changed.connect(self._refresh_icons)

    def _refresh_icons(self, _name: str = "") -> None:
        theme = get_theme()
        self.close_btn.setIcon(theme.colored_icon("close", theme.palette.text_muted, size=14))

    def set_active(self, index: int, *, animate: bool = True) -> None:
        if 0 <= index < len(self.buttons):
            self._group.blockSignals(True)
            self.buttons[index].setChecked(True)
            self._group.blockSignals(False)
            self._sync_thumb(animate=animate)

    def active_index(self) -> int:
        checked = self._group.checkedId()
        return checked if checked >= 0 else 0

    def _on_clicked(self, index: int) -> None:
        self._sync_thumb(animate=True)
        self.tabSelected.emit(index)

    def _sync_thumb(self, *, animate: bool) -> None:
        btn = self._group.checkedButton()
        if btn is None:
            return
        geo = btn.geometry()
        if geo.width() <= 0:
            return
        tx, tw = float(geo.x()), float(geo.width())
        self._thumb.lower()
        if not animate or not motion.enabled():
            self._thumb_x, self._thumb_w = tx, tw
            self._apply_thumb()
            return
        motion.tween(self._thumb, self._thumb_x, tx, TOKENS.anim_drop, self._set_tx, key="_st_x")
        motion.tween(self._thumb, self._thumb_w, tw, TOKENS.anim_drop, self._set_tw, key="_st_w")

    def _set_tx(self, v: float) -> None:
        self._thumb_x = v
        self._apply_thumb()

    def _set_tw(self, v: float) -> None:
        self._thumb_w = v
        self._apply_thumb()

    def _apply_thumb(self) -> None:
        h = max(20, self._seg.height() - 4)
        self._thumb.setGeometry(QRect(round(self._thumb_x), 2, max(24, round(self._thumb_w)), h))

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._sync_thumb(animate=False)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._sync_thumb(animate=False)


class TranslateStudioPane(QFrame):
    """右侧学术划词翻译工作台：选区原文对照、流式译文、本机词卡与术语库自动高亮。"""

    translateSelectionRequested = Signal()
    parallelRequested = Signal()
    continuousToggled = Signal(bool)
    clipQuoteRequested = Signal(str)
    continueInAiRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranslateStudioPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._translator: SelectionTranslator | None = None
        self._glossary_fn: Callable[[str], list[tuple[str, str]]] = lambda _t: []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        # ---- 顶部控制条：目标语种 · 连翻 · 全文对照
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        self.lang_label = QLabel("目标语种 · 简体中文")
        self.lang_label.setObjectName("AiProvider")
        ctrl.addWidget(self.lang_label)
        ctrl.addStretch(1)

        self.chain_btn = QToolButton(self)
        self.chain_btn.setObjectName("AiChip")
        self.chain_btn.setText("连翻拼接")
        self.chain_btn.setCheckable(True)
        self.chain_btn.setToolTip("跨栏 / 跨页连续选中多段自动拼接翻译 (Ctrl+Shift+L)")
        self.chain_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chain_btn.toggled.connect(self.continuousToggled.emit)
        ctrl.addWidget(self.chain_btn)

        self.parallel_btn = QToolButton(self)
        self.parallel_btn.setObjectName("AiChip")
        self.parallel_btn.setText("全文对照")
        self.parallel_btn.setToolTip("打开逐段双栏对照翻译视图")
        self.parallel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.parallel_btn.clicked.connect(self.parallelRequested.emit)
        ctrl.addWidget(self.parallel_btn)
        root.addLayout(ctrl)

        # ---- 滚动主体
        scroll = QScrollArea(self)
        scroll.setObjectName("TranslateScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 2, 0, 2)
        body_lay.setSpacing(10)

        # 1) 原文选区卡片
        src_card = QFrame(body)
        src_card.setObjectName("StudioCard")
        src_lay = QVBoxLayout(src_card)
        src_lay.setContentsMargins(12, 10, 12, 10)
        src_lay.setSpacing(6)
        src_head = QHBoxLayout()
        self.src_title = QLabel("原文选区")
        self.src_title.setObjectName("StudioSectionLabel")
        src_head.addWidget(self.src_title)
        src_head.addStretch(1)
        self.copy_src_btn = QToolButton(src_card)
        self.copy_src_btn.setObjectName("AiBubbleAction")
        self.copy_src_btn.setText("去换行复制")
        self.copy_src_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_src_btn.clicked.connect(self._copy_source)
        src_head.addWidget(self.copy_src_btn)
        src_lay.addLayout(src_head)

        self.source_label = QLabel("在左侧 PDF 页面划选任意词句，即可在此实时对照译文与学科术语。")
        self.source_label.setObjectName("TranslateSource")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        src_lay.addWidget(self.source_label)

        self.translate_now_btn = QPushButton("翻译当前选中文字")
        self.translate_now_btn.setProperty("variant", "primary")
        self.translate_now_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.translate_now_btn.clicked.connect(self.translateSelectionRequested.emit)
        src_lay.addWidget(self.translate_now_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        body_lay.addWidget(src_card)

        # 2) 学术译文 / 本机词卡
        ans_card = QFrame(body)
        ans_card.setObjectName("StudioAnswerCard")
        ans_lay = QVBoxLayout(ans_card)
        ans_lay.setContentsMargins(12, 10, 12, 10)
        ans_lay.setSpacing(8)
        ans_head = QHBoxLayout()
        self.ans_title = QLabel("学术译文")
        self.ans_title.setObjectName("StudioSectionLabel")
        ans_head.addWidget(self.ans_title)
        ans_head.addStretch(1)
        ans_lay.addLayout(ans_head)

        self.word_card = WordCard(ans_card)
        self.word_card.hide()
        ans_lay.addWidget(self.word_card)

        self.answer_label = QLabel("选中文字后点击悬浮条「译」或上方按钮开始翻译。")
        self.answer_label.setObjectName("TranslateText")
        self.answer_label.setWordWrap(True)
        self.answer_label.setTextFormat(Qt.TextFormat.RichText)
        self.answer_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ans_lay.addWidget(self.answer_label)

        self.notice_label = QLabel()
        self.notice_label.setObjectName("TranslateNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        ans_lay.addWidget(self.notice_label)

        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        self.copy_ans_btn = QToolButton(ans_card)
        self.copy_ans_btn.setObjectName("AiBubbleAction")
        self.copy_ans_btn.setText("复制译文")
        self.copy_ans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_ans_btn.clicked.connect(self._copy_answer)
        self.clip_btn = QToolButton(ans_card)
        self.clip_btn.setObjectName("AiBubbleAction")
        self.clip_btn.setText("＋ 存入引文")
        self.clip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clip_btn.clicked.connect(self._clip_quote)
        self.ask_ai_btn = QToolButton(ans_card)
        self.ask_ai_btn.setObjectName("AiBubbleAction")
        self.ask_ai_btn.setText("转 AI 追问 →")
        self.ask_ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ask_ai_btn.clicked.connect(self.continueInAiRequested.emit)
        act_row.addWidget(self.copy_ans_btn)
        act_row.addWidget(self.clip_btn)
        act_row.addStretch(1)
        act_row.addWidget(self.ask_ai_btn)
        self._ans_actions = QWidget(ans_card)
        self._ans_actions.setLayout(act_row)
        self._ans_actions.hide()
        ans_lay.addWidget(self._ans_actions)
        body_lay.addWidget(ans_card)

        # 3) 术语库命中对照
        self.terms_box = QFrame(body)
        self.terms_box.setObjectName("StudioCard")
        terms_lay = QVBoxLayout(self.terms_box)
        terms_lay.setContentsMargins(12, 9, 12, 9)
        terms_lay.setSpacing(6)
        terms_title = QLabel("命中的术语库译法")
        terms_title.setObjectName("StudioSectionLabel")
        terms_lay.addWidget(terms_title)
        self.terms_label = QLabel()
        self.terms_label.setObjectName("StudioMutedHint")
        self.terms_label.setWordWrap(True)
        terms_lay.addWidget(self.terms_label)
        self.terms_box.hide()
        body_lay.addWidget(self.terms_box)

        body_lay.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        get_theme().changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _name: str = "") -> None:
        self.refresh()

    def bind_translator(
        self,
        translator: SelectionTranslator,
        glossary_fn: Callable[[str], list[tuple[str, str]]],
    ) -> None:
        if self._translator is translator:
            self.refresh()
            return
        if self._translator is not None:
            try:
                self._translator.stateChanged.disconnect(self.refresh)
                self.word_card.saveToggled.disconnect(self._translator._on_word_save)
                self.word_card.explainRequested.disconnect(self._translator._on_word_explain)
            except (RuntimeError, TypeError):
                pass
        self._translator = translator
        self._glossary_fn = glossary_fn
        translator.stateChanged.connect(self.refresh)
        self.word_card.saveToggled.connect(translator._on_word_save)
        self.word_card.explainRequested.connect(translator._on_word_explain)
        self.refresh()

    def set_continuous_checked(self, on: bool) -> None:
        self.chain_btn.blockSignals(True)
        self.chain_btn.setChecked(on)
        self.chain_btn.blockSignals(False)

    def _current_source(self) -> str:
        if self._translator is None:
            return ""
        src = self._translator.source_text()
        if src:
            return src
        return clean_copied_text(self._translator._view.selected_text)

    def _copy_source(self) -> None:
        src = self._current_source()
        cb = QGuiApplication.clipboard()
        if src and cb is not None:
            cb.setText(src)
            self.copy_src_btn.setText("已复制")

    def _copy_answer(self) -> None:
        if self._translator is None:
            return
        ans = tidy_markdown(self._translator.popup.answer).strip()
        cb = QGuiApplication.clipboard()
        if ans and cb is not None:
            cb.setText(ans)
            self.copy_ans_btn.setText("已复制")

    def _clip_quote(self) -> None:
        src = self._current_source()
        if src:
            self.clipQuoteRequested.emit(src)

    def refresh(self) -> None:
        tr = self._translator
        if tr is None:
            return
        popup = tr.popup
        self.set_continuous_checked(tr.continuous)
        lang = tr._ai.settings.target_language or "简体中文"
        self.lang_label.setText(f"目标语种 · {lang}")
        self.copy_src_btn.setText("去换行复制")
        self.copy_ans_btn.setText("复制译文")

        src = self._current_source()
        segments = len(tr._segments)
        if src:
            suffix = f"（已拼接 {segments} 段）" if segments > 1 else ""
            self.src_title.setText(f"原文选区{suffix}")
            self.source_label.setText(src)
            self.translate_now_btn.setVisible(not bool(popup.answer) and not popup.word_mode)
        else:
            self.src_title.setText("原文选区")
            self.source_label.setText(
                "在左侧 PDF 页面划选任意词句，即可在此实时对照译文与学科术语。"
            )
            self.translate_now_btn.setVisible(False)

        entry = popup.word_card.entry
        if popup.word_mode and entry is not None:
            self.ans_title.setText("本机词典释义")
            self.word_card.set_entry(
                entry,
                saved=popup.word_card.save_btn.isChecked(),
                can_explain=not popup.word_card.explain_btn.isHidden(),
            )
            self.word_card.show()
        else:
            self.ans_title.setText("学术译文")
            self.word_card.hide()

        if popup.answer:
            self.answer_label.ensurePolished()
            self.answer_label.setText(
                markdown_html(
                    tidy_markdown(popup.answer), get_theme().palette, self.answer_label.font()
                )
            )
            self.answer_label.show()
            self._ans_actions.show()
        elif not popup.waiting.isHidden():
            self.answer_label.setText(popup.waiting.text())
            self.answer_label.show()
            self._ans_actions.hide()
        elif popup.word_mode:
            self.answer_label.hide()
            self._ans_actions.hide()
        else:
            self.answer_label.setText("选中文字后点击悬浮条「译」或上方按钮开始翻译。")
            self.answer_label.show()
            self._ans_actions.hide()

        notice = popup.notice.text()
        self.notice_label.setText(notice)
        self.notice_label.setVisible(bool(notice))

        terms = self._glossary_fn(src) if src else []
        if terms:
            self.terms_label.setText("  ·  ".join(f"{k} → {v}" for k, v in terms[:8]))
            self.terms_box.show()
        else:
            self.terms_box.hide()


class RightStudioDock(QFrame):
    """右侧多合一学术助读坞（翻译 · AI 导读 · 伴随笔记）。

    对外同时兼容原 ``QStackedWidget`` 的 ``indexOf / addWidget / removeWidget / setCurrentWidget``
    接口，使每个文档标签各自的 :class:`AiPanel` 无缝托管在 ``AI 导读`` 页中。
    """

    TAB_TRANSLATE = 0
    TAB_AI = 1
    TAB_NOTEBOOK = 2

    notebookRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiDock")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.header = StudioSegmentedHeader(initial=self.TAB_AI, parent=self)
        self.header.tabSelected.connect(self._on_header_tab)
        self.header.closeRequested.connect(self.closeRequested.emit)
        lay.addWidget(self.header)

        self._pages = QStackedWidget(self)
        self.translate_pane = TranslateStudioPane(self._pages)
        self._ai_stack = QStackedWidget(self._pages)
        self._pages.addWidget(self.translate_pane)
        self._pages.addWidget(self._ai_stack)
        self._pages.setCurrentIndex(self.TAB_AI)
        lay.addWidget(self._pages, stretch=1)

    # ---- 与原 QStackedWidget (AiDock) 兼容的托管接口 ----
    def indexOf(self, panel: AiPanel) -> int:
        return self._ai_stack.indexOf(panel)

    def addWidget(self, panel: AiPanel) -> int:
        panel.set_hosted(True)
        return self._ai_stack.addWidget(panel)

    def removeWidget(self, panel: AiPanel) -> None:
        self._ai_stack.removeWidget(panel)

    def setCurrentWidget(self, panel: AiPanel) -> None:
        self._ai_stack.setCurrentWidget(panel)

    def currentWidget(self) -> QWidget | None:
        w: QWidget | None = self._ai_stack.currentWidget()
        return w

    def count(self) -> int:
        return self._ai_stack.count()

    def current_ai_panel(self) -> QWidget | None:
        return self.currentWidget()

    # ---- 多合一工作台页签切换 ----
    def studio_tab(self) -> int:
        return self._pages.currentIndex()

    def set_studio_tab(self, index: int, *, animate: bool = True) -> None:
        if index == self.TAB_NOTEBOOK:
            self.notebookRequested.emit()
            return
        if index not in (self.TAB_TRANSLATE, self.TAB_AI):
            return
        changed = self._pages.currentIndex() != index
        self._pages.setCurrentIndex(index)
        self.header.set_active(index, animate=animate)
        if changed and animate:
            cur = self._pages.currentWidget()
            if cur is not None:
                motion.fade_up(cur, duration=TOKENS.anim_drop)

    def _on_header_tab(self, index: int) -> None:
        if index == self.TAB_NOTEBOOK:
            # 保持自身滑块先恢复到当前页，由主窗口打开伴随笔记停靠面板接力显示
            self.header.set_active(self._pages.currentIndex(), animate=False)
            self.notebookRequested.emit()
            return
        self.set_studio_tab(index, animate=True)
