"""长文与多文档研究：先抽取和预算预览，再明确发起网络工作流。"""

from __future__ import annotations

import logging
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import DocumentController
from pdfreader.gui.ai_assistant import AiAssistant
from pdfreader.services.ai.research import (
    Evidence,
    ResearchChunk,
    ResearchDocument,
    batch_context,
    check_evidence,
    request_bound,
    research_chunks,
    retrieve_chunks,
    workflow_key,
)
from pdfreader.services.ai.semantic import embedding_requests
from pdfreader.workers.research_worker import ResearchWorker

_logger = logging.getLogger("pdfreader")
_TASKS = (
    "按章节总结全文：研究问题、方法、数据、主要结果、局限。说明材料覆盖范围。",
    "生成跨文献对比表：方法、数据集、结论、局限；逐篇列明，并列出证据引用矩阵。",
    "撰写可核对的综述草稿：主题、共识、分歧、证据不足和后续研究。每条事实需原文短引。",
    "仅依据检索到的片段回答问题；若证据不足，明确说明。问题：",
)


class ResearchDialog(QDialog):
    def __init__(
        self,
        sources: Sequence[tuple[str, DocumentController]],
        assistant: AiAssistant,
        navigate: Callable[[int, int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("长文与多文档研究")
        self.resize(1000, 800)
        self._sources = list(sources)
        self._assistant = assistant
        self._navigate = navigate
        self._documents: list[ResearchDocument] = []
        self._buffers: dict[int, list[str]] = {}
        self._queue: deque[tuple[int, int]] = deque()
        self._characters = 0
        self._batches: list[str] = []
        self._chunks: list[ResearchChunk] = []
        self._evidence: list[Evidence] = []
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_key = ""
        self._worker: ResearchWorker | None = None
        self._running = False
        self._closing = False
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._extract)
        root = QVBoxLayout(self)
        self.documents = QListWidget()
        self.documents.setMaximumHeight(110)
        for title, controller in sources:
            item = QListWidgetItem(title)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.documents.addItem(item)
            controller.documentClosed.connect(self.invalidate)
            controller.documentChanged.connect(self.invalidate)
        root.addWidget(self.documents)
        line = QHBoxLayout()
        self.task = QComboBox()
        self.task.addItems(
            ["分层全文摘要", "多文档对比与引用矩阵", "综述草稿", "章节与图注检索问答"]
        )
        self.query = QLineEdit()
        self.query.setPlaceholderText("检索问题（问答模式必填）")
        self.query.setMaxLength(2000)
        self.embedding_model = QLineEdit()
        self.embedding_model.setPlaceholderText("可选向量模型（仅问答；当前服务需支持 embeddings）")
        self.embedding_model.setMaxLength(200)
        self.first_page = QSpinBox()
        self.last_page = QSpinBox()
        for spin in (self.first_page, self.last_page):
            spin.setRange(1, 100000)
        self.last_page.setValue(
            max((controller.page_count for _, controller in sources), default=1)
        )
        self.budget = QSpinBox()
        self.budget.setRange(2, 200)
        self.budget.setValue(40)
        for widget in (
            self.task,
            self.query,
            QLabel("页范围"),
            self.first_page,
            self.last_page,
            QLabel("请求上限"),
            self.budget,
        ):
            line.addWidget(widget)
        root.addLayout(line)
        root.addWidget(self.embedding_model)
        controls = QHBoxLayout()
        self.preview_btn = QPushButton("提取并预览预算（不联网）")
        self.preview_btn.clicked.connect(self.prepare)
        self.run_btn = QPushButton("确认后运行")
        self.run_btn.clicked.connect(self.run)
        stop = QPushButton("停止")
        stop.clicked.connect(self.stop)
        for widget in (self.preview_btn, self.run_btn, stop):
            controls.addWidget(widget)
        root.addLayout(controls)
        self.answer = QTextBrowser()
        self.answer.setOpenLinks(False)
        self.answer.setOpenExternalLinks(False)
        root.addWidget(self.answer)
        self.evidence = QListWidget()
        self.evidence.setMaximumHeight(180)
        self.evidence.itemDoubleClicked.connect(self.locate)
        root.addWidget(self.evidence)
        self.status = QLabel(
            "只处理勾选文档与页范围。原文短引核对不等于结论语义验证；未找到依据会单独列出。"
        )
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.documents.itemChanged.connect(self.invalidate)
        self.task.currentIndexChanged.connect(self.invalidate)
        self.query.textChanged.connect(self.invalidate)
        self.embedding_model.textChanged.connect(self.invalidate)
        self.first_page.valueChanged.connect(self.invalidate)
        self.last_page.valueChanged.connect(self.invalidate)
        self._controls()

    def _controls(self) -> None:
        busy = self._timer.isActive() or self._worker is not None
        for widget in (
            self.documents,
            self.task,
            self.query,
            self.embedding_model,
            self.first_page,
            self.last_page,
            self.preview_btn,
        ):
            widget.setEnabled(not busy)
        self.run_btn.setEnabled(not busy and bool(self._batches))

    def stop(self) -> None:
        self._timer.stop()
        self._queue.clear()
        self._running = False
        if self._worker is not None:
            self._worker.shutdown()
        self.status.setText("已停止；未生成完整结果的任务不会标为完成。")
        self._controls()

    def invalidate(self) -> None:
        self.stop()
        self._buffers.clear()
        self._documents.clear()
        self._batches.clear()
        self._chunks.clear()
        self._evidence.clear()
        self.evidence.clear()
        self._controls()

    def prepare(self) -> None:
        self.invalidate()
        self._characters = 0
        if self.first_page.value() > self.last_page.value():
            self.status.setText("页范围无效。")
            return
        for index, (_title, controller) in enumerate(self._sources):
            item = self.documents.item(index)
            if (
                item is None
                or item.checkState() != Qt.CheckState.Checked
                or not controller.has_document
            ):
                continue
            self._buffers[index] = [""] * controller.page_count
            self._queue.extend(
                (index, page)
                for page in range(
                    self.first_page.value() - 1, min(self.last_page.value(), controller.page_count)
                )
            )
        if not self._queue:
            self.status.setText("所选文档没有有效页面。")
            return
        self._timer.start()
        self._controls()

    def _extract(self) -> None:
        if self._queue:
            index, page = self._queue.popleft()
            controller = self._sources[index][1]
            try:
                self._buffers[index][page] = controller.page_text(page)
                self._characters += len(self._buffers[index][page])
            except Exception:
                _logger.exception("研究任务提取文字失败")
                self.invalidate()
                self.status.setText("读取文字失败，未发起网络请求。")
                return
            if self._characters > 8_000_000:
                self.invalidate()
                self.status.setText("超过本次 800 万字符内存上限，请缩小页范围。")
                return
            self.status.setText(
                f"正在读取文档 {index + 1} 第 {page + 1} 页，剩余 {len(self._queue)} 页。"
            )
            return
        self._timer.stop()
        self._documents = [
            ResearchDocument(
                index + 1,
                self._sources[index][0],
                tuple(pages),
                tuple(
                    (entry.page_num, entry.title)
                    for entry in self._sources[index][1].get_toc()
                    if entry.page_num >= 0
                ),
            )
            for index, pages in self._buffers.items()
        ]
        chunks = research_chunks(self._documents)
        if self.task.currentIndex() == 3:
            if not self.query.text().strip():
                self.status.setText("请填写检索问题后重新预览。")
                self._controls()
                return
            if not self.embedding_model.text().strip():
                chunks = retrieve_chunks(chunks, self.query.text())
            elif len(chunks) > 512:
                self.status.setText("向量检索最多处理 512 个分片，请缩小范围。")
                self._controls()
                return
        self._chunks = chunks
        self._batches = batch_context([chunk.render() for chunk in chunks])
        size = sum(len(batch) for batch in self._batches)
        count = self._request_estimate()
        self.status.setText(
            f"{len(self._documents)} 篇文档，{size:,} 个上下文字符，"
            f"预计最多 {count} 次请求；按字符 / 4 粗估输入约 {size // 4:,} token，"
            "另有提示词、归并和输出开销，以服务账单为准。"
        )
        if not self._batches:
            self.status.setText("未找到可发送的相关文字；扫描件需先 OCR。")
        self._controls()

    def _request_estimate(self) -> int:
        if self.task.currentIndex() == 3 and self.embedding_model.text().strip():
            return embedding_requests(len(self._chunks)) + request_bound(min(12, len(self._chunks)))
        return request_bound(len(self._batches))

    def run(self) -> None:
        if not self._batches or self._worker is not None:
            return
        if self._request_estimate() > self.budget.value():
            self.status.setText("预计请求次数超过上限，请缩小范围或调整预算。")
            return
        size = sum(len(batch) for batch in self._batches)
        provider = self._assistant.authorize_request(size)
        if provider is None:
            return
        encrypted = any(controller.is_encrypted for _title, controller in self._sources)
        if (
            encrypted
            and not provider.is_local
            and QMessageBox.question(
                self, "研究任务包含加密文档", "选定内容可能来自加密文档。仍要发送到当前 AI 服务吗？"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        task = (
            _TASKS[self.task.currentIndex()]
            + "\n"
            + self.query.text()
            + "\n用"
            + self._assistant.settings.target_language
            + "回答。"
        )
        embedding_model = (
            self.embedding_model.text().strip() if self.task.currentIndex() == 3 else ""
        )
        self._cache_key = workflow_key(
            self._documents, task, provider.model + "|" + embedding_model, provider.base_url
        )
        cached = self._cache.get(self._cache_key)
        if cached is not None:
            self._running = True
            self._result(cached)
            return
        try:
            client = self._assistant.client_factory(
                provider.base_url, self._assistant.keystore.get(provider.id)
            )
        except Exception:
            _logger.exception("研究任务配置读取失败")
            self.status.setText("读取模型凭据失败，请检查模型设置。")
            return
        self._worker = ResearchWorker(
            client,
            model=provider.model,
            batches=list(self._batches),
            task=task,
            max_requests=self.budget.value(),
            chunks=list(self._chunks),
            embedding_model=embedding_model,
            query=self.query.text(),
        )
        self._worker.progress.connect(self.status.setText)
        self._worker.failed.connect(self._failed)
        self._worker.result.connect(self._result)
        self._worker.finished.connect(self._finished)
        self._running = True
        self._controls()
        self._worker.start()

    def _result(self, answer: str) -> None:
        if not self._running:
            return
        self._cache[self._cache_key] = answer
        self._cache.move_to_end(self._cache_key)
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        self.answer.setMarkdown(answer)
        self._evidence = check_evidence(answer, self._documents)
        self.evidence.clear()
        self.evidence.addItems(
            [
                ("原文可定位" if item.found else "未在原文找到依据")
                + f" · 文档 {item.document} 第 {item.page + 1} 页 · {item.quote}"
                for item in self._evidence
            ]
        )
        self.status.setText(
            "已生成。双击可定位的短引回原文；引文存在不代表结论正确，请核对语义。"
            if self._evidence
            else "已生成，但未提供可核对的原文短引，结果需要人工核查。"
        )
        self._running = False

    def _failed(self, message: str) -> None:
        if self._running:
            self.status.setText("任务失败，未标记完成：" + message)
        self._running = False

    def _finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._controls()
        if self._closing:
            self.close()

    def locate(self) -> None:
        row = self.evidence.currentRow()
        if 0 <= row < len(self._evidence):
            item = self._evidence[row]
            if item.found:
                self._navigate(item.document - 1, item.page)

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
