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
    QGraphicsView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pdfreader.controllers import (
    AnnotationController,
    AnnotationMode,
    DocumentController,
    OpenResult,
)
from pdfreader.engines.types import SearchResult
from pdfreader.gui.panels import SearchPanel
from pdfreader.gui.widgets import AnnotationToolbar, PdfView
from pdfreader.workers import RenderWorker, SearchWorker

__all__ = ["MainWindow"]

_PDF_FILTER = "PDF 文件 (*.pdf);;所有文件 (*.*)"


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self._controller = DocumentController()
        self._annotation_controller = AnnotationController()
        self._render_worker: RenderWorker | None = None
        self._search_worker: SearchWorker | None = None
        self._setup_window()
        self._setup_central_widget()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_search_panel()
        self._setup_annotation()
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

        edit_menu = menubar.addMenu(self.tr("编辑(&E)"))
        self.find_action = QAction(self.tr("查找(&F)..."), self)
        self.find_action.setShortcut(QKeySequence.StandardKey.Find)
        self.find_action.triggered.connect(self._on_find)
        self.find_action.setEnabled(False)
        edit_menu.addAction(self.find_action)

        edit_menu.addSeparator()

        self.delete_page_action = QAction(self.tr("删除当前页"), self)
        self.delete_page_action.triggered.connect(self._on_delete_page)
        self.delete_page_action.setEnabled(False)
        edit_menu.addAction(self.delete_page_action)

        self.rotate_action = QAction(self.tr("旋转当前页"), self)
        self.rotate_action.triggered.connect(self._on_rotate_page)
        self.rotate_action.setEnabled(False)
        edit_menu.addAction(self.rotate_action)

        self.merge_action = QAction(self.tr("合并 PDF..."), self)
        self.merge_action.triggered.connect(self._on_merge)
        self.merge_action.setEnabled(False)
        edit_menu.addAction(self.merge_action)

        self.watermark_action = QAction(self.tr("添加水印..."), self)
        self.watermark_action.triggered.connect(self._on_watermark)
        self.watermark_action.setEnabled(False)
        edit_menu.addAction(self.watermark_action)

        edit_menu.addSeparator()

        self.save_as_action = QAction(self.tr("另存为..."), self)
        self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_as_action.triggered.connect(self._on_save_as)
        self.save_as_action.setEnabled(False)
        edit_menu.addAction(self.save_as_action)

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

    def _setup_search_panel(self) -> None:
        """创建搜索面板（停靠在右侧）。"""
        self.search_panel = SearchPanel(self.tr("搜索"), self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.search_panel)
        self.search_panel.searchRequested.connect(self._on_search_requested)
        self.search_panel.resultClicked.connect(self._on_search_result_clicked)

    def _setup_annotation(self) -> None:
        """创建批注工具栏与控制器连接。"""
        self.annotation_toolbar = AnnotationToolbar(self.tr("批注"), self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.annotation_toolbar)
        self.annotation_toolbar.setVisible(False)
        # 视图注入批注控制器
        self.pdf_view.set_annotation_controller(self._annotation_controller)
        # 工具栏 → 控制器模式切换
        self.annotation_toolbar.modeRequested.connect(self._annotation_controller.set_mode)
        # 控制器 → 创建批注请求 → 委托文档
        self._annotation_controller.highlightRequested.connect(self._on_annot_highlight)
        self._annotation_controller.inkRequested.connect(self._on_annot_ink)
        self._annotation_controller.textRequested.connect(self._on_annot_text)
        self._annotation_controller.modeChanged.connect(self._on_annot_mode_changed)

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._controller.documentOpened.connect(self._on_document_opened)
        self._controller.documentClosed.connect(self._on_document_closed)
        self._controller.openFailed.connect(self._on_open_failed)

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def _on_find(self) -> None:
        if not self._controller.has_document:
            return
        self.search_panel.focus_input()

    def _on_search_requested(self, query: str) -> None:
        """启动后台搜索线程。"""
        if not self._controller.has_document:
            return
        # 停止旧搜索
        if self._search_worker is not None and self._search_worker.isRunning():
            self._search_worker.shutdown()
            self._search_worker.wait(2000)
        # 新建搜索线程（独立打开文档）
        worker = SearchWorker(self)
        worker.searchCompleted.connect(self._on_search_completed)
        worker.searchFailed.connect(self._on_search_failed)
        assert self._controller.document is not None
        worker.setup(self._controller.document.path, query)
        self._search_worker = worker
        worker.start()

    def _on_search_completed(self, query: str, result: SearchResult) -> None:
        self.search_panel.show_results(query, result.hits)

    def _on_search_failed(self, message: str) -> None:
        self.search_panel.show_error(message)

    def _on_search_result_clicked(self, page_num: int) -> None:
        """点击搜索结果，跳转到对应页。"""
        self.pdf_view.jump_to_page(page_num)

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------
    def _current_page_for_edit(self) -> int:
        """获取当前页码用于编辑操作（无则返回 0）。"""
        return self.pdf_view.current_page_num or 0

    def _on_delete_page(self) -> None:
        page = self._current_page_for_edit()
        reply = QMessageBox.question(
            self,
            self.tr("删除页"),
            self.tr("确定删除第 %1 页吗？").replace("%1", str(page + 1)),
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self._controller.delete_page(page):
                self._refresh_after_edit()
            else:
                QMessageBox.warning(self, self.tr("失败"), self.tr("删除页失败"))

    def _on_rotate_page(self) -> None:
        page = self._current_page_for_edit()
        if self._controller.rotate_page(page, 90):
            self._refresh_after_edit()
        else:
            QMessageBox.warning(self, self.tr("失败"), self.tr("旋转页失败"))

    def _on_merge(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("选择要合并的 PDF"), "", _PDF_FILTER)
        if path:
            added = self._controller.merge(Path(path))
            if added > 0:
                self._refresh_after_edit()
                self._status.showMessage(f"已合并 {added} 页")
            else:
                QMessageBox.warning(self, self.tr("失败"), self.tr("合并失败"))

    def _on_watermark(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, self.tr("添加水印"), self.tr("水印文字："))
        if ok and text:
            page = self._current_page_for_edit()
            if self._controller.add_watermark(page, text):
                self._refresh_after_edit()
            else:
                QMessageBox.warning(self, self.tr("失败"), self.tr("添加水印失败"))

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("另存为"), "", _PDF_FILTER)
        if path:
            if self._controller.save_as(Path(path)):
                self._status.showMessage(f"已保存到 {path}")
            else:
                QMessageBox.warning(self, self.tr("失败"), self.tr("保存失败"))

    def _refresh_after_edit(self) -> None:
        """编辑后刷新视图布局与渲染。"""
        # 重建视图布局（页数/尺寸可能变了）
        page_sizes = self._controller.get_page_sizes()
        self.pdf_view.setup_document(self._controller.page_count, page_sizes)
        # 标记文档已修改（状态栏提示）
        if self._controller.document is not None and self._controller.document.is_modified:
            self._status.showMessage(f"共 {self._controller.page_count} 页 · 未保存")

    # ------------------------------------------------------------------
    # 批注操作
    # ------------------------------------------------------------------
    def _on_annot_mode_changed(self, mode: str) -> None:
        names = {
            AnnotationMode.NONE: "浏览",
            AnnotationMode.HIGHLIGHT: "高亮",
            AnnotationMode.INK: "手写",
            AnnotationMode.TEXT: "便签",
        }
        # 批注模式下用等待箭头光标，浏览模式恢复拖拽
        from PySide6.QtGui import QCursor

        if mode == AnnotationMode.NONE:
            self.pdf_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.pdf_view.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        else:
            self.pdf_view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._status.showMessage(f"批注模式：{names.get(mode, mode)}")

    def _on_annot_highlight(self, page_num: int, rect) -> None:  # type: ignore[no-untyped-def]
        if self._controller.document is not None:
            self._controller.document.add_highlight(page_num, rect)
            self._refresh_page(page_num)

    def _on_annot_ink(self, page_num: int, stroke) -> None:  # type: ignore[no-untyped-def]
        if self._controller.document is not None:
            self._controller.document.add_ink(page_num, (stroke,))
            self._refresh_page(page_num)

    def _on_annot_text(self, page_num: int, point) -> None:  # type: ignore[no-untyped-def]
        if self._controller.document is None:
            return
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, self.tr("便签"), self.tr("输入便签内容："))
        if ok and text:
            self._controller.document.add_text_annot(page_num, point, text)
            self._refresh_page(page_num)

    def _refresh_page(self, page_num: int) -> None:
        """批注后重新渲染该页（清除缓存并请求重渲染）。"""
        self.pdf_view._rendered_cache.remove(page_num)
        if self._render_worker is not None:
            self._render_worker.request_render(page_num, self.pdf_view.zoom)

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
        self.find_action.setEnabled(True)
        self._enable_edit_actions(True)
        self.annotation_toolbar.setVisible(True)
        self._status.showMessage(f"共 {page_count} 页 · 渲染中...")

    def _enable_edit_actions(self, enabled: bool) -> None:
        """启用/禁用编辑相关动作。"""
        self.delete_page_action.setEnabled(enabled)
        self.rotate_action.setEnabled(enabled)
        self.merge_action.setEnabled(enabled)
        self.watermark_action.setEnabled(enabled)
        self.save_as_action.setEnabled(enabled)

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
        # 关闭搜索线程
        sw = getattr(self, "_search_worker", None)
        if sw is not None:
            sw.shutdown()
            sw.wait(2000)
            self._search_worker = None
        self.search_panel.clear()
        self.search_panel.setVisible(False)
        # 重置批注模式并隐藏工具栏
        self._annotation_controller.set_mode(AnnotationMode.NONE)
        self.annotation_toolbar.setVisible(False)
        self.annotation_toolbar.reset_to_select()
        self.pdf_view.clear()
        self.pdf_view.hide()
        self._placeholder.show()
        self.setWindowTitle(self.tr("PDF Reader"))
        self.close_doc_action.setEnabled(False)
        self.zoom_in_action.setEnabled(False)
        self.zoom_out_action.setEnabled(False)
        self.fit_action.setEnabled(False)
        self.find_action.setEnabled(False)
        self._enable_edit_actions(False)
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
