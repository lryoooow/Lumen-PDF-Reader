"""主窗口。

阶段 3：接入 DocumentController 与 PdfView，实现"打开 PDF + 单页渲染"。
- 菜单/工具栏的"打开"动作 → 控制器打开文件 → 渲染首页显示
- 错误处理：损坏/加密/不存在文件给出友好提示
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import DocumentController, OpenResult
from pdfreader.gui.widgets import PdfView
from pdfreader.workers import RenderWorker

__all__ = ["MainWindow"]

_PDF_FILTER = "PDF 文件 (*.pdf);;所有文件 (*.*)"


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self._controller = DocumentController()
        self._render_worker: RenderWorker | None = None
        self._setup_window()
        self._setup_central_widget()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()
        self._connect_signals()

    # ------------------------------------------------------------------
    # 初始化各部分
    # ------------------------------------------------------------------
    def _setup_window(self) -> None:
        self.setWindowTitle(self.tr("PDF Reader"))
        self.resize(1024, 768)
        self.setMinimumSize(640, 480)

    def _setup_central_widget(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pdf_view = PdfView(central)
        layout.addWidget(self.pdf_view)

        self._placeholder = QLabel(self.tr("未打开文件 · 文件 → 打开 或 Ctrl+O"), central)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("font-size: 14pt; color: gray; background: #f5f5f5;")
        layout.addWidget(self._placeholder)

        self.setCentralWidget(central)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu(self.tr("文件(&F)"))

        self.open_action = QAction(self.tr("打开(&O)..."), self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self._on_open)
        file_menu.addAction(self.open_action)

        self.close_doc_action = QAction(self.tr("关闭文档(&C)"), self)
        self.close_doc_action.setShortcut(QKeySequence.StandardKey.Close)
        self.close_doc_action.setEnabled(False)
        self.close_doc_action.triggered.connect(self._on_close_document)
        file_menu.addAction(self.close_doc_action)

        file_menu.addSeparator()

        exit_action = QAction(self.tr("退出(&Q)"), self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu(self.tr("视图(&V)"))
        self.zoom_in_action = QAction(self.tr("放大(&Z)"), self)
        self.zoom_in_action.setShortcut(QKeySequence("Ctrl++"))
        self.zoom_in_action.triggered.connect(lambda: self.pdf_view.zoom_in())
        self.zoom_in_action.setEnabled(False)
        view_menu.addAction(self.zoom_in_action)

        self.zoom_out_action = QAction(self.tr("缩小"), self)
        self.zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        self.zoom_out_action.triggered.connect(lambda: self.pdf_view.zoom_out())
        self.zoom_out_action.setEnabled(False)
        view_menu.addAction(self.zoom_out_action)

        self.fit_action = QAction(self.tr("适应宽度(&W)"), self)
        self.fit_action.triggered.connect(lambda: self.pdf_view.fit_to_width())
        self.fit_action.setEnabled(False)
        view_menu.addAction(self.fit_action)

        help_menu = menubar.addMenu(self.tr("帮助(&H)"))
        about_action = QAction(self.tr("关于(&A)"), self)
        about_action.setEnabled(False)
        help_menu.addAction(about_action)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar(self.tr("主工具栏"), self)
        toolbar.setMovable(False)
        toolbar.addAction(self.open_action)
        toolbar.addSeparator()
        toolbar.addAction(self.zoom_in_action)
        toolbar.addAction(self.zoom_out_action)
        toolbar.addAction(self.fit_action)
        self.addToolBar(toolbar)

    def _setup_statusbar(self) -> None:
        self._status = QStatusBar(self)
        self._status.showMessage(self.tr("就绪"))
        self.setStatusBar(self._status)

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._controller.documentOpened.connect(self._on_document_opened)
        self._controller.documentClosed.connect(self._on_document_closed)
        self._controller.openFailed.connect(self._on_open_failed)

    def _setup_render_worker(self) -> None:
        """创建渲染线程并连接信号（在打开文档时按需启动）。"""
        worker = RenderWorker(self)
        worker.pageRendered.connect(self._on_page_rendered)
        worker.documentLoaded.connect(self._on_worker_document_loaded)
        worker.documentLoadFailed.connect(self._on_worker_load_failed)
        worker.renderFailed.connect(self._on_render_failed)
        # view 请求渲染 → 转发给 worker
        self.pdf_view.renderRequested.connect(worker.request_render)
        self._render_worker = worker

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("打开 PDF"), "", _PDF_FILTER)
        if path:
            self._controller.open_file(Path(path))

    def _on_close_document(self) -> None:
        self._controller.close_document()

    def _on_document_opened(self, page_count: int) -> None:
        """文档成功打开：准备视图布局，启动渲染线程加载文档。"""
        self._placeholder.hide()
        self.pdf_view.show()
        title = self._controller.title
        self.setWindowTitle(f"{title} — PDF Reader")

        # 用 controller 取页面尺寸，设置视图布局（占位，无需渲染）
        page_sizes = self._controller.get_page_sizes()
        self.pdf_view.setup_document(page_count, page_sizes)
        self.pdf_view.set_zoom(1.5)
        self.pdf_view.fit_to_width()

        # 启动渲染线程加载文档（Document 在线程内打开，主线程不持有）
        self._setup_render_worker()
        assert self._render_worker is not None
        assert self._controller.document is not None
        self._render_worker.load_document(self._controller.document.path)
        self._render_worker.start()

        self.close_doc_action.setEnabled(True)
        self.zoom_in_action.setEnabled(True)
        self.zoom_out_action.setEnabled(True)
        self.fit_action.setEnabled(True)
        self._status.showMessage(f"共 {page_count} 页 · 渲染中...")

    def _on_worker_document_loaded(self, page_count: int) -> None:
        """渲染线程加载文档成功，触发首屏渲染。"""
        self.pdf_view._request_visible_pages()
        self._status.showMessage(f"共 {page_count} 页")

    def _on_worker_load_failed(self, message: str) -> None:
        QMessageBox.warning(self, self.tr("渲染加载失败"), message)

    def _on_page_rendered(self, page_num: int, zoom: float, data) -> None:  # type: ignore[no-untyped-def]
        """渲染线程返回结果，转发给视图显示。"""
        self.pdf_view.on_page_rendered(page_num, zoom, data)
        # 更新状态栏当前页
        current = self.pdf_view.current_page_num
        if current is not None:
            total = self._controller.page_count
            self._status.showMessage(f"第 {current + 1} / {total} 页")

    def _on_render_failed(self, page_num: int, message: str) -> None:
        self._status.showMessage(f"第 {page_num + 1} 页渲染失败：{message}")

    def _on_document_closed(self) -> None:
        # 关闭渲染线程
        worker = getattr(self, "_render_worker", None)
        if worker is not None:
            worker.shutdown()
            worker.wait(3000)
            self._render_worker = None
        self.pdf_view.clear()
        self.pdf_view.hide()
        self._placeholder.show()
        self.setWindowTitle(self.tr("PDF Reader"))
        self.close_doc_action.setEnabled(False)
        self.zoom_in_action.setEnabled(False)
        self.zoom_out_action.setEnabled(False)
        self.fit_action.setEnabled(False)
        self._status.showMessage(self.tr("就绪"))

    def _on_open_failed(self, result: str, message: str) -> None:
        """打开失败的友好提示。"""
        titles = {
            OpenResult.NOT_FOUND: self.tr("文件不存在"),
            OpenResult.CORRUPT: self.tr("文件无法打开"),
            OpenResult.NEEDS_PASSWORD: self.tr("需要密码"),
            OpenResult.UNKNOWN_ERROR: self.tr("打开失败"),
        }
        title = titles.get(result, self.tr("打开失败"))
        QMessageBox.warning(self, title, message)

    # ------------------------------------------------------------------
    # 关闭事件
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # 先停渲染线程，再关文档
        worker = getattr(self, "_render_worker", None)
        if worker is not None:
            worker.shutdown()
            worker.wait(3000)
        if self._controller.has_document:
            self._controller.close_document()
        super().closeEvent(event)
