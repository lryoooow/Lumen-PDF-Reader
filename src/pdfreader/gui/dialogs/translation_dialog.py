"""段落对照翻译：主线程逐页抽取、一次一个网络请求、仅会话内缓存。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import DocumentController
from pdfreader.gui.ai_assistant import AiAssistant
from pdfreader.gui.theme import get_theme
from pdfreader.services.ai import prompts
from pdfreader.services.ai.provider import ProviderConfig
from pdfreader.services.translation import (
    Paragraph,
    TranslationCache,
    paragraphs_from_words,
    quality_warnings,
    translation_key,
)
from pdfreader.workers.ai_worker import AiWorker


class TranslationDialog(QDialog):
    def __init__(
        self,
        controller: DocumentController,
        assistant: AiAssistant,
        navigate: Callable[[int, float | None], None],
        current_page: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("段落对照翻译")
        self.resize(1100, 740)
        self._controller = controller
        self._assistant = assistant
        self._navigate = navigate
        self._paragraphs: list[Paragraph] = []
        self._cache = TranslationCache()
        self._worker: AiWorker | None = None
        self._running = False
        self._closing = False
        self._extract_page = 0
        self._extract_end = 0
        self._row = -1
        self._key = ""
        self._terms: list[tuple[str, str]] = []
        self._provider: ProviderConfig | None = None
        self._language = ""
        self._queue: list[int] = []
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._extract_tick)
        root = QVBoxLayout(self)
        line = QHBoxLayout()
        self.first_page = QSpinBox()
        self.last_page = QSpinBox()
        for spin in (self.first_page, self.last_page):
            spin.setRange(1, max(1, controller.page_count))
        self.first_page.setValue(max(1, current_page + 1))
        self.last_page.setValue(max(1, current_page + 1))
        self.preview_btn = QPushButton("提取段落")
        self.preview_btn.clicked.connect(self.extract)
        self.start_btn = QPushButton("翻译所选 / 重试未完成")
        self.start_btn.clicked.connect(self.start_translation)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop)
        self.mode = QComboBox()
        self.mode.addItems(["原文与译文", "仅译文"])
        for widget in (
            QLabel("起始页"),
            self.first_page,
            QLabel("结束页"),
            self.last_page,
            self.preview_btn,
            self.start_btn,
            self.stop_btn,
            self.mode,
        ):
            line.addWidget(widget)
        root.addLayout(line)
        hint = QLabel(
            "先预览并勾选要发送的段落。公式/页眉/参考文献过滤为候选，可手动改选。"
            "双击行回原文；悬停译文查看原文。译文仅保存在本窗口内存中。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.mode.currentIndexChanged.connect(
            lambda index: self.table.setColumnHidden(1, index == 1)
        )
        self.table.setHorizontalHeaderLabels(["发送 / 页码", "原文", "译文 / 核对提示"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        for col in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._locate)
        root.addWidget(self.table)
        self.status = QLabel("提取不联网。费用由所选服务计价；本工具显示字符量，不猜测费用。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        controller.documentClosed.connect(self.close)
        controller.documentChanged.connect(self._invalidate)
        self._controls()

    def _controls(self) -> None:
        busy = self._running or self._timer.isActive() or self._worker is not None
        for widget in (self.preview_btn, self.first_page, self.last_page):
            widget.setEnabled(not busy)
        self.start_btn.setEnabled(not busy and bool(self._paragraphs))
        self.stop_btn.setEnabled(busy)

    def extract(self) -> None:
        if not self._controller.has_document or self.first_page.value() > self.last_page.value():
            self.status.setText("请选择有效的页范围。")
            return
        self._paragraphs.clear()
        self.table.setRowCount(0)
        self._extract_page = self.first_page.value() - 1
        self._extract_end = self.last_page.value()
        self._timer.start()
        self._controls()

    def _extract_tick(self) -> None:
        page = self._extract_page
        try:
            bounds = self._controller.get_page_bounds()[page]
            paragraphs = paragraphs_from_words(page, self._controller.get_words(page), bounds)
        except (RuntimeError, IndexError):
            self.stop()
            self.status.setText("提取失败，请重新打开文档后重试。")
            return
        in_references = any(p.skip_reason == "参考文献标题" for p in self._paragraphs)
        for paragraph in paragraphs:
            if paragraph.skip_reason == "参考文献标题":
                in_references = True
            elif in_references and not paragraph.skip_reason:
                paragraph = replace(paragraph, skip_reason="参考文献候选")
            row = len(self._paragraphs)
            self._paragraphs.append(paragraph)
            self.table.insertRow(row)
            selection = QTableWidgetItem(f"第 {page + 1} 页")
            selection.setFlags(selection.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            selection.setCheckState(
                Qt.CheckState.Unchecked if paragraph.skip_reason else Qt.CheckState.Checked
            )
            selection.setToolTip(paragraph.skip_reason)
            self.table.setItem(row, 0, selection)
            self.table.setItem(row, 1, QTableWidgetItem(paragraph.text))
            self.table.setItem(row, 2, QTableWidgetItem(paragraph.skip_reason or "待翻译"))
        self._extract_page += 1
        self.status.setText(f"已提取至第 {page + 1} 页，共 {len(self._paragraphs)} 段。")
        if self._extract_page >= self._extract_end:
            self._timer.stop()
            if not self._paragraphs:
                self.status.setText("所选页没有文字层，请先运行 OCR 并另存带文字层的 PDF。")
            self.table.resizeRowsToContents()
            self._controls()

    def start_translation(self) -> None:
        rows = [
            i
            for i in range(len(self._paragraphs))
            if (item := self.table.item(i, 0)) is not None
            and item.checkState() == Qt.CheckState.Checked
        ]
        if not rows:
            self.status.setText("请勾选至少一段。")
            return
        size = sum(len(self._paragraphs[i].text) for i in rows)
        self.status.setText(f"所选 {len(rows)} 段，约 {size:,} 字符；正在等待确认。")
        provider = self._assistant.authorize_request(size)
        if provider is None:
            self.status.setText("未发送。请检查配置或重新确认。")
            return
        self._provider = replace(provider)
        self._language = self._assistant.settings.target_language
        self._queue = rows
        self._running = True
        self._controls()
        self._next()

    def _next(self) -> None:
        if not self._running or self._worker is not None:
            return
        provider = self._provider
        if provider is None:
            return
        while self._queue:
            self._row = self._queue.pop(0)
            paragraph = self._paragraphs[self._row]
            self._terms = self._assistant.glossary_for(paragraph.text)
            self._key = translation_key(
                paragraph.text,
                endpoint=provider.base_url,
                model=provider.model,
                language=self._language,
                glossary=self._terms,
                temperature=provider.temperature,
            )
            cached = self._cache.get(self._key)
            if cached is not None:
                self._show_result(cached)
                continue
            try:
                key = self._assistant.keystore.get(provider.id)
                client = self._assistant.client_factory(provider.base_url, key)
            except Exception:
                self.status.setText("读取服务凭据失败，请检查模型设置。")
                self._running = False
                self._controls()
                return
            worker = AiWorker(self)
            worker.setup_chat(
                client,
                prompts.translate_messages(paragraph.text, self._language, self._terms),
                model=provider.model,
                temperature=provider.temperature,
            )
            worker.finished_text.connect(self._result)
            worker.failed.connect(self._failed)
            worker.finished.connect(self._thread_finished)
            self._worker = worker
            self._result_item().setText("翻译中…")
            worker.start()
            return
        self._running = False
        self.status.setText("所选段落处理完成。黄色提示需要对照原文核对。")
        self._controls()

    def _show_result(self, text: str) -> None:
        paragraph = self._paragraphs[self._row]
        warnings = quality_warnings(paragraph.text, text, self._terms)
        item = self._result_item()
        item.setText(text + ("\n\n⚠ " + "；".join(warnings) if warnings else ""))
        item.setToolTip(paragraph.text)
        item.setBackground(
            QColor(get_theme().palette.mark) if warnings else QColor(Qt.GlobalColor.transparent)
        )
        item.setForeground(
            QColor(Qt.GlobalColor.black) if warnings else QColor(get_theme().palette.text)
        )
        self.table.resizeRowToContents(self._row)

    def _result(self, text: str) -> None:
        if not self._running or self.sender() is not self._worker:
            return
        if not text.strip():
            self._failed("服务返回空译文，请重试。")
            return
        self._cache.put(self._key, text)
        self._show_result(text)
        self.status.setText(f"已完成一段，剩余 {len(self._queue)} 段。")

    def _failed(self, message: str) -> None:
        if not self._running:
            return
        self._result_item().setText(message)
        self.status.setText("翻译已暂停：" + message + " 已完成段落保留，可重试。")
        self._running = False

    def _thread_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._controls()
        if self._closing:
            self.close()
        else:
            self._next()

    def _result_item(self) -> QTableWidgetItem:
        item = self.table.item(self._row, 2)
        if not isinstance(item, QTableWidgetItem):
            raise RuntimeError("译文行已失效")
        return item

    def stop(self) -> None:
        self._timer.stop()
        self._running = False
        self._queue.clear()
        if self._worker is not None:
            self._worker.shutdown()
            item = self.table.item(self._row, 2)
            if item is not None:
                item.setText("已停止，可重试")
        self.status.setText("已停止；完成的译文仍保留在本窗口。")
        self._controls()

    def _invalidate(self) -> None:
        self.stop()
        self._cache.clear()
        self._paragraphs.clear()
        self.table.setRowCount(0)
        self.status.setText("文档已变化，已清除旧定位和缓存，请重新提取。")
        self._controls()

    def _locate(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._paragraphs):
            paragraph = self._paragraphs[row]
            self._navigate(paragraph.page, paragraph.rect.y0)

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.stop()
        if self._worker is not None:
            event.ignore()
            return
        self._cache.clear()
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
