"""Markdown 阅读笔记编辑器；显式保存/导出，来源失效可见。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import string
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
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


def _markdown_caption(text: str) -> str:
    return "".join("\\" + char if char in string.punctuation else char for char in text)


class _Sources(QListWidget):
    sourcePrepared = Signal(str)

    def mimeData(self, items: Sequence[QListWidgetItem]) -> QMimeData:
        data = QMimeData()
        text = "\n".join(str(item.data(Qt.ItemDataRole.UserRole)) for item in items)
        self.sourcePrepared.emit(text)
        markdown = display_markdown(text)
        rendered = QTextDocument()
        rendered.setMarkdown(markdown)
        data.setText(rendered.toPlainText())
        data.setHtml(rendered.toHtml())
        data.setData("application/x-lumen-markdown", markdown.encode("utf-8"))
        return data


class _SourceEdit(QPlainTextEdit):
    def insertFromMimeData(self, source: QMimeData) -> None:
        if source.hasFormat("application/x-lumen-markdown"):
            self.insertPlainText(bytes(source.data("application/x-lumen-markdown")).decode("utf-8"))
        else:
            super().insertFromMimeData(source)


class _NoteEdit(QTextEdit):
    anchorActivated = Signal(QUrl)

    def __init__(self) -> None:
        super().__init__()
        self._link_press = QPoint()
        self.setToolTip("点击来源链接回查原文；按 Esc 返回阅读。链接内容可在 Markdown 源码中修改。")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._link_press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint()
        anchor = self.anchorAt(point)
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and (point - self._link_press).manhattanLength() < 6
            and not self.textCursor().hasSelection()
            and QUrl(anchor).scheme() in ("source", "material", "note")
        ):
            self.anchorActivated.emit(QUrl(anchor))

    def insertFromMimeData(self, source: QMimeData) -> None:
        if source.hasFormat("application/x-lumen-markdown"):
            self.textCursor().insertMarkdown(
                bytes(source.data("application/x-lumen-markdown")).decode("utf-8")
            )
        else:
            super().insertFromMimeData(source)


class NotebookPanel(QWidget):
    """可嵌入的单文献笔记；来源实例在创建时固定，不随当前标签变化。"""

    closed = Signal()
    closeReady = Signal()
    sourceStateChanged = Signal()
    readingRequested = Signal()

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
        self._document = controller.document
        self._source_title = (
            self._document.path.name if self._document else controller.title
        ) or "阅读笔记"
        self._encrypted_source = controller.is_encrypted
        self._setting_body = False
        self._visual_original = ""
        self._visual_revision = -1
        self._edit_source = False
        self._closing = False
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
        root.setContentsMargins(8, 8, 8, 8)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        root.addWidget(self.source_label)
        line = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(list(TEMPLATES))
        self.mode = QComboBox()
        self.mode.addItems(["编辑", "预览", "源码"])
        self.mode.setToolTip("编辑 / 预览 / Markdown 源码；源码保留原语法和来源标识。")
        self.mode.setAccessibleName("笔记视图")
        line.addWidget(self.mode)
        save_button = QToolButton()
        save_button.setText("保存")
        save_button.clicked.connect(self.save)
        line.addWidget(save_button)
        self.source_button = QToolButton()
        self.source_button.setText("批注")
        self.source_button.setCheckable(True)
        self.source_button.setToolTip("展开批注来源，双击或拖入笔记")
        line.addWidget(self.source_button)
        more = QToolButton()
        more.setText("更多")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        templates = menu.addMenu("新建模板")
        for kind in TEMPLATES:
            action = templates.addAction(kind)
            action.triggered.connect(lambda checked=False, name=kind: self._choose_template(name))
        for text, handler in (
            ("打开笔记", self.open_note),
            ("导出 Word", self.word),
            ("导出 PDF", self.pdf),
            ("刷新来源", self.refresh_sources),
            ("插入素材", self.insert_material),
            ("插入引用", self.insert_citation),
            ("反向链接", self.backlinks),
            ("关闭笔记", self.close),
        ):
            action = menu.addAction(text)
            assert action is not None
            action.triggered.connect(handler)
        more.setMenu(menu)
        line.addWidget(more)
        root.addLayout(line)
        self.sources = _Sources()
        self.sources.setMaximumHeight(180)
        self.sources.sourcePrepared.connect(self._remember_source)
        self.sources.setDragEnabled(True)
        self.sources.itemDoubleClicked.connect(self._insert_source)
        self.sources.hide()
        self.source_button.toggled.connect(self.sources.setVisible)
        root.addWidget(self.sources)
        self._views = QStackedWidget()
        self.visual_editor = _NoteEdit()
        self.visual_editor.setAcceptRichText(False)
        self.visual_editor.anchorActivated.connect(self._follow)
        self.visual_editor.setPlaceholderText("写下想法，或展开批注插入带来源的摘录…")
        self.source_editor = _SourceEdit()
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.setOpenExternalLinks(False)
        self.preview.anchorClicked.connect(self._follow)
        for widget in (self.visual_editor, self.preview, self.source_editor):
            self._views.addWidget(widget)
            widget.installEventFilter(self)
        root.addWidget(self._views, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        for editor in (self.visual_editor, self.source_editor):
            editor.textChanged.connect(self._render)
        self.mode.currentIndexChanged.connect(self._change_mode)
        controller.documentChanged.connect(self.refresh_sources)
        controller.documentClosed.connect(self._source_closed)
        controller.documentOpened.connect(self.refresh_sources)
        self._set_body(template(_markdown_caption(self._source_title), "期刊论文"), fresh=True)
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.document().setModified(False)
        self.refresh_sources()

    @property
    def editor(self) -> QTextEdit | QPlainTextEdit:
        return self.source_editor if self._edit_source else self.visual_editor

    def _body(self) -> str:
        if self._edit_source:
            return self.source_editor.toPlainText()
        if self.visual_editor.document().revision() == self._visual_revision:
            return self._visual_original
        markdown = self.visual_editor.toMarkdown()
        # 富文本把用户手输的 [[双链]] 当作普通方括号并自动转义。仅恢复可见正文中
        # 未转义的双链；源码/已有显式转义文本不经过该转换。
        titles = {
            match[1]
            for match in re.finditer(r"(?<!\\)\[\[([^\]\n]+)\]\]", self.visual_editor.toPlainText())
        }
        for title in titles:
            token = f"[[{title}]]"
            literal = QTextDocument()
            literal.setPlainText(token)
            escaped = literal.toMarkdown().strip("\r\n")
            markdown = markdown.replace(escaped, token)
        # Qt 序列化锚文本时不转义文件名中的方括号。只根据文档里实际存在的
        # 锚点修复相应标签，不能在任意用户 Markdown 上猜测或全局重写链接。
        block = self.visual_editor.document().begin()
        while block.isValid():
            iterator = block.begin()
            runs: list[tuple[int, int, str]] = []
            previous_href = ""
            while not iterator.atEnd():
                fragment = iterator.fragment()
                href = fragment.charFormat().anchorHref()
                start, end = fragment.position(), fragment.position() + fragment.length()
                if href:
                    if runs and previous_href == href and runs[-1][1] == start:
                        runs[-1] = (runs[-1][0], end, href)
                    else:
                        runs.append((start, end, href))
                previous_href = href
                iterator += 1
            for start, end, href in runs:
                selected = QTextCursor(self.visual_editor.document())
                selected.setPosition(start)
                selected.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                # 选区的真实 writer 输出保留长 URI 引起的换行和格式拆分；清块格式
                # 避免把列表/标题前缀也当作链接的一部分。默认字体须与原文档相同。
                fragment_doc = QTextDocument()
                fragment_doc.setDefaultFont(self.visual_editor.document().defaultFont())
                cursor = QTextCursor(fragment_doc)
                cursor.insertFragment(selected.selection())
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.setBlockFormat(QTextBlockFormat())
                encoded = fragment_doc.toMarkdown().strip("\r\n")
                caption = _markdown_caption(selected.selectedText())
                replacement = f"[{caption}]({href})"
                if encoded in markdown:
                    markdown = markdown.replace(encoded, replacement)
                else:
                    # 列表和引用会为 writer 自动换行加续行前缀；仅匹配实际锚点候选。
                    pattern = r"\n[ \t>]*".join(re.escape(line) for line in encoded.split("\n"))

                    def substitute(_match: re.Match[str], value: str = replacement) -> str:
                        return value

                    markdown = re.sub(pattern, substitute, markdown)
            block = block.next()
        return markdown

    def _set_body(self, text: str, *, fresh: bool = False) -> None:
        self._setting_body = True
        self.visual_editor.setMarkdown(text)
        canonical = self.visual_editor.toMarkdown()
        if fresh:
            cursor = self.visual_editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if cursor.blockFormat().headingLevel():
                cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
                self.visual_editor.setTextCursor(cursor)
        # Qt 不支持任意 Markdown 的无损往返。既有文件不等价时保留源码，绝不静默重写。
        safe = fresh or (canonical.strip("\r\n") == text.strip("\r\n") and r"\[[" not in text)
        self._edit_source = not safe
        self.source_editor.setPlainText(text)
        self._visual_original = text
        self._visual_revision = self.visual_editor.document().revision()
        self.mode.setCurrentIndex(0 if safe else 2)
        self._views.setCurrentIndex(0 if safe else 2)
        self._setting_body = False
        self._render()
        if not safe:
            self.status.setText("此笔记含无法无损转换的 Markdown，已保留源码编辑；可切换预览查看。")

    def _change_mode(self, index: int) -> None:
        if self._setting_body:
            return
        if index == 1:
            self.preview.setMarkdown(preview_markdown(self._body()))
            self._views.setCurrentIndex(1)
            return
        if (index == 2) == self._edit_source:
            self._views.setCurrentIndex(index)
            self.editor.setFocus()
            return
        text, modified = self._body(), self.editor.document().isModified()
        if index == 0:
            check = QTextDocument()
            check.setMarkdown(text)
            if check.toMarkdown().strip("\r\n") != text.strip("\r\n"):
                self.mode.blockSignals(True)
                self.mode.setCurrentIndex(2)
                self.mode.blockSignals(False)
                self._views.setCurrentIndex(2)
                self.status.setText("当前 Markdown 无法无损转换，继续保留源码；预览不会改写内容。")
                return
        self._setting_body = True
        self._edit_source = index == 2
        if self._edit_source:
            self.source_editor.setPlainText(text)
        else:
            self.visual_editor.setMarkdown(text)
            self._visual_original = text
            self._visual_revision = self.visual_editor.document().revision()
        self.editor.document().setModified(modified)
        self._views.setCurrentIndex(index)
        self._setting_body = False
        self.editor.setFocus()

    def _choose_template(self, kind: str) -> None:
        self.templates.setCurrentText(kind)
        self.new_template()

    def _insert_markdown(self, text: str) -> None:
        if self._edit_source:
            self.source_editor.insertPlainText(text)
        else:
            self.visual_editor.textCursor().insertMarkdown(text)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(event, QKeyEvent) and event.type() in (
            QEvent.Type.ShortcutOverride,
            QEvent.Type.KeyPress,
        ):
            save = event.matches(QKeySequence.StandardKey.Save)
            undo = event.matches(QKeySequence.StandardKey.Undo)
            redo = event.matches(QKeySequence.StandardKey.Redo)
            escape = event.key() == Qt.Key.Key_Escape
            if save or undo or redo or escape:
                if event.type() == QEvent.Type.KeyPress:
                    if save:
                        self.save()
                    elif undo:
                        self.editor.undo()
                    elif redo:
                        self.editor.redo()
                    elif escape:
                        self.readingRequested.emit()
                event.accept()
                return True
        return bool(super().eventFilter(watched, event))

    def _source_alive(self) -> bool:
        return self._document is not None and self._controller.document is self._document

    def _source_closed(self) -> None:
        self._timer.stop()
        self._annotations.clear()
        self.sources.clear()
        self.source_label.setText(
            f"来源：{self._source_title} · 已关闭；草稿保留，来源定位已失效。"
        )
        self._render()
        self.sourceStateChanged.emit()

    def backlinks(self) -> None:
        if self._path is None:
            self.status.setText("先保存当前笔记，再查看同目录反向链接。")
            return
        if self._links_worker is not None:
            return
        self._links_worker = BacklinksWorker(self._path)
        self._links_worker.result.connect(self._backlinks_result)
        self._links_worker.failed.connect(self._backlinks_failed)
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

    def _backlinks_failed(self, message: str) -> None:
        if not self._closing:
            self.status.setText(message)

    def _backlinks_finished(self) -> None:
        if self._links_worker is not None:
            self._links_worker.deleteLater()
            self._links_worker = None
        if self._closing:
            self.closeReady.emit()

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
            self._insert_markdown(
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
        if self._source_alive():
            self.source_label.setText(
                f"来源：{self._source_title} · 固定绑定此文献，切换标签不会改绑。"
            )
            self._timer.start()
        else:
            self._source_closed()

    def _scan_tick(self) -> None:
        if not self._source_alive() or self._page >= self._controller.page_count:
            self._timer.stop()
            self._render()
            return
        items = self._controller.list_annotations(self._page)
        self._annotations.extend(items)
        caption = _markdown_caption(self._source_title)
        for annotation in items:
            item = QListWidgetItem(
                f"第 {self._page + 1} 页 · {annotation.type_name}\n"
                + (annotation.marked_text or annotation.content)[:70]
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                annotation_note(annotation, self._document_key).replace("[原文 ·", f"[{caption} ·"),
            )
            self.sources.addItem(item)
        self._page += 1

    def _insert_source(self, item: QListWidgetItem) -> None:
        text = str(item.data(Qt.ItemDataRole.UserRole))
        self._remember_source(text)
        self._insert_markdown(display_markdown(text))

    def _remember_source(self, text: str) -> None:
        self._anchors.update((anchor.fingerprint, anchor) for anchor in source_anchors(text))

    def _markdown(self) -> str:
        return self._invalid_metadata + annotated_markdown(
            self._body(), list(self._anchors.values())
        )

    def _render(self) -> None:
        if self._setting_body:
            return
        text = self._body()
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
            if self._timer.isActive():
                self.status.setText("正在核对批注来源，请稍候再定位。")
                return
            if not self._source_alive():
                self.status.setText("该来源文档已关闭，定位已失效；摘录和草稿仍保留。")
                return
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
            self._set_body(
                template(_markdown_caption(self._source_title), self.templates.currentText()),
                fresh=True,
            )
            self.editor.document().setModified(True)

    def open_note(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "打开笔记", "", "Markdown (*.md)")
        if filename:
            try:
                self.load_path(Path(filename))
            except (OSError, UnicodeError):
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
        self._set_body(display_markdown(text))
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
            self._encrypted_source
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
            export_docx(target, self._body())
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
            document.setMarkdown(preview_markdown(self._body()))
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

    def prepare_close(self) -> bool:
        """先处理草稿，再等待只读扫描结束；等待期间禁止继续编辑。"""
        if not self._close_approved and not self._may_discard():
            return False
        self._close_approved = True
        if self._links_worker is not None:
            self._closing = True
            self.setEnabled(False)
            self.status.setText("等待反向链接扫描结束后关闭，草稿仍保留。")
            return False
        return True

    def cancel_close(self) -> None:
        self._close_approved = False
        self._closing = False
        self.setEnabled(True)

    def finish_close(self) -> None:
        self._timer.stop()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.prepare_close():
            event.ignore()
            return
        self.finish_close()
        self.closed.emit()
        event.accept()


class NotebookDialog(QDialog):
    """独立窗口宿主；主阅读器直接复用 NotebookPanel。"""

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
        self.resize(640, 760)
        self.content = NotebookPanel(
            controller,
            navigate,
            self,
            materials=materials,
            reference=reference,
            on_reference=on_reference,
        )
        layout = QVBoxLayout(self)
        layout.addWidget(self.content)
        self.content.closed.connect(self.close)
        self.content.closeReady.connect(self.close)

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.content.prepare_close():
            event.ignore()
            return
        self.content.finish_close()
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
