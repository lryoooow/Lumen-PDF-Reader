"""划词翻译：选中文字 → 选区旁的「译」按钮（或直接翻译）→ 浮窗里流式显示译文。

借鉴小绿鲸的交互（见 ``memory/小绿鲸_功能实现分析.md``），按 Lumen 的方式实现：

- 触发方式三选一（设置 ``selection_translate``）：``button`` 选区旁出现「译」，点了才发送；
  ``auto`` 选中即翻译；``off`` 不出现（右键「AI 翻译」照常可用）。只有 AI 助读已配置好时才出现，
  不给没用 AI 的人添噪音。
- 发送前清洗（合并换行、连字、软连字符）；像乱码就不发，提示改用框选截图识别。
- 连翻：新选中的文字接在上一段后面一起翻译，跨栏 / 跨页断开的句子也能整句翻。
- 钉住：点页面别处时浮窗不收起；拖动过的浮窗也留在原位。
- 截图识别：模型支持图片就直接发截图，否则用本机 OCR 读出文字再翻译。
- 查词：选中单个英文单词时显示词卡（本机 ECDICT 词典，不联网、不需要 AI），可收藏进单词本、
  让 AI 结合本句解释；词典没收录时直接交给 AI。

每个文档标签一个实例：浮窗是该标签阅读视图的子控件；本对象挂在该标签的 AI 会话下
（标签关闭时视图先销毁，OCR 线程可能还在跑，要等它结束再回收，见 :meth:`busy`）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent

from pdfreader.engines.types import Rect
from pdfreader.gui.widgets.translate_popup import TranslatePill, TranslatePopup
from pdfreader.services.dictionary import Dictionary, WordEntry, default_dictionary, is_word
from pdfreader.services.materials import MaterialError, MaterialStore
from pdfreader.services.ocr import OCR_AVAILABLE
from pdfreader.utils.text import clean_copied_text, join_words, looks_garbled
from pdfreader.workers.ocr_worker import ImageOcrWorker

if TYPE_CHECKING:
    from pdfreader.gui.ai_assistant import AiAssistant
    from pdfreader.gui.widgets.pdf_view import PdfView
    from pdfreader.workers.ai_worker import AiWorker

__all__ = ["MAX_CONTINUOUS_CHARS", "SelectionTranslator"]

# 「直接翻译」模式下松开鼠标后等一会儿再发（连续拖选时只发最后一次）
_AUTO_DELAY_MS = 350
# 连翻累计的原文上限：超过后从头开始，避免越拼越长
MAX_CONTINUOUS_CHARS = 4000


class SelectionTranslator(QObject):
    """一个标签的划词翻译。

    Signals:
        continuousChanged(bool): 用户在浮窗上切换了连翻（主窗口记进设置、同步给其他标签）
        captureRequested(): 乱码提示里点了「框选识别」（主窗口进入框选，松开后交回 :meth:`translate_area`）
        materialsChanged(): 单词本有增删（主窗口刷新侧栏素材页）
    """

    continuousChanged = Signal(bool)
    captureRequested = Signal()
    materialsChanged = Signal()

    def __init__(
        self,
        view: PdfView,
        ai: AiAssistant,
        *,
        mode: str = "button",
        continuous: bool = False,
        notify: Callable[[str], None] | None = None,
        dictionary: Dictionary | None = None,
        materials: MaterialStore | None = None,
        doc_info: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(ai)
        self._view = view
        self._ai = ai
        self._mode = mode
        self._notify = notify or (lambda _m: None)
        self._dictionary = dictionary if dictionary is not None else default_dictionary()
        self._materials = materials
        self._doc_info = doc_info or (lambda: ("", ""))  # (路径, 标题)：收藏单词时记来源
        self._entry: WordEntry | None = None  # 词卡上的词
        self._word = ""  # 正在查的词（选中的原样）
        self.pill = TranslatePill(view)
        self.popup = TranslatePopup(view)
        self.popup.set_continuous(continuous)
        self._segments: list[str] = []  # 本次浮窗里翻译的原文（连翻时多段）
        self._page: int | None = None
        self._rect: Rect | None = None
        self._worker: AiWorker | None = None
        self._ocr: ImageOcrWorker | None = None
        self._ocr_threads: list[ImageOcrWorker] = []  # 含已作废但还没跑完的
        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.setInterval(_AUTO_DELAY_MS)
        self._auto.timeout.connect(self.translate_selection)

        view.selectionFinished.connect(self._on_selection)
        view.scrolled.connect(self.pill.hide)
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        self.pill.clicked.connect(self.translate_selection)
        self.popup.closed.connect(self._on_closed)
        self.popup.continuousToggled.connect(self._on_continuous_toggled)
        self.popup.captureRequested.connect(self._on_capture_requested)
        self.popup.panelRequested.connect(self._continue_in_panel)
        self.popup.wordSaveToggled.connect(self._on_word_save)
        self.popup.wordExplainRequested.connect(self._on_word_explain)

    # ------------------------------------------------------------------ 设置
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode != "button":
            self.pill.hide()

    @property
    def continuous(self) -> bool:
        return self.popup.continuous

    def set_continuous(self, on: bool) -> None:
        """外部（快捷键 / 其他标签）切换连翻。关掉时下一次重新开始拼接。"""
        self.popup.set_continuous(on)
        if not on:
            self._segments = self._segments[-1:]

    # ------------------------------------------------------------------ 触发
    def _on_selection(self) -> None:
        self.pill.hide()
        if self._mode == "off":
            return
        text = clean_copied_text(self._view.selected_text)
        word = self._dictionary.available and is_word(text)
        if not (word or self._ai.is_ready()):  # 查词不需要 AI；翻译需要
            return
        if self._mode == "auto":
            self._auto.start()
            return
        rect = self._view.selection_view_rect(last_word=True)
        if rect is not None:
            self.pill.show_near(rect)

    def translate_selection(self) -> None:
        """翻译当前选中的文字（「译」按钮 / 直接翻译 / 快捷入口）。"""
        self._auto.stop()
        self.pill.hide()
        text = clean_copied_text(self._view.selected_text)
        if not text:
            return
        picked = self._view.selection_rects()
        page, rect = None, None
        if picked is not None and picked[1]:
            rects = picked[1]
            page = picked[0]
            rect = Rect(
                min(r.x0 for r in rects),
                min(r.y0 for r in rects),
                max(r.x1 for r in rects),
                max(r.y1 for r in rects),
            )
        self._page, self._rect = page, rect
        self.popup.set_language(self._ai.settings.target_language)
        if looks_garbled(text):
            self._stop_requests()
            self.popup.show_garbled(text)
            self.popup.place_near(self._view.selection_view_rect())
            return
        if is_word(text) and not self._continuing():
            self.lookup_word(text)
            return
        self._add_segment(text)
        self._translate_text(self.source_text())

    def _continuing(self) -> bool:
        """连翻正在拼一句话：这时选中的单个词也接到句子后面，而不是查词。"""
        return (
            self.popup.continuous
            and self.popup.isVisible()
            and not self.popup.word_mode
            and bool(self._segments)
        )

    # ------------------------------------------------------------------ 查词
    def lookup_word(self, text: str) -> None:
        """查一个单词：词典里有就显示词卡；没有则交给 AI（AI 未配置时提示）。"""
        self._stop_requests()
        self._segments = []
        self._word = text.strip()
        entry = self._dictionary.lookup(text)
        self._entry = entry
        if entry is None:
            if self._ai.is_ready():
                self._explain_word(keep_card=False)
            else:
                self.popup.show_message(
                    f"词典里没有「{self._word}」。在「模型设置」里配置 AI 助读后，可以让 AI 解释。"
                )
                self.popup.place_near(self._view.selection_view_rect())
            return
        self.popup.show_word(
            entry, saved=self._is_saved(entry.word), can_explain=self._ai.is_ready()
        )
        self.popup.place_near(self._view.selection_view_rect())

    def _explain_word(self, *, keep_card: bool) -> None:
        def prepare(worker: AiWorker) -> None:
            self.popup.begin_word(self._word, keep_card=keep_card)
            self.popup.place_near(self._view.selection_view_rect())
            self._attach(worker)

        self._ai.quick_word(self._word, self._page, self._rect, prepare=prepare)

    def _on_word_explain(self) -> None:
        if self._word:
            self._stop_requests()
            self._explain_word(keep_card=True)

    def _is_saved(self, word: str) -> bool:
        if self._materials is None:
            return False
        try:
            return self._materials.find("word", word) is not None
        except MaterialError:
            return False

    def _on_word_save(self, save: bool) -> None:
        entry = self._entry
        if entry is None or self._materials is None:
            self.popup.set_word_saved(False)
            return
        try:
            if save:
                path, title = self._doc_info()
                self._materials.add(
                    "word",
                    entry.word,
                    entry.summary(3),
                    doc_path=path,
                    doc_title=title,
                    page=self._page,
                    extra={"phonetic": entry.phonetic} if entry.phonetic else None,
                )
            else:
                existing = self._materials.find("word", entry.word)
                if existing is not None:
                    self._materials.remove(existing.id)
        except MaterialError as exc:
            self.popup.set_word_saved(not save)
            self._notify(f"单词本：{exc}")
            return
        self.popup.set_word_saved(save)
        self._notify(f"已把 {entry.word} 加入单词本" if save else f"已从单词本移除 {entry.word}")
        self.materialsChanged.emit()

    def translate_area(self, page: int, rect: Rect) -> None:
        """框选区域截图翻译（乱码 PDF）：视觉模型直接读图，否则本机 OCR 后再翻译。"""
        self._page, self._rect = page, rect
        png = self._ai.capture_png(page, rect)
        if not png:
            self._notify("没能截取框选区域")
            return
        self.popup.set_language(self._ai.settings.target_language)
        self._stop_requests()
        if self._ai.supports_image:

            def prepare(worker: AiWorker) -> None:
                self._segments = []
                self.popup.begin("（框选区域截图）")
                self.popup.place_near(None)
                self._attach(worker)

            self._ai.quick_translate(image=png, prepare=prepare)
            return
        if not OCR_AVAILABLE:
            self.popup.show_message(
                "本机没有可用的 OCR，且当前模型未开启图片输入；"
                "可在「模型设置」里改用视觉模型并勾选「支持图片输入」。"
            )
            self.popup.place_near(None)
            return
        self.popup.begin("（正在识别框选区域的文字…）")
        self.popup.place_near(None)
        ocr = ImageOcrWorker(png, self)
        ocr.recognized.connect(self._on_ocr_text)
        ocr.failed.connect(self._on_ocr_failed)
        ocr.finished.connect(self._on_ocr_thread_finished)
        self._ocr = ocr
        self._ocr_threads.append(ocr)
        ocr.start()

    # ------------------------------------------------------------------ 连翻
    def source_text(self) -> str:
        """当前浮窗翻译的原文（连翻时为各段拼接）。"""
        text = ""
        for piece in self._segments:
            text = text + join_words(text, piece) + piece if text else piece
        return text

    def _add_segment(self, text: str) -> None:
        keep = self.popup.continuous and self.popup.isVisible() and bool(self._segments)
        if keep and self._segments[-1] == text:
            return  # 同一段又点了一次「译」
        if not keep or len(self.source_text()) + len(text) > MAX_CONTINUOUS_CHARS:
            self._segments = []
        self._segments.append(text)

    def _on_continuous_toggled(self, on: bool) -> None:
        if not on:
            self._segments = self._segments[-1:]
        self.continuousChanged.emit(on)
        self._notify("连翻已开启：接着选的文字会拼在一起翻译" if on else "连翻已关闭")

    # ------------------------------------------------------------------ 请求
    def _translate_text(self, text: str) -> None:
        self._stop_requests()

        def prepare(worker: AiWorker) -> None:
            self.popup.begin(text, len(self._segments))
            self.popup.place_near(self._view.selection_view_rect())
            self._attach(worker)

        self._ai.quick_translate(text, prepare=prepare)

    def _attach(self, worker: AiWorker) -> None:
        self._worker = worker
        worker.delta.connect(self._on_delta)
        worker.finished_text.connect(self._on_finished)
        worker.failed.connect(self._on_failed)

    def _is_current(self) -> bool:
        return self._worker is not None and self.sender() is self._worker

    def _on_delta(self, text: str) -> None:
        if self._is_current():
            self.popup.append(text)

    def _on_finished(self, text: str) -> None:
        if self._is_current():
            self._worker = None
            self.popup.finish(text)

    def _on_failed(self, message: str) -> None:
        if self._is_current():
            self._worker = None
            self.popup.fail(message)

    def _on_ocr_text(self, text: str) -> None:
        if self.sender() is not self._ocr:
            return
        self._ocr = None
        text = clean_copied_text(text)
        if not text:
            self.popup.show_message("框选区域里没有识别出文字。")
            return
        self._segments = [text]
        self._translate_text(text)

    def _on_ocr_failed(self, message: str) -> None:
        if self.sender() is self._ocr:
            self._ocr = None
            self.popup.fail(message)

    def _on_ocr_thread_finished(self) -> None:
        thread = self.sender()
        if isinstance(thread, ImageOcrWorker) and thread in self._ocr_threads:
            self._ocr_threads.remove(thread)
            thread.deleteLater()

    def _stop_requests(self) -> None:
        self._worker = None
        self._ai.stop_quick()
        if self._ocr is not None:
            # OCR 无法中途打断：断开结果即可，线程跑完自行释放
            self._ocr.recognized.disconnect(self._on_ocr_text)
            self._ocr.failed.disconnect(self._on_ocr_failed)
            self._ocr = None

    # ------------------------------------------------------------------ 浮窗按钮
    def _on_closed(self) -> None:
        self._stop_requests()
        self._segments = []

    def _on_capture_requested(self) -> None:
        self.popup.dismiss()
        self.captureRequested.emit()

    def _continue_in_panel(self) -> None:
        """转到 AI 面板（进对话历史，可以接着追问）。"""
        if self.popup.word_mode or not self._segments:
            if self._word:
                self._ai.explain(self._word, self._page, self._rect)
            return
        self._ai.translate(self.source_text(), self._page, self._rect)

    def reset(self) -> None:
        """换文档 / 关标签：收起并停止请求。"""
        self._auto.stop()
        self._stop_requests()
        self._segments = []
        self.pill.hide()
        self.popup.dismiss()

    def busy(self) -> bool:
        """还有截图 OCR 线程在跑（标签关闭后要等它结束再回收）。"""
        return any(t.isRunning() for t in self._ocr_threads)

    def shutdown(self) -> None:
        """退出前：作废结果并等 OCR 线程结束（运行中销毁 QThread 会让进程崩溃）。"""
        self._auto.stop()
        self._stop_requests()
        for thread in self._ocr_threads:
            thread.wait(10000)

    # ------------------------------------------------------------------ 收起时机
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and watched is self._view.viewport():
            # 点页面别处：小按钮总是收起；浮窗没钉住、也没开连翻时收起
            self.pill.hide()
            self._auto.stop()
            if self.popup.isVisible() and not (self.popup.pinned or self.popup.continuous):
                self.popup.dismiss()
                self._stop_requests()
        elif (
            kind == QEvent.Type.KeyPress
            and watched is self._view
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            self.pill.hide()
            if self.popup.isVisible() and not self.popup.pinned:
                self.popup.dismiss()
                self._stop_requests()
        return False
