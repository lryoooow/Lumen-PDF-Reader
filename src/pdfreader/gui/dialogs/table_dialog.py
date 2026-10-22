"""当前内存页面的表格提取与显式导出。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import DocumentController
from pdfreader.engines.types import TableData
from pdfreader.gui.ai_assistant import AiAssistant
from pdfreader.services.notebook import save_markdown
from pdfreader.services.tables import table_csv, table_markdown

_logger = logging.getLogger("pdfreader")


class TableDialog(QDialog):
    def __init__(
        self,
        controller: DocumentController,
        assistant: AiAssistant,
        current_page: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("表格提取")
        self.resize(900, 650)
        self._controller = controller
        self._assistant = assistant
        self._tables: tuple[TableData, ...] = ()
        root = QVBoxLayout(self)
        line = QHBoxLayout()
        self.page = QSpinBox()
        self.page.setRange(1, max(1, controller.page_count))
        self.page.setValue(current_page + 1)
        extract = QPushButton("提取所选页")
        extract.clicked.connect(self.extract)
        self.tables = QComboBox()
        self.tables.currentIndexChanged.connect(self._show_table)
        for widget in (QLabel("页码"), self.page, extract, self.tables):
            line.addWidget(widget)
        root.addLayout(line)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self.table)
        buttons = QHBoxLayout()
        self._exports: list[QPushButton] = []
        for title, handler in (
            ("导出 Markdown", self.markdown),
            ("导出 CSV", self.csv),
            ("送到 AI 面板解释", self.explain),
        ):
            button = QPushButton(title)
            button.clicked.connect(handler)
            button.setEnabled(False)
            self._exports.append(button)
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.status = QLabel("表格识别可能误判合并单元格，请对照原文核对。扫描件需先 OCR。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        controller.documentChanged.connect(self.invalidate)
        controller.documentClosed.connect(self.reject)

    def invalidate(self) -> None:
        self._tables = ()
        self.tables.clear()
        self.table.setRowCount(0)
        for button in self._exports:
            button.setEnabled(False)
        self.status.setText("文档已变化，请重新提取。")

    def extract(self) -> None:
        self._tables = self._controller.extract_tables(self.page.value() - 1)
        self.tables.clear()
        self.tables.addItems([f"表格 {i + 1}" for i in range(len(self._tables))])
        for button in self._exports:
            button.setEnabled(bool(self._tables))
        self._show_table(0)
        self.status.setText(
            f"识别到 {len(self._tables)} 个表格，请核对单元格。"
            if self._tables
            else (
                self._controller.last_error or "未识别到表格；扫描页可先 OCR，复杂表格可框选提问。"
            )
        )

    def _show_table(self, index: int) -> None:
        self.table.clear()
        self.table.setRowCount(0)
        if not 0 <= index < len(self._tables):
            return
        table = self._tables[index]
        self.table.setRowCount(len(table.rows))
        self.table.setColumnCount(max((len(row) for row in table.rows), default=0))
        for r, row in enumerate(table.rows):
            for c, text in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def _selected(self) -> TableData | None:
        index = self.tables.currentIndex()
        return self._tables[index] if 0 <= index < len(self._tables) else None

    def _export(self, extension: str) -> None:
        table = self._selected()
        if table is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出表格", "table." + extension, f"表格 (*.{extension})"
        )
        if not filename:
            return
        if (
            self._controller.is_encrypted
            and QMessageBox.question(
                self, "导出明文表格", "导出文件不继承 PDF 密码，仍要保存明文表格吗？"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            save_markdown(
                Path(filename),
                "\ufeff" + table_csv(table) if extension == "csv" else table_markdown(table),
            )
        except OSError:
            _logger.exception("表格导出失败")
            self.status.setText("导出失败，请检查路径和权限。")
            return
        self.status.setText("表格已导出，请核对识别结果。")

    def markdown(self) -> None:
        self._export("md")

    def csv(self) -> None:
        self._export("csv")

    def explain(self) -> None:
        table = self._selected()
        if table is not None:
            self._assistant.explain(table_markdown(table), table.page_num, table.rect)
            if self._assistant.busy:
                self.accept()
