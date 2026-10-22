"""独立阅读分屏与演示窗口；共用受控文档，在主线程渲染，不改文档旋转。"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import DocumentController
from pdfreader.gui.widgets.pdf_view import PdfView
from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

_logger = logging.getLogger("pdfreader")


class ReadingPane(QWidget):
    def __init__(
        self, documents: Sequence[tuple[str, DocumentController]], selected: int = 0
    ) -> None:
        super().__init__()
        self._documents = list(documents)
        self._controller: DocumentController | None = None
        self._rotation = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.controls = QWidget()
        header = QHBoxLayout(self.controls)
        self.document = QComboBox()
        self.document.addItems([title for title, _controller in documents])
        self.document.setCurrentIndex(selected)
        self.document.currentIndexChanged.connect(self._select_document)
        self.page = QSpinBox()
        self.page.setRange(1, 1)
        self.page.valueChanged.connect(self._jump)
        rotate = QPushButton("旋转预览")
        rotate.clicked.connect(self.rotate_preview)
        overview = QPushButton("页览")
        overview.clicked.connect(self.overview)
        zoom_in = QPushButton("＋")
        zoom_out = QPushButton("－")
        for widget in (self.document, self.page, rotate, overview, zoom_out, zoom_in):
            header.addWidget(widget)
        root.addWidget(self.controls)
        self.view = PdfView(cache_size=8, cache_bytes=96 * 1024 * 1024)
        self.view.set_read_only(True)
        self.view.renderRequested.connect(self._render)
        self.view.pageChanged.connect(self._page_changed)
        zoom_in.clicked.connect(self.view.zoom_in)
        zoom_out.clicked.connect(self.view.zoom_out)
        self.canvas = QGraphicsView()
        self.canvas.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._scene = QGraphicsScene(self.canvas)
        self.canvas.setScene(self._scene)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.canvas)
        root.addWidget(self.stack)
        self.status = QLabel("两栏独立滚动；旋转预览只影响显示，返回原视图后可选择文字。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self._select_document(selected)

    def _select_document(self, index: int) -> None:
        if not 0 <= index < len(self._documents):
            return
        if self._controller is not None:
            self._controller.documentClosed.disconnect(self._closed)
            self._controller.documentChanged.disconnect(self._reload)
        self._controller = self._documents[index][1]
        self._controller.documentClosed.connect(self._closed)
        self._controller.documentChanged.connect(self._reload)
        self.view.set_text_provider(self._controller.get_words)
        self.view.set_link_provider(self._controller.get_links)
        self._rotation = 0
        self.stack.setCurrentIndex(0)
        self._reload()

    def _reload(self) -> None:
        controller = self._controller
        if controller is None or not controller.has_document:
            self._closed()
            return
        page = min(self.page.value() - 1, controller.page_count - 1)
        self.page.setMaximum(controller.page_count)
        bounds = controller.get_page_bounds()
        self.view.setup_document(
            controller.page_count,
            [(round(b.width), round(b.height)) for b in bounds],
            keep_page=max(0, page),
        )
        self.view.fit_to_width()
        if self._rotation:
            self._rotated_page()

    def _closed(self) -> None:
        self.view.clear()
        self._scene.clear()
        self.status.setText("此文档已关闭，请选择其他文档。")

    def _render(self, page: int, zoom: float) -> None:
        controller = self._controller
        if controller is None or not controller.has_document:
            return
        try:
            data = controller.render_page(page, self.view.render_scale(page))
            self.view.on_page_rendered(page, zoom, data)
        except Exception:
            _logger.exception("分屏页面渲染失败")
            self.status.setText(f"第 {page + 1} 页渲染失败。")

    def _page_changed(self, page: int) -> None:
        self.page.blockSignals(True)
        self.page.setValue(page + 1)
        self.page.blockSignals(False)

    def _jump(self, page: int) -> None:
        self.view.jump_to_page(page - 1)
        if self._rotation:
            self._rotated_page()

    def rotate_preview(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self.stack.setCurrentIndex(1 if self._rotation else 0)
        if self._rotation:
            self._rotated_page()

    def _rotated_page(self) -> None:
        controller = self._controller
        if controller is None or not controller.has_document:
            return
        try:
            page = self.page.value() - 1
            bounds = controller.get_page_bounds()[page]
            scale = min(2.0, 1600 / max(bounds.width, bounds.height, 1.0))
            pixmap = pixmap_to_qpixmap(controller.render_page(page, scale))
            rotated = pixmap.transformed(
                QTransform().rotate(self._rotation), Qt.TransformationMode.SmoothTransformation
            )
            self._scene.clear()
            self._scene.addPixmap(rotated)
            self._scene.setSceneRect(self._scene.itemsBoundingRect())
            self.canvas.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        except Exception:
            _logger.exception("旋转预览失败")
            self.status.setText("旋转预览失败。")

    def overview(self) -> None:
        controller = self._controller
        if controller is None or not controller.has_document:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("页面总览")
        dialog.resize(700, 600)
        root = QVBoxLayout(dialog)
        pages = QListWidget()
        pages.setViewMode(QListWidget.ViewMode.IconMode)
        pages.setIconSize(QSize(100, 140))
        pages.setGridSize(QSize(120, 175))
        pages.setResizeMode(QListWidget.ResizeMode.Adjust)
        for index in range(controller.page_count):
            item = QListWidgetItem(str(index + 1))
            pages.addItem(item)
        root.addWidget(pages)
        timer = QTimer(dialog)
        timer.setInterval(20)
        rendered: set[int] = set()
        bounds = controller.get_page_bounds()

        def tick() -> None:
            if not controller.has_document:
                dialog.reject()
                return
            for index in range(controller.page_count):
                item = pages.item(index)
                if (
                    index in rendered
                    or item is None
                    or not pages.visualItemRect(item).intersects(pages.viewport().rect())
                ):
                    continue
                try:
                    scale = min(100 / bounds[index].width, 140 / bounds[index].height)
                    item.setIcon(QIcon(pixmap_to_qpixmap(controller.render_page(index, scale))))
                except Exception:
                    _logger.exception("页览缩略图生成失败")
                    item.setText(f"{index + 1}（失败）")
                rendered.add(index)
                break

        def locate(item: QListWidgetItem) -> None:
            self.page.setValue(pages.row(item) + 1)
            dialog.accept()

        pages.itemDoubleClicked.connect(locate)
        timer.timeout.connect(tick)
        timer.start()
        dialog.exec()
        timer.stop()
        dialog.deleteLater()


class ReadingWorkspace(QDialog):
    def __init__(
        self,
        documents: Sequence[tuple[str, DocumentController]],
        selected: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("分屏阅读 · F11 演示 · Esc 返回")
        self.resize(1250, 850)
        root = QVBoxLayout(self)
        self.splitter = QSplitter()
        self.left = ReadingPane(documents, selected)
        self.right = ReadingPane(documents, selected)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self.right)
        root.addWidget(self.splitter)
        fullscreen = QShortcut(QKeySequence("F11"), self)
        fullscreen.activated.connect(self.toggle_presentation)
        self._shortcuts: list[QShortcut] = []
        for key, step in (
            ("Right", 1),
            ("Left", -1),
            ("Space", 1),
            ("PageDown", 1),
            ("PageUp", -1),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda step=step: self.left.page.stepBy(step))
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    def toggle_presentation(self) -> None:
        fullscreen = not self.isFullScreen()
        self.right.setVisible(not fullscreen)
        self.left.controls.setVisible(not fullscreen)
        self.left.status.setVisible(not fullscreen)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(fullscreen)
        if fullscreen:
            self.showFullScreen()
            self.left.view.fit_to_page()
        else:
            self.showNormal()
