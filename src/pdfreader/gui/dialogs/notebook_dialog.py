"""Markdown 阅读笔记编辑器；显式保存/导出，来源失效可见。"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QTextCursor, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import DocumentController
from pdfreader.engines.types import Annotation
from pdfreader.services.bibliography import Reference
from pdfreader.services.materials import KIND_LABELS, MaterialStore
from pdfreader.services.notebook import (
    TEMPLATES,
    Backlinks,
    SourceAnchor,
    annotated_markdown,
    annotation_note,
    display_markdown,
    export_docx,
    linked_note_path,
    preview_markdown,
    resolve_sources,
    save_markdown,
    source_anchors,
    template,
)
from pdfreader.workers.notebook_worker import BacklinksWorker

_logger = logging.getLogger("pdfreader")


class _Sources(QListWidget):
    sourcePrepared = Signal(str)

    def mimeData(self, items: Sequence[QListWidgetItem]) -> QMimeData:
        data = QMimeData()
        text = "\n".join(str(item.data(Qt.ItemDataRole.UserRole)) for item in items)
        self.sourcePrepared.emit(text)
        data.setText(display_markdown(text))
        return data


class NotebookDialog(QDialog):
    def __init__(
        self,
        controller: DocumentController,
        navigate: Callable[[int, float | None], None],
        parent: QWidget | None = None,
        *,
        materials: MaterialStore | None = None,
        reference: Reference | None = None,
        on_reference: Callable[[Reference], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("阅读笔记")
        self.resize(1150, 760)
        self._controller = controller
        self._materials = materials
        self._reference = reference or Reference("paper", controller.title)
        self._on_reference = on_reference
        document = controller.document
        self._document_key = hashlib.sha256(
            os.path.normcase(str(document.path.resolve()) if document else "").encode()
        ).hexdigest()
        self._navigate = navigate
        self._path: Path | None = None
        self._annotations: list[Annotation] = []
        self._anchors: dict[str, SourceAnchor] = {}
        self._invalid_metadata = ""
        self._links_worker: BacklinksWorker | None = None
        self._close_approved = False
        self._page = 0
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._scan_tick)
        root = QVBoxLayout(self)
        line = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(list(TEMPLATES))
        line.addWidget(self.templates)
        for text, handler in (
            ("新建模板", self.new_template),
            ("打开笔记", self.open_note),
            ("保存 Markdown", self.save),
            ("导出 Word", self.word),
            ("导出 PDF", self.pdf),
            ("刷新来源", self.refresh_sources),
            ("插入素材", self.insert_material),
            ("插入引用", self.insert_citation),
            ("反向链接", self.backlinks),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            line.addWidget(button)
        root.addLayout(line)
        hint = QLabel(
            "双击或拖入批注到编辑器；[[笔记名]] 链接同目录笔记。"
            "笔记独立保存，关闭前提示未保存内容；加密 PDF 的导出会再次确认明文。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        split = QSplitter()
        self.sources = _Sources()
        self.sources.sourcePrepared.connect(self._remember_source)
        self.sources.setDragEnabled(True)
        self.sources.itemDoubleClicked.connect(self._insert_source)
        self.editor = QPlainTextEdit()
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.setOpenExternalLinks(False)
        self.preview.anchorClicked.connect(self._follow)
        for widget in (self.sources, self.editor, self.preview):
            split.addWidget(widget)
        split.setSizes([240, 420, 420])
        root.addWidget(split, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.editor.textChanged.connect(self._render)
        controller.documentChanged.connect(self.refresh_sources)
        controller.documentClosed.connect(self.close)
        self.editor.setPlainText(template(controller.title or "阅读笔记", "期刊论文"))
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.document().setModified(False)
        self.refresh_sources()

    def backlinks(self) -> None:
        if self._path is None:
            self.status.setText("先保存当前笔记，再查看同目录反向链接。")
            return
        if self._links_worker is not None:
            return
        self._links_worker = BacklinksWorker(self._path)
        self._links_worker.result.connect(self._backlinks_result)
        self._links_worker.failed.connect(self.status.setText)
        self._links_worker.finished.connect(self._backlinks_finished)
        self._links_worker.start()

    def _backlinks_result(self, result: object) -> None:
        if not isinstance(result, Backlinks) or self._close_approved:
            return
        self.status.setText(
            f"找到 {len(result.paths)} 篇反向链接；{len(result.failed)} 篇读取失败。"
            + ("仅扫描前 1000 篇。" if result.truncated else "")
        )
        if result.paths:
            labels = [path.name for path in result.paths]
            label, ok = QInputDialog.getItem(
                self, "引用本笔记的文件", "选择打开", labels, editable=False
            )
            if ok:
                try:
                    self.load_path(result.paths[labels.index(label)])
                except (OSError, UnicodeError):
                    _logger.exception("打开反向链接失败")
                    self.status.setText("反向链接打开失败，当前编辑内容仍保留。")

    def _backlinks_finished(self) -> None:
        if self._links_worker is not None:
            self._links_worker.deleteLater()
            self._links_worker = None
        if self._close_approved:
            self.close()

    def insert_material(self) -> None:
        if self._materials is None:
            self.status.setText("素材库不可用。")
            return
        try:
            materials = [item for kind in KIND_LABELS for item in self._materials.items(kind)]
        except Exception:
            _logger.exception("读取素材库失败")
            self.status.setText("素材库读取失败。")
            return
        if not materials:
            self.status.setText("素材库为空，可先保存选区或术语。")
            return
        labels = [f"{item.id} · {KIND_LABELS[item.kind]} · {item.text[:70]}" for item in materials]
        label, ok = QInputDialog.getItem(self, "插入素材", "选择素材", labels, editable=False)
        if ok:
            material = materials[labels.index(label)]
            self.editor.insertPlainText(
                f"\n[{KIND_LABELS[material.kind]} #{material.id}](material:{material.id})\n\n"
                + material.text
                + "\n\n"
                + material.note
                + "\n"
            )

    def insert_citation(self) -> None:
        from pdfreader.gui.dialogs.bibliography_dialog import BibliographyDialog

        dialog = BibliographyDialog(self._reference, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reference = dialog.reference()
            if self._on_reference is not None:
                self._on_reference(self._reference)
            self.editor.insertPlainText("\n" + dialog.citation() + "\n")
        dialog.deleteLater()

    def refresh_sources(self) -> None:
        self._timer.stop()
        self._page = 0
        self._annotations.clear()
        self.sources.clear()
        if self._controller.has_document:
            self._timer.start()

    def _scan_tick(self) -> None:
        if self._page >= self._controller.page_count:
            self._timer.stop()
            self._render()
            return
        items = self._controller.list_annotations(self._page)
        self._annotations.extend(items)
        for annotation in items:
            item = QListWidgetItem(
                f"第 {self._page + 1} 页 · {annotation.type_name}\n"
                + (annotation.marked_text or annotation.content)[:70]
            )
            item.setData(Qt.ItemDataRole.UserRole, annotation_note(annotation, self._document_key))
            self.sources.addItem(item)
        self._page += 1

    def _insert_source(self, item: QListWidgetItem) -> None:
        text = str(item.data(Qt.ItemDataRole.UserRole))
        self._remember_source(text)
        self.editor.insertPlainText(display_markdown(text))

    def _remember_source(self, text: str) -> None:
        self._anchors.update((anchor.fingerprint, anchor) for anchor in source_anchors(text))

    def _markdown(self) -> str:
        return self._invalid_metadata + annotated_markdown(
            self.editor.toPlainText(), list(self._anchors.values())
        )

    def _render(self) -> None:
        text = self.editor.toPlainText()
        self.preview.setMarkdown(preview_markdown(text))
        anchors = source_anchors(self._markdown())
        resolved = resolve_sources(anchors, self._annotations, self._document_key)
        missing = sum(value is None for value in resolved.values())
        if self._timer.isActive():
            self.status.setText(f"正在核对来源：第 {self._page} 页…")
        else:
            self.status.setText(
                f"来源 {len(anchors)} 处，失效或存在歧义 {missing} 处，"
                f"来源标记损坏 {self._invalid_metadata.count('<!-- lumen-source:')} 处。"
                + ("失效来源保留摘录，不自动跳转。" if missing else "")
            )

    def _follow(self, url: QUrl) -> None:
        if url.scheme() == "material" and self._materials is not None:
            try:
                material = self._materials.get(int(url.path()))
                if material is None:
                    self.status.setText("该素材已不存在，笔记中的摘录仍保留。")
                else:
                    QMessageBox.information(
                        self,
                        "素材来源",
                        material.text + "\n\n" + material.note + "\n\n" + material.source_label,
                    )
            except Exception:
                _logger.exception("读取笔记素材来源失败")
                self.status.setText("素材来源不可用，笔记中的摘录仍保留。")
            return
        if url.scheme() == "source":
            sources = resolve_sources(
                source_anchors(self._markdown()), self._annotations, self._document_key
            )
            annotation = sources.get(url.path())
            if annotation is None:
                self.status.setText("该来源已失效或有多个候选，摘录仍保留。")
                return
            self._navigate(annotation.page_num, annotation.rect.y0)
        elif url.scheme() == "note":
            if self._path is None:
                self.status.setText("先保存当前笔记，再打开同目录双链。")
                return
            try:
                target = linked_note_path(self._path.parent, unquote(url.path()))
                self.load_path(target)
            except (OSError, ValueError) as exc:
                self.status.setText(f"打开双链失败：{exc}")

    def _may_discard(self) -> bool:
        if not self.editor.document().isModified():
            return True
        answer = QMessageBox.question(
            self,
            "笔记尚未保存",
            "保存当前笔记后继续？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        return (
            self.save()
            if answer == QMessageBox.StandardButton.Save
            else answer == QMessageBox.StandardButton.Discard
        )

    def new_template(self) -> None:
        if self._may_discard():
            self._path = None
            self._anchors.clear()
            self._invalid_metadata = ""
            self.editor.setPlainText(
                template(self._controller.title or "阅读笔记", self.templates.currentText())
            )
            self.editor.document().setModified(True)

    def open_note(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "打开笔记", "", "Markdown (*.md)")
        if filename:
            try:
                self.load_path(Path(filename))
            except OSError:
                _logger.exception("打开笔记失败")
                self.status.setText("打开笔记失败，请检查文件和权限。")

    def load_path(self, path: Path) -> None:
        if not self._may_discard():
            return
        text = path.read_text("utf-8")
        self._path = path
        self._anchors = {anchor.fingerprint: anchor for anchor in source_anchors(text)}
        self._invalid_metadata = ""
        for line in text.splitlines():
            if line.startswith("<!-- lumen-source:") and not source_anchors(line):
                self._invalid_metadata += line + "\n"
        self.editor.setPlainText(display_markdown(text))
        self.editor.document().setModified(False)

    def _destination(self, label: str, extension: str) -> Path | None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            label,
            str(self._path or "阅读笔记") if extension == "md" else "阅读笔记." + extension,
            f"{extension.upper()} (*.{extension})",
        )
        if not filename:
            return None
        if (
            self._controller.is_encrypted
            and QMessageBox.question(
                self,
                "导出加密文档的笔记",
                "此文件将包含明文笔记和摘录，不继承 PDF 密码。确认保存？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return None
        result = Path(filename)
        return (
            result
            if result.suffix.lower() == "." + extension
            else result.with_suffix("." + extension)
        )

    def save(self) -> bool:
        target = self._destination("保存笔记", "md")
        if target is None:
            return False
        try:
            save_markdown(target, self._markdown())
        except (OSError, UnicodeError):
            _logger.exception("保存笔记失败")
            self.status.setText("保存失败，编辑内容仍保留，请检查路径和权限。")
            return False
        self._path = target
        self.editor.document().setModified(False)
        self.status.setText("笔记已保存。")
        return True

    def word(self) -> None:
        target = self._destination("导出笔记 Word", "docx")
        if target is None:
            return
        try:
            export_docx(target, self.editor.toPlainText())
        except Exception:
            _logger.exception("笔记 Word 导出失败")
            self.status.setText("Word 导出失败，编辑内容仍保留。")
            return
        self.status.setText("Word 已导出。")

    def pdf(self) -> None:
        target = self._destination("导出笔记 PDF", "pdf")
        if target is None:
            return
        temporary = target.with_name(f".{target.stem}.{uuid4().hex}.tmp.pdf")
        try:
            document = QTextDocument()
            document.setMarkdown(preview_markdown(self.editor.toPlainText()))
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(temporary))
            document.print_(printer)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise OSError("未生成 PDF")
            temporary.replace(target)
        except Exception:
            _logger.exception("笔记 PDF 导出失败")
            self.status.setText("PDF 导出失败，编辑内容仍保留。")
            return
        self.status.setText("PDF 已导出。")

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._close_approved and not self._may_discard():
            event.ignore()
            return
        self._close_approved = True
        if self._links_worker is not None:
            event.ignore()
            return
        self._timer.stop()
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
