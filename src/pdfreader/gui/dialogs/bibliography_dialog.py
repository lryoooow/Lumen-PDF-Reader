"""书目元数据、格式交换与可编辑引用预览。"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pdfreader.services.bibliography import (
    Reference,
    export_bibtex,
    export_csl_json,
    export_ris,
    format_citation,
    import_bibtex,
    import_ris,
)
from pdfreader.services.notebook import save_markdown
from pdfreader.workers.metadata_worker import MetadataWorker

_logger = logging.getLogger("pdfreader")


class BibliographyDialog(QDialog):
    def __init__(self, reference: Reference, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("书目与引用")
        self.resize(850, 700)
        self._references = [reference]
        self._worker: MetadataWorker | None = None
        self._closing = False
        self._loading = False
        self._lookup_target: Reference | None = None
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        for title, action in (
            ("导入 BibTeX / RIS", self.load),
            ("导出 BibTeX", self.bibtex),
            ("导出 RIS", self.ris),
            ("导出 CSL JSON", self.csl),
        ):
            button = QPushButton(title)
            button.clicked.connect(action)
            top.addWidget(button)
        root.addLayout(top)
        self.items = QListWidget()
        self.items.setMaximumHeight(100)
        self.items.currentRowChanged.connect(self._selected)
        root.addWidget(self.items)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for key, title in (
            ("title", "标题"),
            ("authors", "作者（姓, 名；作者间分号）"),
            ("year", "年份"),
            ("doi", "DOI"),
            ("journal", "期刊"),
            ("volume", "卷"),
            ("issue", "期"),
            ("pages", "起止页"),
        ):
            edit = QLineEdit()
            edit.textChanged.connect(self._changed)
            self.fields[key] = edit
            form.addRow(title, edit)
        root.addLayout(form)
        line = QHBoxLayout()
        self.lookup_btn = QPushButton("用 DOI 查询 Crossref（只发送 DOI）")
        self.lookup_btn.clicked.connect(self.lookup)
        self.styles = QComboBox()
        self.styles.addItems(["GB/T 7714-2015", "APA 7", "BibTeX"])
        self.styles.currentTextChanged.connect(self._preview)
        self.page = QSpinBox()
        self.page.setRange(0, 100000)
        self.page.setSpecialValueText("不附页码")
        self.page.valueChanged.connect(self._preview)
        copy = QPushButton("复制引用")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.citation()))
        for widget in (self.lookup_btn, self.styles, QLabel("引文页码"), self.page, copy):
            line.addWidget(widget)
        root.addLayout(line)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        root.addWidget(self.preview)
        self.status = QLabel("引用使用常用期刊论文模板，提交前请按目标规范核对；元数据可编辑。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        done = QPushButton("使用当前引用")
        done.clicked.connect(self.accept)
        root.addWidget(done)
        self._reload_list()

    def _reload_list(self) -> None:
        self.items.blockSignals(True)
        self.items.clear()
        self.items.addItems([ref.title or ref.key for ref in self._references])
        self.items.blockSignals(False)
        self.items.setCurrentRow(0)

    def _selected(self, row: int) -> None:
        if not 0 <= row < len(self._references):
            return
        reference = self._references[row]
        self._loading = True
        for key, edit in self.fields.items():
            edit.setText(
                "; ".join(reference.authors) if key == "authors" else str(getattr(reference, key))
            )
        self._loading = False
        self._preview()

    def reference(self) -> Reference:
        original = self._references[max(0, self.items.currentRow())]
        return replace(
            original,
            title=self.fields["title"].text(),
            authors=tuple(a.strip() for a in self.fields["authors"].text().split(";") if a.strip()),
            year=self.fields["year"].text(),
            doi=self.fields["doi"].text(),
            journal=self.fields["journal"].text(),
            volume=self.fields["volume"].text(),
            issue=self.fields["issue"].text(),
            pages=self.fields["pages"].text(),
        )

    def _changed(self) -> None:
        if self._loading:
            return
        row = self.items.currentRow()
        if row >= 0:
            self._references[row] = self.reference()
        self._preview()

    def citation(self) -> str:
        return format_citation(
            self.reference(),
            self.styles.currentText(),
            self.page.value() - 1 if self.page.value() else None,
        )

    def _preview(self) -> None:
        if self._references:
            self.preview.setPlainText(self.citation())

    def load(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "导入书目", "", "书目 (*.bib *.ris)")
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > 4_000_000:
                raise ValueError("书目文件过大")
            text = path.read_text("utf-8-sig")
            references = import_ris(text) if path.suffix.lower() == ".ris" else import_bibtex(text)
        except Exception:
            _logger.exception("导入书目失败")
            self.status.setText("导入失败，请检查 UTF-8 编码和书目格式；原列表保留。")
            return
        self._references = references
        self._reload_list()

    def _export(self, extension: str, text: str) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出书目", "references." + extension, f"书目 (*.{extension})"
        )
        if filename:
            try:
                save_markdown(Path(filename), text)
                self.status.setText("书目已导出。")
            except OSError:
                _logger.exception("书目导出失败")
                self.status.setText("导出失败，请检查路径和权限。")

    def bibtex(self) -> None:
        self._export("bib", export_bibtex(self._references))

    def ris(self) -> None:
        self._export("ris", export_ris(self._references))

    def csl(self) -> None:
        self._export("json", export_csl_json(self._references))

    def lookup(self) -> None:
        if self._worker is not None:
            return
        self._lookup_target = self.reference()
        self._worker = MetadataWorker(self.fields["doi"].text())
        self._worker.received.connect(self._metadata)
        self._worker.failed.connect(self.status.setText)
        self._worker.finished.connect(self._finished)
        self.lookup_btn.setEnabled(False)
        self.status.setText("正在向 Crossref 查询 DOI 元数据…")
        self._worker.start()

    def _metadata(self, reference: object) -> None:
        if self.reference() != self._lookup_target:
            self.status.setText("条目已发生变化，未覆盖编辑内容。请重新查询 DOI。")
            return
        if isinstance(reference, Reference):
            self._references[self.items.currentRow()] = reference
            self._selected(self.items.currentRow())
            self.status.setText("已获取元数据，请核对后使用。")

    def _finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self.lookup_btn.setEnabled(True)
        if self._closing:
            self.close()

    def accept(self) -> None:
        if self._worker is None:
            super().accept()

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        if self._worker is not None:
            self.status.setText("等待元数据请求结束后关闭（最多约 10 秒）…")
            event.ignore()
            return
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
