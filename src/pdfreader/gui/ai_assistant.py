"""AI 助读编排：把「翻译 / 解释 / 总结 / 提问 / 框选解释」接到 AI 面板与后台线程。

主窗口只负责把用户动作转过来；这里决定发什么、发给谁、何时需要用户确认：

- 未启用或未配置时引导去「模型设置」；
- 首次向某个远程服务发送内容前征得同意（可勾「不再询问」），本机服务不问；
- 加密 PDF 每个文档会话确认一次；单次外发超过 ``LARGE_SEND_CHARS`` 字符时提醒；
- 文本在主线程取好（PyMuPDF 非线程安全），后台线程只做网络请求。
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRect
from PySide6.QtWidgets import QCheckBox, QDialog, QMessageBox, QWidget

from pdfreader.controllers import DocumentController
from pdfreader.engines.types import Point, Rect
from pdfreader.gui.dialogs.ai_settings_dialog import AiSettingsDialog, ClientFactory
from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.services.ai import prompts
from pdfreader.services.ai.client import ChatClient
from pdfreader.services.ai.context import overview_context, question_context
from pdfreader.services.ai.keystore import KeyStore, KeyStoreError, default_keystore
from pdfreader.services.ai.provider import AiSettings, AiSettingsStore, ProviderConfig
from pdfreader.workers.ai_worker import AiWorker

__all__ = ["LARGE_SEND_CHARS", "AiAssistant"]

# 单次外发超过这么多字符时提醒（约等于一篇中等长度论文的全文）
LARGE_SEND_CHARS = 40000
# 文字层少于这么多字符的页视为扫描页，有 OCR 结果时用 OCR 文字代替
_SCANNED_PAGE_CHARS = 20
# 框选区域渲染：目标最长边像素（够看清公式，又不至于图片过大）
_CAPTURE_MAX_SIDE = 1600
_CAPTURE_MAX_ZOOM = 4.0

Message = dict[str, Any]


class AiAssistant(QObject):
    """AI 助读的会话与请求管理。

    Args:
        parent: 对话框的父窗口
        controller: 文档控制器（取文字、渲染、添加便签）
        panel: 右侧 AI 面板
        current_page: 返回当前页（0 起）
        navigate: 跳转到 (页, 页内 y)
        show_panel: 打开右侧面板
        notify: 状态栏提示
        on_annotation_added: AI 笔记存为便签后通知主窗口刷新该页
    """

    def __init__(
        self,
        parent: QWidget,
        controller: DocumentController,
        panel: AiPanel,
        *,
        current_page: Callable[[], int],
        navigate: Callable[[int, float | None], None],
        show_panel: Callable[[], None],
        notify: Callable[[str], None],
        on_annotation_added: Callable[[int], None],
        start_ocr: Callable[[], None] | None = None,
        store: AiSettingsStore | None = None,
        keystore: KeyStore | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = parent
        self._controller = controller
        self._panel = panel
        self._current_page = current_page
        self._navigate = navigate
        self._show_panel = show_panel
        self._notify = notify
        self._on_annotation_added = on_annotation_added
        self._start_ocr = start_ocr
        self._store = store or AiSettingsStore()
        self._keystore = keystore or default_keystore()
        self._client_factory: ClientFactory = client_factory or (lambda b, k: ChatClient(b, k))
        self._settings = self._store.load()
        self._worker: AiWorker | None = None
        self._retired: list[AiWorker] = []
        self._kind = "chat"
        self._question = ""
        self._history: list[Message] = []
        self._note_anchor: tuple[int, float, float] | None = None
        self._encrypted_ok = False
        self._texts: list[str] | None = None
        # 本次会话的 OCR 识别结果（页 → 文字）：扫描页没有文字层时用它代替，不必先另存
        self._ocr: dict[int, str] = {}
        self._ocr_page_count = 0

        panel.askRequested.connect(self.ask)
        panel.actionRequested.connect(self.run_action)
        panel.stopRequested.connect(self.stop)
        panel.settingsRequested.connect(self.open_settings)
        panel.citationClicked.connect(lambda page: self._navigate(page - 1, None))
        panel.saveNoteRequested.connect(self.save_note)
        panel.clearRequested.connect(self.clear)
        self.refresh_panel()

    # ------------------------------------------------------------------ 状态
    @property
    def settings(self) -> AiSettings:
        return self._settings

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def workers(self) -> list[AiWorker]:
        return [w for w in (self._worker, *self._retired) if w is not None]

    def is_ready(self) -> bool:
        provider = self._settings.active()
        if not self._settings.enabled or provider is None or not provider.is_usable():
            return False
        return not provider.needs_key or bool(self._key(provider))

    def _key(self, provider: ProviderConfig) -> str:
        try:
            return self._keystore.get(provider.id)
        except KeyStoreError:
            return ""

    def refresh_panel(self) -> None:
        provider = self._settings.active()
        ready = self.is_ready()
        self._panel.set_configured(ready)
        if provider is None or not self._settings.enabled:
            self._panel.set_provider_label("未启用")
        else:
            where = "本机" if provider.is_local else provider.host
            self._panel.set_provider_label(
                f"{provider.name} · {provider.model or '未选模型'} · {where}"
            )
        self._panel.set_page_count(self._controller.page_count)

    # ------------------------------------------------------------------ 设置
    def open_settings(self) -> bool:
        dialog = AiSettingsDialog(
            self._settings, self._keystore, self._window, client_factory=self._client_factory
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.apply_settings(dialog.result_settings())
        return True

    def apply_settings(self, settings: AiSettings) -> None:
        self._settings = settings
        try:
            self._store.save(settings)
        except OSError as exc:
            self._notify(f"AI 设置保存失败：{exc}")
        self.refresh_panel()

    # ------------------------------------------------------------------ 文档会话
    def reset_document(self) -> None:
        """切换 / 关闭文档：停掉请求，清空对话与缓存。"""
        self.stop()
        self._history.clear()
        self._note_anchor = None
        self._encrypted_ok = False
        self._texts = None
        self._ocr.clear()
        self._panel.clear()
        self._panel.set_page_count(self._controller.page_count)

    def invalidate_text(self) -> None:
        """文档内容变了（删页 / 合并 / OCR 等）：下次重新取文字。"""
        self._texts = None
        if self._controller.page_count != self._ocr_page_count:
            self._ocr.clear()  # 页数变了，页码已对不上 OCR 结果
        self._panel.set_page_count(self._controller.page_count)

    def add_ocr_text(self, page: int, text: str) -> None:
        """收下一页 OCR 结果：该页没有文字层时，AI 用它代替（只在内存里，不改文件）。"""
        if not text.strip():
            return
        self._ocr[page] = text
        self._ocr_page_count = self._controller.page_count
        self._texts = None

    def _page_texts(self) -> list[str]:
        if self._texts is None or len(self._texts) != self._controller.page_count:
            texts = self._controller.page_texts()
            for page, text in self._ocr.items():
                if 0 <= page < len(texts) and len(texts[page].strip()) < _SCANNED_PAGE_CHARS:
                    texts[page] = text
            self._texts = texts
        return self._texts

    # ------------------------------------------------------------------ 动作入口
    def translate(self, text: str, page: int | None = None, rect: Rect | None = None) -> None:
        text = text.strip()
        if not text:
            self._notify("请先选中要翻译的文字")
            return
        self._set_anchor(page, rect)
        language = self._settings.target_language
        self._start(f"翻译 → {language}", text, prompts.translate_messages(text, language))

    def explain(self, text: str, page: int | None = None, rect: Rect | None = None) -> None:
        text = text.strip()
        if not text:
            self._notify("请先选中要解释的文字")
            return
        self._set_anchor(page, rect)
        surrounding = ""
        if page is not None and self._controller.has_document:
            top = max(0.0, rect.y0 - 80) if rect is not None else None
            surrounding = self._controller.text_excerpt(page, top, max_chars=900)
        messages = prompts.explain_messages(text, surrounding, self._settings.target_language)
        self._start("解释", text, messages)

    def run_action(self, key: str) -> None:
        if key == "summary":
            self.summarize()
        elif key == "suggest":
            self.suggest()
        elif key == "capture":
            self._notify("在页面上拖框选取公式或图表")
        else:
            preset = prompts.preset_by_key(key)
            if preset is not None:
                self.ask(preset.question, title=preset.title)

    def summarize(self) -> None:
        pages = self._doc_pages()
        if pages is None:
            return
        context, truncated = overview_context(pages)
        messages = prompts.summary_messages(
            context, self._settings.target_language, truncated=truncated
        )
        self._note_anchor = None
        self._start("总结全文", "", messages)

    def suggest(self) -> None:
        pages = self._doc_pages()
        if pages is None:
            return
        context, _ = overview_context(pages, budget=12000)
        messages = prompts.suggest_messages(context, self._settings.target_language)
        self._start("推荐问题", "", messages, kind="suggest")

    def ask(self, question: str, *, title: str | None = None) -> None:
        question = question.strip()
        if not question:
            return
        pages = self._doc_pages()
        if pages is None:
            return
        context, _ = question_context(pages, question)
        messages = prompts.ask_messages(
            question, context, self._settings.target_language, self._history
        )
        self._start(title or "提问", question, messages, kind="ask", question=question)

    def explain_area(self, page: int, rect: Rect) -> None:
        """框选区域：模型支持图片就发截图，否则退回区域内的文字。"""
        if not self._controller.has_document:
            return
        self._set_anchor(page, rect)
        provider = self._settings.active()
        hint = self._controller.text_in_rect(page, rect)
        language = self._settings.target_language
        quote = f"第 {page + 1} 页框选区域"
        if provider is not None and provider.vision:
            png = self._capture_png(page, rect)
            if png:
                encoded = base64.b64encode(png).decode("ascii")
                self._start(
                    "解释图表 / 公式",
                    quote,
                    prompts.explain_image_messages(encoded, hint, language),
                )
                return
        if not hint.strip():
            self._show_panel()
            self._notify(
                "框选区域没有可提取的文字；请在模型设置里勾选「支持图片输入」并使用视觉模型"
            )
            return
        surrounding = self._controller.text_excerpt(page, max(0.0, rect.y0 - 80), max_chars=900)
        self._start("解释", hint, prompts.explain_messages(hint, surrounding, language))

    def stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._retire(worker)
        self._panel.cancel_turn()

    def clear(self) -> None:
        self.stop()
        self._history.clear()
        self._panel.clear()

    def save_note(self, text: str) -> None:
        """把回答存为便签批注：锚在选区 / 框选处，否则当前页左上角。"""
        if not text.strip() or not self._controller.has_document:
            return
        if self._note_anchor is not None and self._note_anchor[0] < self._controller.page_count:
            page, x, y = self._note_anchor
        else:
            page, x, y = self._current_page(), 36.0, 36.0
        bounds = self._controller.get_page_bounds()
        if page < len(bounds):
            box = bounds[page]
            x = min(max(x, box.x0 + 4), box.x1 - 24)
            y = min(max(y, box.y0 + 4), box.y1 - 24)
        provider = self._settings.active()
        model = provider.model if provider is not None else "AI"
        content = f"【AI · {model}】\n{text.strip()}"
        if self._controller.add_text_annot(page, Point(x, y), content):
            self._on_annotation_added(page)
            self._notify(f"已存为第 {page + 1} 页的便签")
        else:
            self._notify(f"保存便签失败：{self._controller.last_error or '未知原因'}")

    def shutdown(self) -> None:
        """退出前：取消并等待所有请求线程。"""
        for worker in self.workers():
            worker.shutdown()
        for worker in self.workers():
            worker.wait(5000)

    # ------------------------------------------------------------------ 内部：发送前检查
    def _doc_pages(self) -> list[str] | None:
        if not self._controller.has_document:
            self._notify("请先打开文档")
            return None
        pages = self._page_texts()
        if not any(p.strip() for p in pages):
            self._show_panel()
            if self._start_ocr is None:
                self._notify("文档没有可提取的文字（像是扫描件，可先用「工具 → OCR」识别）")
                return None
            ok, _ = self.confirm(
                "没有可读的文字",
                "这份文档没有文字层（像是扫描件）。现在用 OCR 识别全部页吗？\n\n"
                "识别在本机进行；结果直接交给 AI 助读使用，不会改动文件。识别完成后请再提问一次。",
            )
            if ok:
                self._start_ocr()
            return None
        return pages

    def _set_anchor(self, page: int | None, rect: Rect | None) -> None:
        if page is None:
            self._note_anchor = None
        elif rect is None:
            self._note_anchor = (page, 36.0, 36.0)
        else:
            self._note_anchor = (page, rect.x1 + 4, rect.y0)

    def confirm(self, title: str, text: str, *, remember: str | None = None) -> tuple[bool, bool]:
        """询问用户；返回（是否继续, 是否勾选了「不再询问」）。测试可替换此方法。"""
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("继续")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        check: QCheckBox | None = None
        if remember:
            check = QCheckBox(remember)
            box.setCheckBox(check)
        ok = box.exec() == QMessageBox.StandardButton.Yes
        return ok, bool(check is not None and check.isChecked())

    def _ensure_ready(self) -> ProviderConfig | None:
        if not self.is_ready():
            self._show_panel()
            ok, _ = self.confirm(
                "AI 助读未配置",
                "AI 助读尚未启用或缺少模型 / API Key。现在打开「模型设置」吗？",
            )
            if not ok or not self.open_settings() or not self.is_ready():
                return None
        return self._settings.active()

    def _allowed(self, provider: ProviderConfig, size: int) -> bool:
        if not provider.is_local and provider.id not in self._settings.consented:
            ok, remember = self.confirm(
                "发送到 AI 服务",
                f"将把所选内容发送到「{provider.name}」（{provider.host}）处理。\n"
                "内容会离开本机，请确认该服务的隐私政策可以接受。",
                remember="以后向该服务发送时不再询问",
            )
            if not ok:
                return False
            if remember:
                self._settings.consented.append(provider.id)
                self.apply_settings(self._settings)
        if self._controller.is_encrypted and not self._encrypted_ok and not provider.is_local:
            ok, _ = self.confirm(
                "加密文档", "这是一份加密（受密码保护）的 PDF。仍要把其中的内容发送给 AI 服务吗？"
            )
            if not ok:
                return False
            self._encrypted_ok = True
        if size > LARGE_SEND_CHARS:
            ok, _ = self.confirm(
                "内容较多",
                f"这次将发送约 {size:,} 个字符，可能消耗较多额度、等待较久。继续吗？",
            )
            if not ok:
                return False
        return True

    # ------------------------------------------------------------------ 内部：请求
    def _start(
        self,
        title: str,
        quote: str,
        messages: list[Message],
        *,
        kind: str = "chat",
        question: str = "",
    ) -> None:
        provider = self._ensure_ready()
        if provider is None:
            return
        if not self._allowed(provider, _message_chars(messages)):
            return
        if self._worker is not None:
            self.stop()
        client = self._client_factory(provider.base_url, self._key(provider))
        worker = AiWorker(self)
        worker.setup_chat(client, messages, model=provider.model, temperature=provider.temperature)
        worker.delta.connect(self._on_delta)
        worker.reasoning.connect(self._on_reasoning)
        worker.finished_text.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._on_thread_finished)
        self._worker = worker
        self._kind = kind
        self._question = question
        self._show_panel()
        self._panel.set_page_count(self._controller.page_count)
        self._panel.begin_turn(title, quote)
        worker.start()

    def _retire(self, worker: AiWorker) -> None:
        worker.shutdown()
        if worker is self._worker:
            self._worker = None
        if worker.isRunning():
            self._retired.append(worker)
        else:
            worker.deleteLater()

    def _is_current(self) -> bool:
        return self._worker is not None and self.sender() is self._worker

    def _on_delta(self, text: str) -> None:
        if self._is_current():
            self._panel.append_delta(text)

    def _on_reasoning(self, text: str) -> None:
        if self._is_current():
            self._panel.append_reasoning(text)

    def _on_finished(self, text: str) -> None:
        if not self._is_current():
            return
        self._worker = None
        if self._kind == "suggest":
            questions = prompts.parse_questions(text)
            if questions:
                self._panel.finish_turn("", questions=questions)
            else:
                self._panel.finish_turn(text)
            return
        self._panel.finish_turn(text)
        if self._kind == "ask" and text.strip():
            self._history.append({"role": "user", "content": self._question})
            self._history.append({"role": "assistant", "content": text})
            del self._history[: -prompts.MAX_HISTORY_MESSAGES]

    def _on_failed(self, message: str) -> None:
        if self._is_current():
            self._worker = None
            self._panel.fail_turn(message)

    def _on_cancelled(self) -> None:
        if self._is_current():
            self._worker = None
            self._panel.cancel_turn()

    def _on_thread_finished(self) -> None:
        worker = self.sender()
        if not isinstance(worker, AiWorker):
            return
        if worker in self._retired:
            self._retired.remove(worker)
        if worker is self._worker:  # 线程结束却没发任何结果信号（理论上不会）
            self._worker = None
            if self._panel.busy:
                self._panel.fail_turn("请求意外结束")
        worker.deleteLater()

    # ------------------------------------------------------------------ 内部：截图
    def _capture_png(self, page: int, rect: Rect) -> bytes:
        from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

        side = max(rect.width, rect.height, 1.0)
        zoom = min(_CAPTURE_MAX_ZOOM, max(1.0, _CAPTURE_MAX_SIDE / side))
        try:
            data = self._controller.render_page(page, zoom)
        except (RuntimeError, IndexError):
            return b""
        pixmap = pixmap_to_qpixmap(data)
        bounds = self._controller.get_page_bounds()
        origin_x = bounds[page].x0 if page < len(bounds) else 0.0
        origin_y = bounds[page].y0 if page < len(bounds) else 0.0
        area = QRect(
            int((rect.x0 - origin_x) * zoom),
            int((rect.y0 - origin_y) * zoom),
            max(1, int(rect.width * zoom)),
            max(1, int(rect.height * zoom)),
        ).intersected(pixmap.rect())
        if area.isEmpty():
            return b""
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.copy(area).save(buffer, "PNG")
        return bytes(QByteArray(buffer.data()).data())


def _message_chars(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(p.get("text", ""))) for p in content if isinstance(p, dict))
    return total
