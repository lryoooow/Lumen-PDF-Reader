"""当前文档高级搜索与本地库全文索引；结果逐页到达。"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pdfreader.config.settings import default_settings_path
from pdfreader.controllers import DocumentController
from pdfreader.engines.types import SearchHit
from pdfreader.services.research_library import LibraryHit, ResearchLibrary
from pdfreader.services.text_search import SearchOptions, TextSearch
from pdfreader.workers.library_worker import LibraryIndexWorker

_logger = logging.getLogger("pdfreader")


class ResearchSearchDialog(QDialog):
    def __init__(
        self,
        controller: DocumentController,
        navigate: Callable[[int, float | None], None],
        open_result: Callable[[str, int], None],
        parent: QWidget | None = None,
        *,
        database: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("高级搜索与全文索引")
        self.resize(900, 650)
        self._controller = controller
        self._navigate = navigate
        self._open_result = open_result
        self._database = database or default_settings_path().with_name("research.db")
        self._hits: list[SearchHit] = []
        self._library_hits: list[LibraryHit] = []
        self._search: TextSearch | None = None
        self._page = 0
        self._last = 0
        self._text_pages: list[str] = []
        self._indexing = False
        self._worker: LibraryIndexWorker | None = None
        self._closing = False
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._tick)
        root = QVBoxLayout(self)
        line = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("关键词或正则表达式")
        self.query.returnPressed.connect(self.search)
        self.scope = QComboBox()
        self.scope.addItems(["当前文档", "文献库全文"])
        self.case = QCheckBox("区分大小写")
        self.whole = QCheckBox("整词")
        self.regex = QCheckBox("正则")
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self.search)
        for widget in (self.query, self.scope, self.case, self.whole, self.regex, self.search_btn):
            line.addWidget(widget)
        root.addLayout(line)
        options = QHBoxLayout()
        self.first_page = QSpinBox()
        self.last_page = QSpinBox()
        for spin in (self.first_page, self.last_page):
            spin.setRange(1, max(1, controller.page_count))
        self.last_page.setValue(max(1, controller.page_count))
        self.index_btn = QPushButton("更新当前文档的本地全文索引")
        self.index_btn.clicked.connect(self.index_document)
        self.cancel_btn = QPushButton("停止逐页读取")
        self.cancel_btn.clicked.connect(self.stop)
        for widget in (
            QLabel("起始页"),
            self.first_page,
            QLabel("结束页"),
            self.last_page,
            self.index_btn,
            self.cancel_btn,
        ):
            options.addWidget(widget)
        root.addLayout(options)
        labels = QHBoxLayout()
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("标签，逗号分隔")
        self.state = QComboBox()
        self.state.addItems(["未读", "在读", "已读"])
        self.rating = QSpinBox()
        self.rating.setRange(0, 5)
        save_labels = QPushButton("保存当前文档标签 / 状态 / 评分")
        save_labels.clicked.connect(self.save_labels)
        for widget in (self.tags, self.state, self.rating, save_labels):
            labels.addWidget(widget)
        root.addLayout(labels)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self.locate)
        root.addWidget(self.results)
        self.status = QLabel(
            "库索引是用户建立时的文字快照，编辑后需更新；加密文档不会写入明文索引。"
        )
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        controller.documentClosed.connect(self.stop)
        controller.documentChanged.connect(self.stop)

    def stop(self) -> None:
        self._timer.stop()
        self._text_pages.clear()
        if self._indexing:
            self._indexing = False
            self.status.setText("已停止读取，未提交新索引。")
        self._controls()

    def _controls(self) -> None:
        busy = self._worker is not None or self._indexing
        self.index_btn.setEnabled(not busy)
        self.search_btn.setEnabled(not busy)

    def search(self) -> None:
        self.stop()
        query = self.query.text().strip()
        if not query:
            return
        self.results.clear()
        self._hits.clear()
        self._library_hits.clear()
        if self.scope.currentIndex() == 1:
            if self.case.isChecked() or self.whole.isChecked() or self.regex.isChecked():
                self.status.setText("库全文搜索使用短语匹配；高级选项仅用于当前文档。")
                return
            library: ResearchLibrary | None = None
            try:
                library = ResearchLibrary(self._database)
                self._library_hits = library.search(query)
                for hit in self._library_hits:
                    self.results.addItem(f"{hit.title} · 第 {hit.page + 1} 页\n{hit.text}")
                self.status.setText(f"库内找到 {len(self._library_hits)} 页（最多显示 200 页）。")
            except (OSError, sqlite3.Error):
                _logger.exception("库搜索失败")
                self.status.setText("读取全文索引失败，请检查索引文件和权限。")
            finally:
                if library is not None:
                    library.close()
            return
        if not self._controller.has_document or self.first_page.value() > self.last_page.value():
            self.status.setText("没有文档或页范围无效。")
            return
        try:
            self._search = TextSearch(
                query,
                SearchOptions(
                    self.case.isChecked(), self.whole.isChecked(), self.regex.isChecked()
                ),
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._page = self.first_page.value() - 1
        self._last = self.last_page.value()
        self._timer.start()
        self.status.setText("正在逐页搜索；双击结果定位。")

    def _tick(self) -> None:
        page = self._page
        if not self._controller.has_document or page >= self._last:
            self._timer.stop()
            return
        if self._indexing:
            try:
                self._text_pages.append(self._controller.page_text(page))
            except (RuntimeError, IndexError):
                self.stop()
                self.status.setText("提取文字失败，未更新索引。")
                return
            if sum(len(text) for text in self._text_pages) > 16_000_000:
                self.stop()
                self.status.setText("超过单篇索引文字上限，未更新索引。")
                return
        elif self._search is not None:
            try:
                words = self._controller.get_words(page)
                hits = self._search.page_hits(page, words, limit=max(1, 10000 - len(self._hits)))
            except (ValueError, RuntimeError, IndexError) as exc:
                self.stop()
                self.status.setText(f"搜索停止：{exc}")
                return
            self._hits.extend(hits)
            for hit in hits:
                self.results.addItem(f"第 {page + 1} 页 · {hit.text}")
            self.status.setText(
                f"已搜索至第 {page + 1} 页，找到 {len(self._hits)} 处。"
                + ("本页没有文字，可先 OCR。" if not words else "")
            )
            if len(self._hits) >= 10000:
                self.stop()
                self.status.setText("达到 10000 处上限，请缩小范围。")
                return
        self._page += 1
        if self._page >= self._last:
            self._timer.stop()
            if self._indexing:
                self._commit_index()

    def index_document(self) -> None:
        if self._controller.is_encrypted:
            self.status.setText("加密文档不建立明文索引。")
            return
        if not self._controller.has_document:
            return
        if (
            QMessageBox.question(
                self,
                "建立本地全文索引",
                "将当前文档的文字快照保存到本机索引数据库，便于跨文档搜索。确认保存？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.stop()
        self._indexing = True
        self._page = 0
        self._last = self._controller.page_count
        self._timer.start()
        self._controls()

    def _commit_index(self) -> None:
        document = self._controller.document
        if document is None:
            self.stop()
            return
        self._worker = LibraryIndexWorker(
            self._database, str(document.path), document.title, self._text_pages
        )
        self._text_pages = []
        self._worker.succeeded.connect(
            lambda: self.status.setText("索引已更新，可切换到文献库全文搜索。")
        )
        self._worker.failed.connect(self.status.setText)
        self._worker.finished.connect(self._index_finished)
        self._indexing = False
        self._worker.start()

    def _index_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._controls()
        if self._closing:
            self.close()

    def save_labels(self) -> None:
        document = self._controller.document
        if document is None:
            return
        library: ResearchLibrary | None = None
        try:
            library = ResearchLibrary(self._database)
            library.set_labels(
                str(document.path),
                tags=[t.strip() for t in self.tags.text().split(",") if t.strip()],
                state=self.state.currentText(),
                rating=self.rating.value(),
            )
            self.status.setText("标签、状态和评分已保存。")
        except (OSError, ValueError, sqlite3.Error):
            _logger.exception("保存文献标记失败")
            self.status.setText("保存失败，请先建立索引并检查标签内容。")
        finally:
            if library is not None:
                library.close()

    def locate(self) -> None:
        row = self.results.currentRow()
        if self.scope.currentIndex() == 1 and 0 <= row < len(self._library_hits):
            hit = self._library_hits[row]
            self._open_result(hit.path, hit.page)
        elif 0 <= row < len(self._hits):
            current = self._hits[row]
            self._navigate(current.page_num, current.rect.y0)

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.stop()
        if self._worker is not None:
            event.ignore()
            self.status.setText("等待索引事务完成后关闭…")
            return
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
