"""主窗口（Lumen 设计稿）。

- 外层两页：文献库首页（``LibraryHome``，无文档或点品牌 / 网格按钮时显示）⇄ 阅读器
- 阅读器顶部：标题栏（品牌 · 选项卡 · 主题切换 · 文献库）+ 按组标题分组的功能区
  + 文档标签栏（多文档：每个标签独立持有文档 / 视图 / AI 对话，见 ``document_tab``）
- 阅读器中部：左侧边栏（目录 / 批注 / 缩略图，宽度动画显隐）+ 阅读区；
  阅读区上叠加搜索浮层、工具提示胶囊，点击引用弹出参考文献预览卡
- 底部：状态栏（翻页 · 阅读进度 · 自动消失的消息 · 阅读模式标签 · 缩放滑杆）

职责：把用户动作接到控制器 / 后台线程，把结果反馈到界面。领域逻辑在 core / controllers，
失败原因经 ``DocumentController.last_error`` 如实展示（不静默）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from pdfreader.config.settings import (
    MAX_LIBRARY_FILES,
    Bookmark,
    DocMeta,
    ReadingPosition,
    SettingsStore,
)
from pdfreader.controllers import (
    AnnotationController,
    AnnotationMode,
    DocumentController,
    OpenResult,
)
from pdfreader.engines.types import (
    FREE_TEXT_FONTSIZE,
    Annotation,
    LinkInfo,
    Point,
    Rect,
    SearchResult,
)
from pdfreader.gui.ai_assistant import AiAssistant
from pdfreader.gui.document_tab import DocumentTab
from pdfreader.gui.panels import AiPanel, SearchPanel, SidePanel
from pdfreader.gui.panels.ai_panel import AI_PANEL_MAX_WIDTH, AI_PANEL_MIN_WIDTH, AI_PANEL_WIDTH
from pdfreader.gui.panels.sidebar import SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, SIDEBAR_WIDTH
from pdfreader.gui.selection_translator import SelectionTranslator
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import (
    DocumentTabBar,
    LibraryEntry,
    LibraryHome,
    PdfView,
    Ribbon,
    StatusBar,
    motion,
)
from pdfreader.gui.widgets.library import display_title
from pdfreader.gui.widgets.overlays import CitePopover, ToolHint
from pdfreader.gui.widgets.panel_grip import PanelGrip
from pdfreader.gui.widgets.pdf_view import ReadingMode
from pdfreader.services.ai.provider import AiSettings
from pdfreader.services.materials import (
    KIND_LABELS,
    MAX_TERM_CHARS,
    Material,
    MaterialError,
    MaterialStore,
    quote_template,
)
from pdfreader.services.ocr import OCR_AVAILABLE
from pdfreader.services.zotero import (
    ZoteroError,
    ZoteroLibrary,
    find_data_dir,
    to_pdf_space_annotations,
)
from pdfreader.utils.text import clean_copied_text, is_cjk_char
from pdfreader.workers import OcrWorker, SearchWorker

__all__ = ["MainWindow"]

_logger = logging.getLogger("pdfreader")

_PDF_FILTER = "PDF 文件 (*.pdf);;所有文件 (*.*)"
# 打开加密文档时允许输错密码的次数
_PASSWORD_ATTEMPTS = 3
# 允许从文档里点开的外部链接协议（其余如 file: / javascript: 一律不打开）
_SAFE_SCHEMES = ("http", "https", "mailto")
# 高亮颜色（设计稿：黄 / 绿 / 蓝 / 粉 / 橙）
_HIGHLIGHT_COLORS = (
    ("黄", "#FACC15"),
    ("绿", "#4ADE80"),
    ("蓝", "#60A5FA"),
    ("粉", "#F472B6"),
    ("橙", "#FB923C"),
)
_READING_MODES = (
    (ReadingMode.NORMAL, "正常", "#FFFFFF"),
    (ReadingMode.NIGHT, "夜间（页面反色）", "#18181B"),
    (ReadingMode.SEPIA, "护眼（暖色纸张）", "#F3E7C9"),
)
_MODE_CHIP = {ReadingMode.NIGHT: "夜间", ReadingMode.SEPIA: "护眼"}
_TOOL_NAMES = {
    AnnotationMode.NONE: "选择",
    AnnotationMode.PAN: "抓手",
    AnnotationMode.HIGHLIGHT: "高亮",
    AnnotationMode.UNDERLINE: "下划线",
    AnnotationMode.INK: "手写",
    AnnotationMode.TEXT: "便签",
    AnnotationMode.FREETEXT: "文本框",
    AnnotationMode.ERASER: "橡皮擦",
    AnnotationMode.SIGNATURE: "签名",
    AnnotationMode.TIMESTAMP: "时间",
    AnnotationMode.CAPTURE: "框选提问",
}
# 「参考文献 [12]」式链接文字（取词常带上相邻标点，如 "[1]."、"([2,3])"）
_CITATION_RE = re.compile(r"^\W*\[?\s*(\d+(?:\s*[,，–\-]\s*\d+)*)\s*\]?\W*$")
# 导航历史上限
_HISTORY_LIMIT = 50
# 批注面板「随时刷新」的页数上限（更大的文档只在批注页签可见时扫描）
_ANNOT_SCAN_PAGE_LIMIT = 300
# 同时打开的文档标签上限（每个标签常驻一份已解析的文档）
MAX_TABS = 20


def _hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    text = value.lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16) / 255, int(text[2:4], 16) / 255, int(text[4:6], 16) / 255)
    except ValueError:
        return None


def free_text_rect(
    point: Point, text: str, page: Rect, fontsize: float = FREE_TEXT_FONTSIZE
) -> Rect:
    """按文字量估算文本框大小：左上角落在单击点，最宽 260pt 自动折行，超出页边则往回挪。

    估算偏宽一点（中文按 1em、西文按 0.55em），让 MuPDF 的实际排版不至于溢出框外。
    """
    max_w = min(260.0, page.width - 24)
    line_h = fontsize * 1.4
    widths: list[float] = []
    for para in text.splitlines() or [""]:
        w = 0.0
        for ch in para:
            step = fontsize if is_cjk_char(ch) else fontsize * 0.55
            if w + step > max_w:
                widths.append(w)
                w = 0.0
            w += step
        widths.append(w)
    width = min(max_w, max(60.0, max(widths) * 1.08)) + 10
    height = len(widths) * line_h + 10
    x0 = min(max(page.x0 + 4, point.x), page.x1 - width - 4)
    y0 = min(max(page.y0 + 4, point.y), page.y1 - height - 4)
    return Rect(x0, y0, x0 + width, y0 + height)


# 拖宽侧栏时阅读区至少留这么宽
_MIN_READING_WIDTH = 360


class _ContentArea(QWidget):
    """内容容器：侧边栏 + 阅读区；尺寸变化时重新定位浮层（搜索卡片、工具提示）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContentArea")
        self._overlay: QWidget | None = None
        self.tool_hint: ToolHint | None = None
        self.reading_area: QWidget | None = None

    def set_overlay(self, widget: QWidget) -> None:
        self._overlay = widget

    def reposition_overlay(self) -> None:
        ov = self._overlay
        if ov is not None and ov.isVisible():
            margin_right, margin_top = 20, 16  # 设计稿 top-4 right-5
            ov.move(max(margin_right, self.width() - ov.width() - margin_right), margin_top)
            ov.resize(ov.width(), min(ov.sizeHint().height(), self.height() - 2 * margin_top))
        if self.tool_hint is not None and self.reading_area is not None:
            area = self.reading_area.geometry()
            self.tool_hint.move(
                area.x() + (area.width() - self.tool_hint.width()) // 2, area.y() + 16
            )

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.reposition_overlay()


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self._theme = get_theme()
        self._annotation_controller = AnnotationController()
        # 文档标签：至少一个；没有打开文档时只剩一个空标签（显示文献库）
        self._tabs: list[DocumentTab] = []
        self._parked_tabs: list[DocumentTab] = []  # 已关闭、AI 请求线程还没停下的标签
        self._shutting_down = False
        self._search_worker: SearchWorker | None = None
        self._retired_search_workers: list[SearchWorker] = []
        self._active_query: str = ""
        self._retired_ocr_workers: list[OcrWorker] = []
        self._ocr_progress: QProgressDialog | None = None
        self._ocr_progress_owner: DocumentTab | None = None
        self._tab: DocumentTab  # 当前标签（由 _activate 设定）
        self._pending_link: tuple[int, float | None] | None = None  # 引用预览卡待跳转的目标
        self._capture_translate = False  # 本次框选是划词翻译的截图识别（否则交给 AI 解释）
        # 写作素材库（单词本 / 语料 / 引文 / 术语）：跨文档共用，首次用到时才建库
        self._materials = MaterialStore()
        self._snapshot_dir = Path(tempfile.gettempdir()) / f"pdfreader_snapshots_{os.getpid()}"
        self._snapshot_paths: list[Path] = []
        self._temp_files: list[Path] = []  # 其他本进程生成的临时文件（如手绘签名）
        self._last_open_failure: str = ""
        # 文献库显示时暂存的阅读器快捷键（否则 Ctrl+W 等会作用在看不见的文档上）
        self._parked_shortcuts: dict[QAction, list[QKeySequence]] = {}

        # 设置持久化（主题、窗口尺寸、最近文件、阅读位置……）
        self._settings_store = SettingsStore()
        self._settings = self._settings_store.load()

        # 依据设置应用主题（在建 UI 之前，避免闪烁）
        self._theme.set_theme(self._settings.theme)
        self._theme.apply()

        self._setup_window()
        self._create_actions()
        self._setup_ui()
        self._activate(self._new_tab())
        self._connect_signals()
        self._theme.changed.connect(self._on_theme_changed)

        self._set_document_state(False)
        self._show_library(animate=False)

        if self._settings.window_maximized:
            QTimer.singleShot(0, self.showMaximized)

    # ------------------------------------------------------------------ 当前标签
    # 单文档时代的代码都通过这几个属性访问「当前文档」：它们总是指向当前标签，
    # 而后台标签的视图 / 面板信号在接线处就被挡掉（见 _for_tab），不会作用到当前文档上。
    @property
    def _controller(self) -> DocumentController:
        return self._tab.controller

    @property
    def pdf_view(self) -> PdfView:
        return self._tab.view

    @property
    def ai_panel(self) -> AiPanel:
        return self._tab.panel

    @property
    def _ai(self) -> AiAssistant:
        return self._tab.ai

    @property
    def _back_stack(self) -> list[tuple[int, float]]:
        return self._tab.back_stack

    @property
    def _forward_stack(self) -> list[tuple[int, float]]:
        return self._tab.forward_stack

    @property
    def _ocr_results(self) -> dict[int, str]:
        return self._tab.ocr_results

    @_ocr_results.setter
    def _ocr_results(self, value: dict[int, str]) -> None:
        self._tab.ocr_results = value

    @property
    def _ocr_worker(self) -> OcrWorker | None:
        return self._tab.ocr_worker

    @_ocr_worker.setter
    def _ocr_worker(self, worker: OcrWorker | None) -> None:
        self._tab.ocr_worker = worker

    @property
    def _annotations_stale(self) -> bool:
        return self._tab.annotations_stale

    @_annotations_stale.setter
    def _annotations_stale(self, value: bool) -> None:
        self._tab.annotations_stale = value

    @property
    def tabs(self) -> list[DocumentTab]:
        """全部文档标签（按标签栏顺序；无文档时是一个空标签）。"""
        return list(self._tabs)

    @property
    def current_tab(self) -> DocumentTab:
        return self._tab

    # ------------------------------------------------------------------ 标签：创建与接线
    def _new_tab(self) -> DocumentTab:
        """新建一个标签（尚未加入标签栏）：自己的控制器、视图、AI 面板与 AI 会话。"""
        tab = DocumentTab(DocumentController(), PdfView(), AiPanel())
        view, controller = tab.view, tab.controller
        view.set_annotation_controller(self._annotation_controller)
        view.highlight_color = self._settings.highlight_color
        view.set_reading_mode(self._settings.reading_mode)
        view.set_text_provider(controller.get_words)
        view.set_link_provider(controller.get_links)

        # 新标签沿用当前的 AI 配置（设置 / 密钥库 / 客户端工厂），保证各标签行为一致
        template: AiAssistant | None = self._tab.ai if self._tabs else None

        def current_page() -> int:
            return self._page_of(tab)

        def navigate(page: int, y: float | None) -> None:
            if tab is self._tab:
                self._navigate_to(page, y)

        def show_panel() -> None:
            if tab is self._tab:
                self._set_ai_panel_visible(True)

        def notify(message: str) -> None:
            if tab is self._tab:
                self.status_bar.show_message(message)

        def annotation_added(page: int) -> None:
            self._after_tab_annotation_change(tab, page)

        def start_ocr() -> None:
            if tab is self._tab:
                self._on_ocr_all_pages()

        tab.ai = AiAssistant(
            self,
            controller,
            tab.panel,
            current_page=current_page,
            navigate=navigate,
            show_panel=show_panel,
            notify=notify,
            on_annotation_added=annotation_added,
            start_ocr=start_ocr if OCR_AVAILABLE else None,
            show_marks=view.set_marks,
            glossary=self._glossary_for,
            store=template.store if template is not None else None,
            keystore=template.keystore if template is not None else None,
            client_factory=template.client_factory if template is not None else None,
        )
        if template is not None:
            tab.ai.adopt_settings(template.settings)
        tab.ai.settingsApplied.connect(lambda settings: self._share_ai_settings(tab, settings))
        tab.translator = SelectionTranslator(
            view,
            tab.ai,
            mode=self._settings.selection_translate,
            continuous=self._settings.translate_continuous,
            notify=notify,
            materials=self._materials,
            doc_info=lambda: self._doc_info(tab),
        )
        tab.translator.continuousChanged.connect(self._set_translate_continuous)
        tab.translator.materialsChanged.connect(self._refresh_materials)
        tab.translator.captureRequested.connect(self._for_tab(tab, self._start_translate_capture))
        self._wire_tab(tab)
        return tab

    def _for_tab(self, tab: DocumentTab, handler: Callable[..., object]) -> Callable[..., None]:
        """包一层：只有当前标签发出的信号才交给 handler（后台标签的一律忽略）。"""

        def slot(*args: object) -> None:
            if tab is self._tab:
                handler(*args)

        return slot

    def _wire_tab(self, tab: DocumentTab) -> None:
        controller, view, panel = tab.controller, tab.view, tab.panel
        controller.documentOpened.connect(lambda count: self._on_tab_opened(tab, count))
        controller.documentClosed.connect(lambda: self._on_tab_closed(tab))
        controller.openFailed.connect(self._on_open_failed)
        controller.undoStateChanged.connect(lambda: self._on_tab_undo_state(tab))

        on = self._for_tab
        view.renderRequested.connect(on(tab, self._render_page))
        view.pageChanged.connect(on(tab, self._on_page_changed))
        view.zoomChanged.connect(on(tab, lambda _z: self._update_zoom_label()))
        view.textCopied.connect(
            on(tab, lambda n: self.status_bar.show_message(f"已复制 {n} 个字符"))
        )
        view.linkActivated.connect(on(tab, self._on_link_activated))
        view.markupRequested.connect(on(tab, self._on_markup_requested))
        view.placementFinished.connect(on(tab, self._on_placement_finished))
        view.aiRequested.connect(on(tab, self._on_ai_requested))
        view.materialRequested.connect(on(tab, self._on_material_requested))
        view.areaCaptured.connect(on(tab, self._on_area_captured))
        # 批注提交由视图在松开鼠标后发出（拖拽时已实时预览）
        view.annotationHighlight.connect(on(tab, self._on_annot_highlight))
        view.annotationInk.connect(on(tab, self._on_annot_ink))
        view.annotationNote.connect(on(tab, self._on_annot_text))
        view.eraseRequested.connect(on(tab, self._on_erase))
        view.placementRequested.connect(on(tab, self._on_placement))
        panel.closeRequested.connect(on(tab, lambda: self._set_ai_panel_visible(False)))
        panel.captureRequested.connect(on(tab, self._start_capture))

    def _share_ai_settings(self, source: DocumentTab, settings: AiSettings) -> None:
        """某个标签保存了 AI 设置：同步给其他标签（设置是全局的）。"""
        for tab in (*self._tabs, *self._parked_tabs):
            if tab is not source:
                tab.ai.adopt_settings(settings)

    def _page_of(self, tab: DocumentTab) -> int:
        page = tab.view.current_page_num
        count = tab.controller.page_count
        if page is None or count == 0:
            return 0
        return min(max(page, 0), count - 1)

    # ------------------------------------------------------------------ 标签：切换
    def _activate(self, tab: DocumentTab, *, refresh: bool = True) -> None:
        """切到某个标签：先收拾共用界面上属于旧标签的东西，再用新标签的数据重新填充。"""
        old: DocumentTab | None = self._tab if self._tabs else None
        if tab is old:
            return
        if old is not None:
            self._leave_tab(old)
        if tab not in self._tabs:
            at = self._tabs.index(old) + 1 if old in self._tabs else len(self._tabs)
            self._tabs.insert(at, tab)
        self._tab = tab
        if self._stack.indexOf(tab.view) < 0:
            self._stack.addWidget(tab.view)
        if self._ai_dock.indexOf(tab.panel) < 0:
            self._ai_dock.addWidget(tab.panel)
        self._ai_dock.setCurrentWidget(tab.panel)
        if refresh and tab.has_document:
            self._show_tab(tab)
        self._sync_tab_bar()

    def _leave_tab(self, old: DocumentTab) -> None:
        """离开旧标签：取消搜索、收起只对它有意义的浮层与工具，释放它的渲染位图。"""
        if self._search_worker is not None:
            self._retire_search_worker(self._search_worker)
            self._search_worker = None
        self._active_query = ""
        self.search_panel.clear()
        self.search_panel.setVisible(False)
        self.find_next_action.setEnabled(False)
        self.find_prev_action.setEnabled(False)
        self.cite_popover.hide()
        self._pending_link = None
        if self._annotation_controller.mode != AnnotationMode.NONE:
            # 此时 pdf_view 仍是旧视图：模式切换的收尾（清选区 / 待放置）作用在它身上
            self._annotation_controller.set_mode(AnnotationMode.NONE)
        self.tool_hint.hide()
        old.view.clear_pending()
        old.view.clear_search_hit()
        old.view.settle_scroll()
        old.view.release_render_cache()

    def _show_tab(self, tab: DocumentTab) -> None:
        """切回一个已打开文档的标签：共用的侧边栏 / 功能区 / 状态栏按它重新填充。"""
        controller = tab.controller
        count = controller.page_count
        self._show_reader()
        self._stack.setCurrentWidget(tab.view)
        visible = self._settings.sidebar_visible
        self.sidebar_action.setChecked(visible)
        self.sidebar.setMaximumWidth(self._sidebar_width() if visible else 0)
        self.sidebar.setVisible(visible)
        self.sidebar.clear()
        self.sidebar.set_toc(controller.get_toc())
        self.sidebar.set_page_count(count)
        self._refresh_bookmarks()
        tab.annotations_stale = True

        self._set_document_state(True)
        self._update_title()
        self._update_undo_actions()
        self._update_zoom_label()
        self._update_page_actions()
        page = self._current_page()
        self.status_bar.set_page(page + 1, count)
        self.sidebar.set_current_page(page)
        self._page_group_title.setText(f"当前页 · 第 {page + 1} 页")
        self._refresh_annotations_if_shown()
        tab.ai.refresh_panel()
        tab.view.request_visible()
        tab.view.setFocus()

    def _switch_tab(self, step: int) -> None:
        """Ctrl+Tab / Ctrl+Shift+Tab：循环切换标签。"""
        tabs = [t for t in self._tabs if t.has_document]
        if len(tabs) < 2 or self._tab not in tabs:
            return
        index = (tabs.index(self._tab) + step) % len(tabs)
        self._activate(tabs[index])

    # ------------------------------------------------------------------ 标签：标签栏
    def _sync_tab_bar(self) -> None:
        """按 ``_tabs`` 重建标签栏（结构变化时调用；只改文字用 _refresh_tab_labels）。"""
        bar = self.tab_bar
        bar.blockSignals(True)
        try:
            while bar.count():
                bar.removeTab(bar.count() - 1)
            shown = [t for t in self._tabs if t.has_document]
            for tab in shown:
                index = bar.addTab(tab.label())
                path = tab.path
                bar.setTabToolTip(index, str(path) if path is not None else "")
            if self._tab in shown:
                bar.setCurrentIndex(shown.index(self._tab))
        finally:
            bar.blockSignals(False)
        bar.setVisible(bool(shown))
        self._tab_strip.setVisible(bool(shown))

    def _refresh_tab_labels(self) -> None:
        shown = [t for t in self._tabs if t.has_document]
        if len(shown) != self.tab_bar.count():
            self._sync_tab_bar()
            return
        for index, tab in enumerate(shown):
            if self.tab_bar.tabText(index) != tab.label():
                self.tab_bar.setTabText(index, tab.label())

    def _tab_at(self, index: int) -> DocumentTab | None:
        shown = [t for t in self._tabs if t.has_document]
        return shown[index] if 0 <= index < len(shown) else None

    def _on_tab_bar_current(self, index: int) -> None:
        tab = self._tab_at(index)
        if tab is not None:
            self._activate(tab)

    def _on_tab_bar_moved(self, src: int, dst: int) -> None:
        """拖动排序：标签栏只显示有文档的标签，与 _tabs 一一对应。"""
        shown = [t for t in self._tabs if t.has_document]
        if len(shown) != len(self._tabs) or not (0 <= src < len(shown) and 0 <= dst < len(shown)):
            self._sync_tab_bar()
            return
        self._tabs.insert(dst, self._tabs.pop(src))

    def _on_tab_close_requested(self, index: int) -> None:
        tab = self._tab_at(index)
        if tab is not None:
            self._close_tab(tab)

    def _on_close_other_tabs(self, index: int) -> None:
        keep = self._tab_at(index)
        if keep is None:
            return
        for tab in [t for t in self._tabs if t is not keep and t.has_document]:
            if not self._close_tab(tab):
                break  # 用户在「保存更改」里点了取消：剩下的不再关
        if keep in self._tabs:
            self._activate(keep)

    # ------------------------------------------------------------------ 标签：打开 / 关闭
    def _find_tab(self, path: Path) -> DocumentTab | None:
        return next((t for t in self._tabs if t.is_file(path)), None)

    def _on_tab_opened(self, tab: DocumentTab, page_count: int) -> None:
        """某个标签的控制器打开了文档：切到它，再走原来的「打开文档」流程。"""
        if self._shutting_down:
            return
        self._activate(tab, refresh=False)
        self._on_document_opened(page_count)

    def _on_tab_closed(self, tab: DocumentTab) -> None:
        """某个标签的文档关闭了：还有别的标签就移除它，最后一个则回到文献库。"""
        if self._shutting_down:
            return
        others = [t for t in self._tabs if t is not tab]
        if not others:
            if tab is self._tab:
                self._on_document_closed()
            return
        self._remove_tab(tab)

    def _close_tab(self, tab: DocumentTab) -> bool:
        """关闭一个标签（有未保存改动先切过去询问）。返回 False = 用户取消。"""
        if not tab.has_document:
            return True
        if tab.controller.is_modified:
            self._activate(tab)
            if not self._maybe_save():
                return False
        self._remember_reading_position(tab)
        tab.controller.close_document()  # → documentClosed → _on_tab_closed
        return True

    def _remove_tab(self, tab: DocumentTab) -> None:
        if tab is self._tab:
            index = self._tabs.index(tab)
            neighbor = (
                self._tabs[index + 1] if index + 1 < len(self._tabs) else self._tabs[index - 1]
            )
            self._activate(neighbor)
        if tab in self._tabs:
            self._tabs.remove(tab)
        self._dispose_tab(tab)
        self._sync_tab_bar()

    def _dispose_tab(self, tab: DocumentTab) -> None:
        """回收标签：停掉它的后台任务，销毁它的视图与面板。

        AI 请求线程若还没停下，整个标签先「停放」着（线程运行中销毁 QThread 会让进程崩溃），
        线程结束后再回收。
        """
        if tab.ocr_worker is not None:
            self._retire_ocr_worker(tab.ocr_worker)
            tab.ocr_worker = None
        if self._ocr_progress_owner is tab:
            self._close_ocr_progress()
        tab.ai.stop()
        tab.ai.clear_marks()
        tab.translator.reset()
        tab.view.clear()
        self._stack.removeWidget(tab.view)
        tab.view.deleteLater()
        self._ai_dock.removeWidget(tab.panel)
        tab.panel.hide()
        self._parked_tabs.append(tab)
        self._release_parked_tabs()

    def _release_parked_tabs(self) -> None:
        for tab in list(self._parked_tabs):
            if any(w.isRunning() for w in tab.ai.workers()) or tab.translator.busy():
                continue
            self._parked_tabs.remove(tab)
            tab.ai.deleteLater()
            tab.panel.deleteLater()

    def _on_tab_undo_state(self, tab: DocumentTab) -> None:
        self._refresh_tab_labels()
        if tab is self._tab:
            self._update_undo_actions()

    def _after_tab_annotation_change(self, tab: DocumentTab, page_num: int) -> None:
        """AI 给某个标签添加了批注：当前标签走常规刷新，后台标签只让缓存失效。"""
        if tab is self._tab:
            self._after_annotation_change(page_num)
            return
        tab.view.invalidate_page(page_num)
        tab.annotations_stale = True
        self._refresh_tab_labels()

    # ------------------------------------------------------------------ 窗口
    def _setup_window(self) -> None:
        self.setWindowTitle("Lumen")
        self.setWindowIcon(self._theme.app_icon())
        self.setMinimumSize(860, 560)
        w = max(860, self._settings.window_width)
        h = max(560, self._settings.window_height)
        self.resize(w, h)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------ 动作
    def _act(
        self,
        icon_name: str,
        text: str,
        slot: Callable[[], object] | None,
        *,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
        checkable: bool = False,
        enabled: bool = True,
        danger: bool = False,
        tooltip: str | None = None,
    ) -> QAction:
        act = QAction(text, self)
        act.setProperty("iconName", icon_name)
        act.setCheckable(checkable)
        act.setEnabled(enabled)
        if danger:
            act.setProperty("danger", True)
        if shortcut is not None:
            act.setShortcut(shortcut)
            tip = tooltip or text
            seq = act.shortcut().toString()
            act.setToolTip(f"{tip}  ({seq})" if seq else tip)
        elif tooltip:
            act.setToolTip(tooltip)
        if slot is not None:
            act.triggered.connect(lambda _checked=False, s=slot: s())
        # 让快捷键在无菜单栏时也生效
        self.addAction(act)
        return act

    def _create_actions(self) -> None:
        # 文件
        self.open_action = self._act(
            "open", "打开", self._on_open, shortcut=QKeySequence.StandardKey.Open
        )
        self.save_action = self._act(
            "save",
            "保存",
            self._on_save,
            shortcut=QKeySequence.StandardKey.Save,
            enabled=False,
            tooltip="保存到当前文件",
        )
        self.save_as_action = self._act(
            "save",
            "另存为",
            self._on_save_as,
            shortcut=QKeySequence.StandardKey.SaveAs,
            enabled=False,
        )
        self.close_doc_action = self._act(
            "close_doc",
            "关闭",
            self._on_close_document,
            shortcut=QKeySequence.StandardKey.Close,
            enabled=False,
        )
        self.print_action = self._act(
            "print",
            "打印",
            self._on_print,
            shortcut=QKeySequence.StandardKey.Print,
            enabled=False,
        )
        self.zotero_action = self._act(
            "zotero",
            "Zotero 批注",
            self._on_import_zotero,
            enabled=False,
            tooltip="把这份 PDF 在 Zotero 里的批注导入当前文档（可撤销）",
        )

        # 视图
        self.zoom_in_action = self._act(
            "zoom_in",
            "放大",
            self._zoom_in,
            shortcut=QKeySequence.StandardKey.ZoomIn,
            enabled=False,
        )
        self.zoom_out_action = self._act(
            "zoom_out",
            "缩小",
            self._zoom_out,
            shortcut=QKeySequence.StandardKey.ZoomOut,
            enabled=False,
        )
        self.fit_action = self._act(
            "fit_width",
            "适宽",
            self._fit_width,
            shortcut="Ctrl+0",
            enabled=False,
            tooltip="适应宽度",
        )
        self.fit_page_action = self._act(
            "fit_page", "适页", self._fit_page, shortcut="Ctrl+9", enabled=False, tooltip="整页显示"
        )
        self.actual_size_action = self._act(
            "actual_size",
            "原大",
            self._actual_size,
            shortcut="Ctrl+1",
            enabled=False,
            tooltip="实际大小 (100%)",
        )
        self.sidebar_action = self._act(
            "sidebar",
            "侧边栏",
            lambda: self._set_sidebar_visible(self.sidebar_action.isChecked()),
            shortcut="F4",
            checkable=True,
            enabled=False,
            tooltip="目录 / 批注 / 缩略图",
        )
        self.back_action = self._act(
            "back",
            "后退",
            self._go_back,
            shortcut="Alt+Left",
            enabled=False,
            tooltip="回到跳转前的位置",
        )
        self.forward_action = self._act(
            "forward", "前进", self._go_forward, shortcut="Alt+Right", enabled=False
        )
        self.bookmark_action = self._act(
            "bookmark",
            "书签",
            self._add_bookmark,
            shortcut="Ctrl+D",
            enabled=False,
            tooltip="把当前位置加为书签（侧栏「目录」页查看）",
        )
        self.reading_mode_action = self._act(
            "eye",
            "阅读模式",
            None,
            checkable=True,
            enabled=False,
            tooltip="正常 / 夜间（页面反色）/ 护眼（暖色纸张）",
        )

        # 编辑
        self.undo_action = self._act(
            "undo", "撤销", self._on_undo, shortcut=QKeySequence.StandardKey.Undo, enabled=False
        )
        self.redo_action = self._act(
            "redo", "重做", self._on_redo, shortcut=QKeySequence.StandardKey.Redo, enabled=False
        )
        self.delete_page_action = self._act(
            "delete_page", "删除页", self._on_delete_page, enabled=False, danger=True
        )
        self.rotate_action = self._act("rotate", "旋转", self._on_rotate_page, enabled=False)
        self.merge_action = self._act("merge", "合并", self._on_merge, enabled=False)
        self.watermark_action = self._act("watermark", "水印", self._on_watermark, enabled=False)

        # 批注（互斥可勾选）
        self._annot_group = QActionGroup(self)
        self._annot_group.setExclusive(True)
        self.select_action = self._annot_mode_action("select", "选择", AnnotationMode.NONE)
        self.select_action.setChecked(True)
        self.hand_action = self._annot_mode_action("hand", "抓手", AnnotationMode.PAN)
        self.highlight_action = self._annot_mode_action(
            "highlight", "高亮", AnnotationMode.HIGHLIGHT
        )
        self.ink_action = self._annot_mode_action("ink", "手写", AnnotationMode.INK)
        self.note_action = self._annot_mode_action("note", "便签", AnnotationMode.TEXT)
        self.freetext_action = self._annot_mode_action("textbox", "文本框", AnnotationMode.FREETEXT)
        self.freetext_action.setToolTip("单击页面放置文本框：文字直接显示在页面上")
        self.eraser_action = self._annot_mode_action("eraser", "橡皮擦", AnnotationMode.ERASER)
        self.underline_action = self._annot_mode_action(
            "underline", "下划线", AnnotationMode.UNDERLINE
        )
        self.underline_action.setToolTip("拖拽选字添加下划线（右键选中文字可选删除线 / 波浪线）")
        self.capture_action = self._annot_mode_action("capture", "框选提问", AnnotationMode.CAPTURE)
        self.capture_action.setToolTip("拖框选取公式 / 图表，交给 AI 解释")
        self.color_action = self._act(
            "chevron_down", "颜色", None, enabled=False, tooltip="高亮颜色"
        )
        # 签名 / 时间：与其他批注工具互斥（设计稿），选中后单击页面放置
        self.signature_action = self._act(
            "signature",
            "签名",
            self._on_signature,
            checkable=True,
            enabled=False,
            tooltip="插入签名（手绘 / 图片），单击页面放置",
        )
        self.timestamp_action = self._act(
            "stamp",
            "时间",
            self._on_timestamp,
            checkable=True,
            enabled=False,
            tooltip="插入当前日期时间，单击页面放置",
        )
        self._annot_group.addAction(self.signature_action)
        self._annot_group.addAction(self.timestamp_action)
        self._mode_to_action = {
            AnnotationMode.NONE: self.select_action,
            AnnotationMode.PAN: self.hand_action,
            AnnotationMode.HIGHLIGHT: self.highlight_action,
            AnnotationMode.UNDERLINE: self.underline_action,
            AnnotationMode.INK: self.ink_action,
            AnnotationMode.TEXT: self.note_action,
            AnnotationMode.FREETEXT: self.freetext_action,
            AnnotationMode.ERASER: self.eraser_action,
            AnnotationMode.SIGNATURE: self.signature_action,
            AnnotationMode.TIMESTAMP: self.timestamp_action,
            AnnotationMode.CAPTURE: self.capture_action,
        }

        # 工具
        self.ocr_action = self._act(
            "ocr",
            "OCR",
            None,
            enabled=False,
            tooltip="OCR 文字识别：当前页 / 全部页",
        )
        self.export_action = self._act(
            "export", "导出", self._on_export, enabled=False, tooltip="导出 / 转换格式"
        )
        self.export_notes_action = self._act(
            "notes",
            "导出笔记",
            self._on_export_notes,
            enabled=False,
            tooltip="把全部批注导出为 Markdown 笔记",
        )
        self.doc_info_action = self._act("docinfo", "文档信息", self._on_doc_info, enabled=False)
        self.search_action = self._act(
            "search", "搜索", self._on_find, shortcut=QKeySequence.StandardKey.Find, enabled=False
        )
        self.find_next_action = self._act(
            "next", "下一个", lambda: self._step_search(1), shortcut="F3", enabled=False
        )
        self.find_prev_action = self._act(
            "prev", "上一个", lambda: self._step_search(-1), shortcut="Shift+F3", enabled=False
        )
        self.copy_merge_action = self._act(
            "copy",
            "去换行复制",
            self._copy_merged,
            shortcut="Ctrl+Shift+C",
            enabled=False,
            tooltip="复制选中文字并合并 PDF 排版换行与断词（便于粘贴到翻译 / 笔记）",
        )
        self.translate_action = self._act(
            "translate", "翻译", self._on_translate, enabled=False, tooltip="AI 翻译选中的文字"
        )
        self.selection_translate_action = self._act(
            "select",
            "划词翻译",
            None,
            tooltip="选中文字后：在选区旁显示「译」按钮 / 选中即翻译 / 关闭",
        )
        self.translate_continuous_action = self._act(
            "chain",
            "连翻",
            lambda: self._set_translate_continuous(self.translate_continuous_action.isChecked()),
            shortcut="Ctrl+Shift+L",
            checkable=True,
            tooltip="接着选的文字拼在一起翻译（跨栏 / 跨页断开的句子）",
        )
        self.translate_continuous_action.setChecked(self._settings.translate_continuous)

        # AI 助读
        self.ai_panel_action = self._act(
            "ai",
            "AI 面板",
            lambda: self._set_ai_panel_visible(self.ai_panel_action.isChecked()),
            shortcut="Ctrl+J",
            checkable=True,
            tooltip="打开 / 关闭 AI 助读面板",
        )
        self.ai_summary_action = self._act(
            "list",
            "总结全文",
            lambda: self._ai.summarize(),
            enabled=False,
            tooltip="结构化总结（研究问题 / 方法 / 结果 / 局限），要点带页码可跳转",
        )
        self.ai_tldr_action = self._act(
            "doc",
            "TL;DR",
            lambda: self._ai.tldr(),
            enabled=False,
            tooltip="三句话以内的全文速览，每句带页码",
        )
        self.ai_speedread_action = self._act(
            "eye",
            "速读高亮",
            lambda: self._ai.speed_read(),
            enabled=False,
            tooltip="让 AI 标出当前页最值得读的句子（不改文件，可再存为高亮）",
        )
        self.ai_suggest_action = self._act(
            "question",
            "推荐问题",
            lambda: self._ai.suggest(),
            enabled=False,
            tooltip="让 AI 提出值得问的问题，点击即可提问",
        )
        self.ai_explain_action = self._act(
            "info", "解释", self._on_ai_explain, enabled=False, tooltip="AI 解释选中的文字"
        )
        self.ai_settings_action = self._act(
            "settings",
            "模型设置",
            lambda: self._ai.open_settings(),
            tooltip="配置 OpenAI 兼容的模型服务（DeepSeek / 通义 / Kimi / Ollama …）",
        )

        # 标签切换（不上功能区，只用快捷键）
        self.next_tab_action = self._act(
            "next", "下一个标签", lambda: self._switch_tab(1), shortcut="Ctrl+Tab"
        )
        self.prev_tab_action = self._act(
            "prev", "上一个标签", lambda: self._switch_tab(-1), shortcut="Ctrl+Shift+Tab"
        )

        # 关于
        self.about_action = self._act("info", "关于", self._on_about, tooltip="关于 PDF Reader")

    def _annot_mode_action(self, icon_name: str, text: str, mode: str) -> QAction:
        act = self._act(
            icon_name,
            text,
            lambda: self._annotation_controller.set_mode(mode),
            checkable=True,
            enabled=False,
        )
        self._annot_group.addAction(act)
        return act

    # ------------------------------------------------------------------ 布局
    def _setup_ui(self) -> None:
        # 外层：文献库首页 ⇄ 阅读器（功能区 + 标签栏 + 内容区 + 状态栏）
        self._shell = QStackedWidget()
        self._shell.setObjectName("Shell")
        self.library = LibraryHome(self._theme)
        self.library.set_view_mode(self._settings.library_view)
        self._shell.addWidget(self.library)
        root = QWidget()
        root.setObjectName("MainRoot")
        self._reader = root
        self._shell.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏 + 功能区（设计稿分组；另存为 / 关闭补进「文件」组，保证有可见入口）
        self.ribbon = Ribbon(self._theme)
        self.ribbon.add_tab(
            "开始",
            [
                (
                    "文件",
                    [
                        self.open_action,
                        self.save_action,
                        self.save_as_action,
                        self.print_action,
                        self.zotero_action,
                        self.close_doc_action,
                    ],
                ),
                ("编辑", [self.undo_action, self.redo_action]),
                ("工具", [self.select_action, self.hand_action]),
                ("查找与复制", [self.search_action, self.copy_merge_action, self.translate_action]),
            ],
        )
        self.ribbon.add_tab(
            "批注",
            [
                ("文字标记", [self.highlight_action, self.color_action, self.underline_action]),
                (
                    "绘制",
                    [self.ink_action, self.note_action, self.freetext_action, self.eraser_action],
                ),
                ("插入", [self.signature_action, self.timestamp_action]),
            ],
        )
        page_titles = self.ribbon.add_tab(
            "页面",
            [
                (
                    "当前页 · 第 1 页",
                    [
                        self.delete_page_action,
                        self.rotate_action,
                        self.merge_action,
                        self.watermark_action,
                    ],
                ),
            ],
        )
        self._page_group_title = page_titles[0]
        self.ribbon.add_tab(
            "视图",
            [
                (
                    "布局",
                    [
                        self.sidebar_action,
                        self.back_action,
                        self.forward_action,
                        self.bookmark_action,
                    ],
                ),
                (
                    "缩放",
                    [
                        self.zoom_in_action,
                        self.zoom_out_action,
                        self.fit_action,
                        self.fit_page_action,
                    ],
                ),
                ("阅读", [self.reading_mode_action]),
            ],
        )
        self.ribbon.add_tab(
            "工具",
            [
                ("识别", [self.ocr_action]),
                ("输出", [self.export_action, self.export_notes_action]),
                ("文档", [self.doc_info_action]),
            ],
        )
        self.ribbon.add_tab(
            "AI 助读",
            [
                ("全文", [self.ai_panel_action, self.ai_summary_action, self.ai_suggest_action]),
                ("速读", [self.ai_tldr_action, self.ai_speedread_action]),
                (
                    "划词与框选",
                    [
                        self.translate_action,
                        self.selection_translate_action,
                        self.translate_continuous_action,
                        self.ai_explain_action,
                        self.capture_action,
                    ],
                ),
                ("模型", [self.ai_settings_action]),
            ],
        )
        self.ribbon.finish()
        layout.addWidget(self.ribbon)

        # 下拉：OCR 范围 / 阅读模式（带 ✓ 与色点）/ 高亮颜色色板
        ocr_menu = QMenu(self)
        ocr_menu.addAction("当前页", self._on_ocr_current_page)
        ocr_menu.addAction("全部页", self._on_ocr_all_pages)
        self.ribbon.set_menu(self.ocr_action, ocr_menu)
        self._mode_menu = QMenu(self)
        self._mode_actions: dict[str, QAction] = {}
        for mode, label, dot in _READING_MODES:
            act = self._mode_menu.addAction(label, self._mode_setter(mode))
            act.setCheckable(True)
            act.setIcon(_dot_icon(dot))
            self._mode_actions[mode] = act
        self.ribbon.set_menu(self.reading_mode_action, self._mode_menu)
        selection_menu = QMenu(self)
        self._selection_translate_actions: dict[str, QAction] = {}
        for mode, label in (
            ("button", "选中后显示「译」按钮"),
            ("auto", "选中即翻译"),
            ("off", "关闭"),
        ):
            act = selection_menu.addAction(label, lambda m=mode: self._set_selection_translate(m))
            act.setCheckable(True)
            act.setChecked(mode == self._settings.selection_translate)
            self._selection_translate_actions[mode] = act
        selection_menu.addSeparator()
        self.glossary_action = selection_menu.addAction("翻译时使用术语库", self._toggle_glossary)
        self.glossary_action.setCheckable(True)
        self.glossary_action.setChecked(self._settings.translate_use_glossary)
        self.glossary_action.setToolTip("原文出现术语库里的术语时，按你填写的译名翻译")
        self.ribbon.set_menu(self.selection_translate_action, selection_menu)
        self.ribbon.set_menu(self.color_action, self._build_swatch_menu())

        # 文档标签栏（有文档时显示）+ 右侧「+」打开更多文档
        self.tab_bar = DocumentTabBar()
        self.tab_bar.setVisible(False)
        self._tab_strip = QWidget()
        self._tab_strip.setObjectName("DocTabStrip")
        self._tab_strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        strip = QHBoxLayout(self._tab_strip)
        strip.setContentsMargins(8, 4, 8, 0)
        strip.setSpacing(2)
        strip.addWidget(self.tab_bar, 0, Qt.AlignmentFlag.AlignBottom)
        self._tab_add_button = QToolButton()
        self._tab_add_button.setObjectName("DocTabAdd")
        self._tab_add_button.setToolTip("打开 PDF  (Ctrl+O)")
        self._tab_add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tab_add_button.clicked.connect(self._on_open)
        strip.addWidget(self._tab_add_button, 0, Qt.AlignmentFlag.AlignVCenter)
        strip.addStretch(1)
        self._tab_strip.setVisible(False)
        layout.addWidget(self._tab_strip)
        self._refresh_tab_icons()

        # 内容区：侧边栏（宽度动画）| 阅读区
        self._content = _ContentArea()
        content_layout = QHBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.sidebar = SidePanel()
        self.sidebar.setMaximumWidth(0)
        self.sidebar.setVisible(False)
        # 阅读区：每个标签自己的阅读视图（切换标签 = 切换显示哪一个）
        self._stack = QStackedWidget()
        content_layout.addWidget(self.sidebar)
        content_layout.addWidget(self._stack, stretch=1)
        # AI 面板区：每个标签自己的 AI 面板（对话互不相干），整体做宽度动画
        self._ai_dock = QStackedWidget()
        self._ai_dock.setObjectName("AiDock")
        self._ai_dock.setMaximumWidth(0)
        self._ai_dock.setVisible(False)
        content_layout.addWidget(self._ai_dock)
        layout.addWidget(self._content, stretch=1)
        # 两侧栏内侧边缘的宽度拖动条（浮在面板上，不占布局）
        self.sidebar.set_panel_width(self._sidebar_width())
        self._sidebar_grip = PanelGrip(self.sidebar, "left", self._resize_sidebar, self._content)
        self._sidebar_grip.released.connect(self._save_settings)
        self._sidebar_grip.resetRequested.connect(lambda: self._reset_panel_width("sidebar"))
        self._ai_grip = PanelGrip(self._ai_dock, "right", self._resize_ai_panel, self._content)
        self._ai_grip.released.connect(self._save_settings)
        self._ai_grip.resetRequested.connect(lambda: self._reset_panel_width("ai"))

        # 浮层：搜索卡片、工具提示胶囊、参考文献预览卡
        self.search_panel = SearchPanel("搜索", self._content)
        self.search_panel.searchRequested.connect(self._on_search_requested)
        self.search_panel.resultClicked.connect(self._on_search_result_clicked)
        self.search_panel.closed.connect(lambda: self.pdf_view.clear_search_hit())
        self.search_panel.closed.connect(lambda: self.pdf_view.setFocus())
        self._content.set_overlay(self.search_panel)
        self.tool_hint = ToolHint(self._content)
        self._content.tool_hint = self.tool_hint
        self._content.reading_area = self._stack
        self.cite_popover = CitePopover(self)
        self.cite_popover.jumpRequested.connect(self._on_cite_jump)

        # 状态栏
        self.status_bar = StatusBar(self._theme)
        layout.addWidget(self.status_bar)

        self.setCentralWidget(self._shell)

    def _mode_setter(self, mode: str) -> Callable[[], None]:
        return lambda: self._set_reading_mode(mode)

    def _build_swatch_menu(self) -> QMenu:
        """高亮颜色色板（设计稿：一排 5 个圆点，当前色带强调色外环）。"""
        menu = QMenu(self)
        holder = QWidget()
        holder.setObjectName("SwatchPopup")
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        self._swatches: dict[str, QToolButton] = {}
        for name, color in _HIGHLIGHT_COLORS:
            btn = QToolButton()
            btn.setObjectName("Swatch")
            btn.setToolTip(f"高亮颜色：{name}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"QToolButton#Swatch {{ background-color: {color}; }}")
            btn.clicked.connect(self._swatch_handler(menu, color, name))
            row.addWidget(btn)
            self._swatches[color] = btn
        action = QWidgetAction(menu)
        action.setDefaultWidget(holder)
        menu.addAction(action)
        self._refresh_swatches()
        return menu

    def _swatch_handler(self, menu: QMenu, color: str, name: str) -> Callable[[], None]:
        def handler() -> None:
            menu.close()
            self._set_highlight_color(color, name)

        return handler

    def _refresh_swatches(self) -> None:
        current = self._settings.highlight_color.upper()
        for color, btn in getattr(self, "_swatches", {}).items():
            btn.setProperty("current", "true" if color.upper() == current else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ------------------------------------------------------------------ 信号
    def _connect_signals(self) -> None:
        # 每个标签自己的控制器 / 视图 / AI 面板信号在 _wire_tab 里接
        lib = self.library
        lib.openRequested.connect(self._on_open)
        lib.openRecentRequested.connect(self._on_open_recent)
        lib.favoriteToggled.connect(self._on_favorite_toggled)
        lib.themeToggleRequested.connect(self._toggle_theme)
        lib.aboutRequested.connect(self._on_about)
        lib.exploreAiRequested.connect(self._on_explore_ai)
        lib.returnRequested.connect(self._return_to_reader)
        lib.viewModeChanged.connect(self._on_library_view_changed)
        self.ribbon.homeRequested.connect(self._go_home)

        self.tab_bar.currentChanged.connect(self._on_tab_bar_current)
        self.tab_bar.tabMoved.connect(self._on_tab_bar_moved)
        self.tab_bar.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_bar.closeOthersRequested.connect(self._on_close_other_tabs)

        self.status_bar.prevRequested.connect(self._prev_page)
        self.status_bar.nextRequested.connect(self._next_page)
        self.status_bar.pageEntered.connect(self._goto_page)
        self.status_bar.zoomInRequested.connect(self._zoom_in)
        self.status_bar.zoomOutRequested.connect(self._zoom_out)
        self.status_bar.fitRequested.connect(self._fit_width)
        self.status_bar.zoomPercentRequested.connect(self._on_zoom_percent)
        self.ribbon.themeToggleRequested.connect(self._toggle_theme)

        self._annotation_controller.modeChanged.connect(self._on_annot_mode_changed)

        # 侧边栏
        self.sidebar.tocActivated.connect(lambda page, y: self._navigate_to(page, y))
        self.sidebar.bookmarkActivated.connect(self._go_to_bookmark)
        self.sidebar.bookmarkAddRequested.connect(self._add_bookmark)
        self.sidebar.bookmarkRenameRequested.connect(self._rename_bookmark)
        self.sidebar.bookmarkDeleteRequested.connect(self._delete_bookmark)
        self.sidebar.thumbnailActivated.connect(lambda page: self._navigate_to(page, None))
        self.sidebar.thumbnailsNeeded.connect(self._render_thumbnails)
        self.sidebar.annotationActivated.connect(self._on_annotation_activated)
        self.sidebar.annotationEditRequested.connect(self._on_annotation_edit)
        self.sidebar.annotationDeleteRequested.connect(self._on_annotation_delete)
        self.sidebar.exportNotesRequested.connect(self._on_export_notes)
        self.sidebar.materialKindChanged.connect(lambda _kind: self._refresh_materials())
        self.sidebar.materialActivated.connect(self._on_material_activated)
        self.sidebar.materialEditRequested.connect(self._on_material_edit)
        self.sidebar.materialDeleteRequested.connect(self._on_material_delete)
        self.sidebar.materialsExportRequested.connect(self._on_materials_export)
        self.sidebar.tabs.currentChanged.connect(self._on_sidebar_tab)
        self.sidebar.tabs.currentChanged.connect(lambda _i: self._refresh_annotations_if_shown())

    # ------------------------------------------------------------------ 主题
    def _on_theme_mode(self, mode: str) -> None:
        self._theme.set_mode(mode)

    def _toggle_theme(self) -> None:
        """标题栏的浅 / 深色切换（设计稿只有这一个开关）。"""
        self._theme.set_mode("light" if self._theme.is_dark else "dark")

    def _refresh_tab_icons(self) -> None:
        p = self._theme.palette
        self.tab_bar.set_tab_icon(self._theme.colored_icon("file", p.text_muted, size=13))
        self._tab_add_button.setIcon(self._theme.colored_icon("plus", p.text_muted, size=14))

    def _on_theme_changed(self, _name: str) -> None:
        for tab in self._tabs:
            tab.view.apply_palette(self._theme.palette)
        self._refresh_tab_icons()
        self.setWindowIcon(self._theme.app_icon())
        # 持久化"模式"（含 system），而非解析后的具体配色
        self._settings.theme = self._theme.mode
        self._save_settings()

    # ------------------------------------------------------------------ 缩放 / 翻页
    def _update_zoom_label(self) -> None:
        self.status_bar.set_zoom(self.pdf_view.zoom_percent)

    def _zoom_in(self) -> None:
        self.pdf_view.zoom_in()

    def _zoom_out(self) -> None:
        self.pdf_view.zoom_out()

    def _fit_width(self) -> None:
        self.pdf_view.fit_to_width()

    def _fit_page(self) -> None:
        self.pdf_view.fit_to_page()

    def _actual_size(self) -> None:
        self.pdf_view.set_actual_size()

    def _on_zoom_percent(self, percent: int) -> None:
        """状态栏缩放滑杆（百分比，100% = 实际大小）。"""
        self.pdf_view.set_zoom_percent(percent)
        self.status_bar.show_message(f"缩放 {self.pdf_view.zoom_percent}%")

    def _prev_page(self) -> None:
        self.pdf_view.jump_to_page(max(0, self._current_page() - 1))

    def _next_page(self) -> None:
        self.pdf_view.jump_to_page(min(self._controller.page_count - 1, self._current_page() + 1))

    def _goto_page(self, page_1based: int) -> None:
        if 1 <= page_1based <= self._controller.page_count:
            self._navigate_to(page_1based - 1, None)
        else:
            self.status_bar.show_message(f"页码超出范围（1–{self._controller.page_count}）")

    def _on_page_changed(self, page_num: int) -> None:
        self.status_bar.set_page(page_num + 1, self._controller.page_count)
        self.sidebar.set_current_page(page_num)
        self._page_group_title.setText(f"当前页 · 第 {page_num + 1} 页")

    def _current_page(self) -> int:
        """用户正在看的页（视图维护；无文档时为 0）。"""
        return self._page_of(self._tab)

    # ------------------------------------------------------------------ 书签
    def _bookmarks(self) -> list[Bookmark]:
        doc = self._controller.document
        if doc is None:
            return []
        count = self._controller.page_count
        return [b for b in self._settings.bookmarks_for(doc.path) if 0 <= b.page < count]

    def _store_bookmarks(self, marks: list[Bookmark]) -> None:
        doc = self._controller.document
        if doc is None:
            return
        self._settings.set_bookmarks(doc.path, marks)
        self._save_settings()
        self._refresh_bookmarks()

    def _refresh_bookmarks(self) -> None:
        marks = sorted(self._bookmarks(), key=lambda b: (b.page, b.offset))
        self.sidebar.set_bookmarks(marks)

    def _add_bookmark(self) -> None:
        """把视口顶边所在位置加为书签；标题取该处的一行原文（没有文字则「第 N 页」）。"""
        if not self._controller.has_document or self.pdf_view.page_count == 0:
            return
        page, offset = self.pdf_view.reading_position()
        marks = self._bookmarks()
        if any(b.page == page and abs(b.offset - offset) < 0.05 for b in marks):
            self.status_bar.show_message(f"第 {page + 1} 页这里已经有书签了")
            return
        bounds = self._controller.get_page_bounds()
        y = offset * bounds[page].height if 0 <= page < len(bounds) else None
        excerpt = self._controller.text_excerpt(page, y, max_chars=60).strip()
        title = excerpt.splitlines()[0].strip()[:40] if excerpt else ""
        marks.append(Bookmark(page, round(offset, 4), title or f"第 {page + 1} 页"))
        self._store_bookmarks(marks)
        self.sidebar.tabs.setCurrentIndex(SidePanel.TAB_TOC)
        if not self.sidebar_action.isChecked():
            self.sidebar_action.trigger()
        self.status_bar.show_message(f"已添加书签：第 {page + 1} 页 · 右键可重命名")

    def _go_to_bookmark(self, mark: Bookmark) -> None:
        if not 0 <= mark.page < self._controller.page_count:
            return
        self._back_stack.append(self.pdf_view.reading_position())
        del self._back_stack[:-_HISTORY_LIMIT]
        self._forward_stack.clear()
        self.pdf_view.restore_position(mark.page, mark.offset, animate=True)
        self.pdf_view.pulse_page(mark.page)
        self._update_history_actions()

    def _rename_bookmark(self, index: int) -> None:
        marks = self.sidebar.bookmarks()
        if not 0 <= index < len(marks):
            return
        old = marks[index]
        text, ok = QInputDialog.getText(self, "重命名书签", "书签名称：", text=old.title)
        if ok and text.strip():
            marks[index] = Bookmark(old.page, old.offset, text.strip())
            self._store_bookmarks(marks)

    def _delete_bookmark(self, index: int) -> None:
        marks = self.sidebar.bookmarks()
        if 0 <= index < len(marks):
            removed = marks.pop(index)
            self._store_bookmarks(marks)
            self.status_bar.show_message(f"已删除书签：{removed.title}")

    # ------------------------------------------------------------------ 导航历史
    def _navigate_to(self, page_num: int, y: float | None) -> None:
        """跳转并记入历史，之后可用「后退」(Alt+←) 回到原处——看完参考文献再回正文。"""
        if not 0 <= page_num < self._controller.page_count:
            return
        self._back_stack.append(self.pdf_view.reading_position())
        del self._back_stack[:-_HISTORY_LIMIT]
        self._forward_stack.clear()
        self.pdf_view.jump_to_page(page_num, y, animate=True)
        self.pdf_view.pulse_page(page_num)
        self._update_history_actions()
        self.status_bar.show_message(f"已跳转到第 {page_num + 1} 页")

    def _go_back(self) -> None:
        if not self._back_stack:
            return
        self._forward_stack.append(self.pdf_view.reading_position())
        page, offset = self._back_stack.pop()
        self.pdf_view.restore_position(page, offset, animate=True)
        self._update_history_actions()

    def _go_forward(self) -> None:
        if not self._forward_stack:
            return
        self._back_stack.append(self.pdf_view.reading_position())
        page, offset = self._forward_stack.pop()
        self.pdf_view.restore_position(page, offset, animate=True)
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        active = self._controller.has_document
        self.back_action.setEnabled(active and bool(self._back_stack))
        self.forward_action.setEnabled(active and bool(self._forward_stack))

    def _on_link_activated(self, page_num: int, link: LinkInfo) -> None:
        """单击正文链接。

        内部链接（参考文献 / 图表 / 章节）先弹预览卡展示目标处原文，点「跳转到条目 →」再跳
        （设计稿引用弹层）；外部链接确认后用浏览器打开，仅允许 http / https / mailto。
        """
        if link.is_internal:
            anchor = self._controller.text_in_rect(page_num, link.rect).strip()
            match = _CITATION_RE.match(anchor)
            title = f"参考文献 [{match.group(1)}]" if match else f"链接 · 第 {link.page_num + 1} 页"
            excerpt = self._controller.text_excerpt(link.page_num, link.y)
            self._pending_link = (link.page_num, link.y)
            self.cite_popover.show_at(QCursor.pos(), title, excerpt)
            return
        url = QUrl(link.uri)
        if url.scheme().lower() not in _SAFE_SCHEMES:
            self.status_bar.show_message(f"已阻止打开不安全的链接：{link.uri[:80]}")
            return
        reply = QMessageBox.question(
            self, "打开外部链接", f"要在浏览器中打开以下链接吗？\n\n{link.uri}"
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

    def _on_cite_jump(self) -> None:
        self.pdf_view.setFocus()
        if self._pending_link is None:
            return
        page, y = self._pending_link
        self._pending_link = None
        self._navigate_to(page, y)
        self.status_bar.show_message(f"已跳转到第 {page + 1} 页（Alt+← 返回）")

    # ------------------------------------------------------------------ 搜索
    def _on_find(self) -> None:
        if not self._controller.has_document:
            return
        self.search_panel.focus_input()
        self._content.reposition_overlay()

    def _on_search_requested(self, query: str) -> None:
        if not self._controller.has_document:
            return
        self.pdf_view.clear_search_hit()  # 新一轮搜索：旧命中高亮作废
        source = self._worker_source()
        if source is None:
            self.search_panel.show_error("无法生成文档快照，请先保存文档")
            return
        if self._search_worker is not None:
            self._retire_search_worker(self._search_worker)
            self._search_worker = None
        worker = SearchWorker(self)
        worker.searchCompleted.connect(self._on_search_completed)
        worker.searchFailed.connect(self._on_search_failed)
        worker.finished.connect(self._on_search_worker_finished)
        worker.setup(source, query, password=self._controller.password)
        self._active_query = query
        self._search_worker = worker
        worker.start()

    def _on_search_completed(self, query: str, result: SearchResult) -> None:
        sender = self.sender()
        if sender is not None and sender is not self._search_worker:
            return  # 已被新一轮搜索取代的旧线程
        if query != self._active_query:
            return  # 陈旧查询的结果（慢文档上先发后到会覆盖新结果）
        self.search_panel.show_results(query, result.hits)
        has_hits = bool(result.hits)
        self.find_next_action.setEnabled(has_hits)
        self.find_prev_action.setEnabled(has_hits)

    def _on_search_failed(self, message: str) -> None:
        sender = self.sender()
        if sender is not None and sender is not self._search_worker:
            return
        self.search_panel.show_error(message)

    def _step_search(self, step: int) -> None:
        """F3 / Shift+F3：在搜索结果间前后移动并定位。"""
        if not self.search_panel.select_relative(step):
            self.status_bar.show_message("没有搜索结果")

    def _retire_search_worker(self, worker: SearchWorker) -> None:
        """取消并回收一个搜索线程：断开信号 + 请求取消，不长时间阻塞 GUI。"""
        for signal in (worker.searchCompleted, worker.searchFailed):
            # 没有任何连接时 PySide6 会抛 RuntimeError / SystemError，这里都忽略
            with contextlib.suppress(Exception):
                signal.disconnect()
        worker.shutdown()
        if worker.isRunning():
            worker.wait(300)  # 取消在下一个页边界生效，通常远快于此
        if worker.isRunning():
            # 还没停：交给 finished 收尾（保持 Python 引用，避免 QThread 运行中被回收）
            self._retired_search_workers.append(worker)
            worker.finished.connect(self._on_search_worker_finished)
        else:
            worker.deleteLater()

    def _on_search_worker_finished(self) -> None:
        """搜索线程收尾：清引用并交给 Qt 删除（否则连续搜索会累积线程对象）。"""
        worker = self.sender()
        if worker is self._search_worker:
            self._search_worker = None
        if isinstance(worker, SearchWorker):
            with contextlib.suppress(ValueError):
                self._retired_search_workers.remove(worker)
            worker.deleteLater()

    def _on_search_result_clicked(self, page_num: int, rect: Rect) -> None:
        """点击搜索结果：跳到该页并把命中的矩形高亮出来（记入导航历史）。"""
        self._back_stack.append(self.pdf_view.reading_position())
        del self._back_stack[:-_HISTORY_LIMIT]
        self._forward_stack.clear()
        self.pdf_view.show_search_hit(page_num, rect)
        self._update_history_actions()
        self.status_bar.show_message(f"第 {page_num + 1} 页")

    # ------------------------------------------------------------------ 后台快照
    def _worker_source(self) -> Path | None:
        """后台线程（搜索 / OCR / 导出）使用的源路径。

        有未保存改动时先把内存文档存为临时快照（唯一文件名，避免上一份仍被后台线程
        打开时写入失败）；快照失败返回 None，绝不退回磁盘上的过时内容。
        """
        doc = self._controller.document
        if doc is None:
            return None
        if not doc.is_modified:
            return doc.path
        tmp = self._new_snapshot_path()
        # 整份写出大文档要几秒且同步跑在 GUI 线程：给用户一个忙碌光标
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            copied = self._controller.save_copy(tmp)
        finally:
            QApplication.restoreOverrideCursor()
        if not copied:
            self.status_bar.show_message(
                f"生成临时快照失败（{self._controller.last_error or '未知原因'}），请先保存文档"
            )
            return None
        self._snapshot_paths.append(tmp)
        self._discard_stale_snapshots(keep=tmp)
        return tmp

    def _new_snapshot_path(self) -> Path:
        """快照目录下的一个唯一路径（不预先创建文件）。"""
        with contextlib.suppress(OSError):
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        while True:
            candidate = self._snapshot_dir / f"snapshot_{uuid4().hex[:12]}.pdf"
            if not candidate.exists():
                return candidate

    def _discard_stale_snapshots(self, *, keep: Path) -> None:
        """清理不再使用的旧快照（仍被后台线程打开时删除失败，忽略即可）。"""
        for old in [p for p in self._snapshot_paths if p != keep]:
            with contextlib.suppress(OSError):
                old.unlink()
                self._snapshot_paths.remove(old)

    def _cleanup_snapshots(self) -> None:
        """删除本进程创建的快照文件；目录空了才删目录。

        只删自己登记过的快照：旧实现 ``rmtree`` 整个目录，用户若把导出文件存进了
        这里（导出对话框曾默认指向快照目录）会被一并删除。
        """
        for path in [*self._snapshot_paths, *self._temp_files]:
            with contextlib.suppress(OSError):
                path.unlink()
        self._snapshot_paths.clear()
        self._temp_files.clear()
        with contextlib.suppress(OSError):
            self._snapshot_dir.rmdir()  # 非空（含用户文件）时失败，保留

    # ------------------------------------------------------------------ 编辑
    def _report_failure(self, what: str) -> None:
        """把控制器记录的失败原因展示给用户（不静默）。"""
        reason = self._controller.last_error or "未知原因"
        self.status_bar.show_message(f"{what}失败：{reason}")
        QMessageBox.warning(self, f"{what}失败", reason)

    def _on_delete_page(self) -> None:
        page = self._current_page()
        if self._controller.page_count <= 1:
            QMessageBox.information(self, "删除页", "不能删除文档中唯一的一页。")
            return
        reply = QMessageBox.question(self, "删除页", f"确定删除第 {page + 1} 页吗？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        position = self.pdf_view.reading_position()
        if self._controller.delete_page(page):
            new_page = min(page, self._controller.page_count - 1)
            self._refresh_after_edit(keep=(new_page, 0.0 if new_page != page else position[1]))
            self.status_bar.show_message(f"已删除第 {page + 1} 页")
        else:
            self._report_failure("删除页")

    def _on_rotate_page(self) -> None:
        page = self._current_page()
        if self._controller.rotate_page(page, 90):
            self._refresh_after_edit(keep=(page, 0.0))
        else:
            self._report_failure("旋转页")

    def _on_merge(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择要合并的 PDF", "", _PDF_FILTER)
        if not path:
            return
        added = self._controller.merge(Path(path))
        if added > 0:
            self._refresh_after_edit()
            if self._controller.can_undo():
                self.status_bar.show_message(f"已合并 {added} 页")
            else:
                # 合并会重建底层文档：撤销历史随之失效，明确告知而不是留个点不动的按钮
                self.status_bar.show_message(f"已合并 {added} 页（合并后不可撤销）")
        else:
            self._report_failure("合并")

    def _on_watermark(self) -> None:
        text, ok = QInputDialog.getText(self, "添加水印", "水印文字：")
        if not ok or not text.strip():
            return
        scopes = ["仅当前页", "全部页"]
        scope, ok = QInputDialog.getItem(self, "添加水印", "应用范围：", scopes, 0, False)
        if not ok:
            return
        page = self._current_page()
        target: int | list[int] = (
            list(range(self._controller.page_count)) if scope == "全部页" else page
        )
        if self._controller.add_watermark(target, text.strip()):
            self._refresh_after_edit()
        else:
            self._report_failure("添加水印")

    # ------------------------------------------------------------------ 保存
    def _on_save(self) -> None:
        self._do_save()

    def _do_save(self) -> bool:
        """保存到当前文件（增量）；失败时先说明原因，再转为另存为。返回是否保存成功。"""
        if self._controller.document is None:
            return False
        if self._controller.save_in_place():
            self._after_saved()
            return True
        reason = self._controller.last_error or "未知原因"
        QMessageBox.warning(self, "无法保存到原文件", f"{reason}\n\n接下来请选择另存位置。")
        return self._on_save_as()

    def _on_save_as(self) -> bool:
        doc = self._controller.document
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", str(doc.path) if doc is not None else "", _PDF_FILTER
        )
        if not path:
            return False
        old_marks = self._settings.bookmarks_for(doc.path) if doc is not None else []
        if self._controller.save_as(Path(path)):
            self._after_saved()
            if old_marks:  # 书签跟着新文件走（原文件的保留）
                self._settings.set_bookmarks(Path(path), old_marks)
            self._settings.add_recent_file(Path(path), max_count=MAX_LIBRARY_FILES)
            self._save_settings()
            return True
        QMessageBox.warning(self, "保存失败", self._controller.last_error or "保存失败")
        return False

    def _after_saved(self) -> None:
        doc = self._controller.document
        if doc is not None:
            self.status_bar.show_message(f"已保存到 {doc.path}")
            self._record_meta(self._tab, opened=False)
            self._save_settings()
        self._update_title()

    def _maybe_save(self) -> bool:
        """有未保存改动时弹窗询问。返回 True=可继续（已保存或放弃），False=取消。"""
        if not self._controller.is_modified:
            return True
        doc = self._controller.document
        name = doc.path.name if doc is not None else ""
        reply = QMessageBox.question(
            self,
            "保存更改",
            f"是否将更改保存到 “{name}”？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self._do_save()
        # Discard=放弃改动可继续；Cancel=取消不继续
        return bool(reply == QMessageBox.StandardButton.Discard)

    def _update_title(self) -> None:
        """标题栏反映当前文档与未保存状态（脏标记 *）。"""
        doc = self._controller.document
        if doc is None:
            self.setWindowTitle("Lumen")
            self.ribbon.set_document_title("")
            return
        dirty = doc.is_modified
        if not self._library_visible():
            self.setWindowTitle(f"{'* ' if dirty else ''}{doc.path.name} — Lumen")
        self.ribbon.set_document_title(doc.path.name, dirty=dirty)
        self._refresh_tab_labels()

    def _refresh_after_edit(self, keep: tuple[int, float] | None = None) -> None:
        """页结构可能变化的编辑（删页 / 旋转 / 合并 / 水印 / 撤销）之后整体重排。

        ``keep`` 为重排后要回到的 (页, 页内偏移)；默认保持编辑前的阅读位置。旧实现重排后
        当前页变成 None 又被当成第 1 页，下一次「删除 / 旋转」会作用到错误的页。
        """
        page, offset = keep if keep is not None else self.pdf_view.reading_position()
        count = self._controller.page_count
        self.pdf_view.setup_document(
            count,
            self._controller.get_page_sizes(),
            keep_page=min(page, max(0, count - 1)),
            keep_offset=offset,
        )
        self._update_zoom_label()
        self._update_title()
        self._update_undo_actions()
        self._update_page_actions()
        self.status_bar.set_page(self._current_page() + 1, count)
        self.sidebar.set_page_count(count)
        self._mark_annotations_changed()
        # 页码可能已变，旧的跳转历史不再可靠
        self._back_stack.clear()
        self._forward_stack.clear()
        self._update_history_actions()
        self._ai.clear_marks()  # 重排已清掉页面上的速读标记，页码也可能变了
        self._ai.invalidate_text()
        if self._controller.is_modified:
            self.status_bar.show_message(f"共 {count} 页 · 未保存")

    def _update_page_actions(self) -> None:
        """与页数相关的动作：只剩一页时不能删除（0 页的 PDF 无法保存）。"""
        active = self._controller.has_document
        self.delete_page_action.setEnabled(active and self._controller.page_count > 1)

    # ------------------------------------------------------------------ 批注
    def _on_annot_mode_changed(self, mode: str) -> None:
        previous = getattr(self, "_last_mode", AnnotationMode.NONE)
        self._last_mode = mode
        self._capture_translate = False  # 换了工具：框选回到「交给 AI 解释」
        if mode == AnnotationMode.PAN:
            # 抓手：ScrollHandDrag 自带开手/抓手光标
            self.pdf_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.pdf_view.setDragMode(QGraphicsView.DragMode.NoDrag)
            if mode not in (AnnotationMode.SIGNATURE, AnnotationMode.TIMESTAMP):
                self.pdf_view.setCursor(self._mode_cursor(mode))
        if mode != AnnotationMode.NONE:
            self.pdf_view.clear_selection()
        if mode not in (AnnotationMode.SIGNATURE, AnnotationMode.TIMESTAMP):
            self.pdf_view.clear_pending()
        # 同步勾选态（应对程序化切换，如关闭文档 / Esc 时复位）
        act = self._mode_to_action.get(mode)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        color = self._settings.highlight_color
        self.tool_hint.show_tool(mode, color)
        self._content.reposition_overlay()
        if mode == AnnotationMode.NONE and previous not in (
            AnnotationMode.NONE,
            AnnotationMode.PAN,
        ):
            self.status_bar.show_message("已退出批注工具")
        else:
            self.status_bar.show_message(f"工具：{_TOOL_NAMES.get(mode, mode)}")

    def _on_placement_finished(self) -> None:
        """签名 / 时间放置完成或 Esc 取消：回到选择工具。"""
        if self._annotation_controller.mode in (AnnotationMode.SIGNATURE, AnnotationMode.TIMESTAMP):
            self._annotation_controller.set_mode(AnnotationMode.NONE)

    def _mode_cursor(self, mode: str) -> QCursor:
        """各批注模式用不同光标，让画布上一眼看出当前工具。"""
        from pdfreader.gui.theme.icons import render_pixmap

        p = self._theme.palette
        if mode == AnnotationMode.HIGHLIGHT:
            return QCursor(
                render_pixmap("highlight", self._settings.highlight_color, size=26), 4, 22
            )
        if mode == AnnotationMode.UNDERLINE:
            return QCursor(render_pixmap("underline", "#60A5FA", size=24), 4, 20)
        if mode == AnnotationMode.INK:
            return QCursor(render_pixmap("ink", p.danger, size=26), 3, 23)
        if mode == AnnotationMode.TEXT:
            return QCursor(render_pixmap("note", p.accent, size=24), 4, 4)
        if mode == AnnotationMode.FREETEXT:
            return QCursor(render_pixmap("textbox", p.accent, size=24), 4, 4)
        if mode == AnnotationMode.ERASER:
            return QCursor(render_pixmap("eraser", p.text_secondary, size=24), 4, 20)
        if mode == AnnotationMode.CAPTURE:
            return QCursor(Qt.CursorShape.CrossCursor)
        return QCursor(Qt.CursorShape.IBeamCursor)  # 浏览/选择：文本光标

    def _highlight_color(self) -> tuple[float, float, float] | None:
        return _hex_to_rgb(self._settings.highlight_color)

    def _set_highlight_color(self, color: str, name: str = "") -> None:
        self._settings.highlight_color = color
        self._save_settings()
        for tab in self._tabs:
            tab.view.highlight_color = color
        self._refresh_swatches()
        self._annotation_controller.set_mode(AnnotationMode.HIGHLIGHT)
        self.pdf_view.setCursor(self._mode_cursor(AnnotationMode.HIGHLIGHT))
        self.tool_hint.show_tool(AnnotationMode.HIGHLIGHT, color)
        self.status_bar.show_message(f"高亮颜色：{name or color}")

    def _on_annot_highlight(self, page_num: int, rects) -> None:  # type: ignore[no-untyped-def]
        """拖选高亮：多行合成**一条**批注（一次撤销），失败如实提示。"""
        if self._controller.add_highlight(page_num, tuple(rects), color=self._highlight_color()):
            self._after_annotation_change(page_num)
            self.pdf_view.flash_rects(page_num, rects, QColor(self._settings.highlight_color))
            self.status_bar.show_message("已添加高亮 · Ctrl+Z 撤销")
        else:
            self._report_failure("添加高亮")

    def _on_markup_requested(self, page_num: int, rects, kind: str) -> None:  # type: ignore[no-untyped-def]
        """右键菜单 / 功能区：对选中文字加高亮 / 下划线 / 删除线 / 波浪线。"""
        color = self._highlight_color() if kind == "highlight" else None
        if self._controller.add_markup(page_num, tuple(rects), kind=kind, color=color):
            self._after_annotation_change(page_num)
            flash = self._settings.highlight_color if kind == "highlight" else "#60A5FA"
            self.pdf_view.flash_rects(page_num, rects, QColor(flash))
            names = {
                "highlight": "高亮",
                "underline": "下划线",
                "strikeout": "删除线",
                "squiggly": "波浪线",
            }
            self.status_bar.show_message(f"已添加{names.get(kind, '标记')} · Ctrl+Z 撤销")
        else:
            self._report_failure("添加标记")

    def _on_annot_ink(self, page_num: int, stroke) -> None:  # type: ignore[no-untyped-def]
        if self._controller.add_ink(page_num, (stroke,)):
            self._after_annotation_change(page_num)
        else:
            self._report_failure("添加手写")

    def _on_annot_text(self, page_num: int, point) -> None:  # type: ignore[no-untyped-def]
        if self._controller.document is None:
            return
        if self._annotation_controller.mode == AnnotationMode.FREETEXT:
            self._place_free_text(page_num, point)
            return
        text, ok = QInputDialog.getMultiLineText(self, "便签", "输入便签内容：")
        if ok and text.strip():
            if self._controller.add_text_annot(page_num, point, text.strip()):
                self._after_annotation_change(page_num)
            else:
                self._report_failure("添加便签")

    def _place_free_text(self, page_num: int, point: Point) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "文本框", "输入要显示在页面上的文字：")
        text = text.strip()
        if not ok or not text:
            return
        bounds = self._controller.get_page_bounds()
        if not 0 <= page_num < len(bounds):
            return
        rect = free_text_rect(point, text, bounds[page_num])
        if self._controller.add_free_text(page_num, rect, text):
            self._after_annotation_change(page_num)
            self.status_bar.show_message("已添加文本框 · 双击侧栏卡片可修改文字 · Ctrl+Z 撤销")
        else:
            self._report_failure("添加文本框")

    def _after_annotation_change(self, page_num: int) -> None:
        self._refresh_page(page_num)
        self.sidebar.invalidate_thumbnail(page_num)
        self._mark_annotations_changed()

    def _refresh_page(self, page_num: int) -> None:
        """批注/编辑后清缓存并重渲染该页，使改动立即显示。"""
        self.pdf_view.invalidate_page(page_num)
        self._render_page(page_num, self.pdf_view.zoom)
        self._update_title()

    # ------------------------------------------------------------------ 批注面板
    def _mark_annotations_changed(self) -> None:
        self._annotations_stale = True
        self._refresh_annotations_if_shown()

    def _refresh_annotations_if_shown(self) -> None:
        """刷新批注列表与「批注」角标。

        全文扫描批注的开销随页数增长：中小文档（≤ 300 页）只要侧边栏可见就刷新，
        角标数量始终准确（设计稿）；大文档只在批注页签可见时刷新。
        """
        small_doc = self._controller.page_count <= _ANNOT_SCAN_PAGE_LIMIT
        if (
            self._annotations_stale
            and self._controller.has_document
            and self.sidebar.isVisible()
            and (small_doc or self.sidebar.tabs.currentIndex() == SidePanel.TAB_ANNOTATIONS)
        ):
            annots = self._controller.list_all_annotations()
            self.sidebar.set_annotations(annots)
            self._tab.annotation_count = len(annots)
            self._annotations_stale = False

    def _on_annotation_activated(self, annot: Annotation) -> None:
        self._navigate_to(annot.page_num, annot.rect.y0)

    def _on_annotation_edit(self, annot: Annotation) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "编辑笔记", f"第 {annot.page_num + 1} 页 · {annot.type_name}", annot.content
        )
        if not ok:
            return
        if self._controller.set_annotation_content(annot.page_num, annot.index, text.strip()):
            self._after_annotation_change(annot.page_num)
        else:
            self._report_failure("修改笔记")

    def _on_annotation_delete(self, annot: Annotation) -> None:
        if self._controller.delete_annotation(annot.page_num, annot.index):
            self._after_annotation_change(annot.page_num)
        else:
            self._report_failure("删除批注")

    def _on_export_notes(self) -> None:
        doc = self._controller.document
        if doc is None:
            return
        from pdfreader.services.notes import annotations_to_markdown

        annotations = self._controller.list_all_annotations()
        if not annotations:
            QMessageBox.information(self, "导出笔记", "本文档还没有批注可导出。")
            return
        default = doc.path.parent / f"{doc.path.stem}_笔记.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出笔记", str(default), "Markdown (*.md);;文本文件 (*.txt)"
        )
        if not path:
            return
        text = annotations_to_markdown(self._controller.title, annotations, source=doc.path.name)
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入文件：{exc.strerror or exc}")
            return
        self.status_bar.show_message(f"已导出 {len(annotations)} 条批注到 {Path(path).name}")

    # ------------------------------------------------------------------ Zotero
    def _zotero_data_dir(self) -> Path | None:
        """Zotero 数据目录：设置里指定的优先，否则按 Zotero 的配置自动找，最后请用户选。"""
        saved = self._settings.zotero_data_dir.strip()
        if saved:
            folder = Path(saved)
            if (folder / "zotero.sqlite").is_file():
                return folder
            self.status_bar.show_message(f"设置里的 Zotero 数据目录没有 zotero.sqlite：{folder}")
        found = find_data_dir()
        if found is not None:
            return found
        chosen = QFileDialog.getExistingDirectory(
            self, "选择 Zotero 数据目录（里面有 zotero.sqlite）", str(Path.home())
        )
        if not chosen:
            return None
        picked = Path(chosen)
        if not (picked / "zotero.sqlite").is_file():
            QMessageBox.warning(
                self, "Zotero", "这个目录里没有 zotero.sqlite，请选 Zotero 的数据目录。"
            )
            return None
        self._settings.zotero_data_dir = str(picked)
        self._save_settings()
        return picked

    def _on_import_zotero(self) -> None:
        """把当前文档在 Zotero 里的批注导入进来（只读 Zotero 库，整批一次撤销）。"""
        tab = self._tab
        path = tab.path
        if path is None or not self._controller.has_document:
            self.status_bar.show_message("先打开一份 PDF")
            return
        folder = self._zotero_data_dir()
        if folder is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = ZoteroLibrary(folder).annotations_for(path)
        except ZoteroError as exc:
            QMessageBox.warning(self, "从 Zotero 导入批注", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not found:
            QMessageBox.information(
                self,
                "从 Zotero 导入批注",
                "Zotero 里没有这份 PDF 的批注。\n\n"
                "这里只读 Zotero 本机数据库，文件要能被 Zotero 对应到同一个附件（路径一致）。",
            )
            return
        items, skipped = to_pdf_space_annotations(found)
        added = tab.controller.import_pdf_space_annotations(items) if items else 0
        if added == 0:
            if items:
                reason = tab.controller.last_error or "原因未知"
                QMessageBox.warning(self, "从 Zotero 导入批注", f"导入失败：{reason}")
            else:
                QMessageBox.information(
                    self,
                    "从 Zotero 导入批注",
                    f"找到 {len(found)} 条批注，但都没有能写进这份 PDF 的（页码或类型不支持）。",
                )
            return
        for page_num in sorted({item.page_num for item in items}):
            self.pdf_view.invalidate_page(page_num)
        self.pdf_view.request_visible()
        self._mark_annotations_changed()
        self._update_title()
        self._update_undo_actions()
        missed = skipped + max(0, len(items) - added)
        suffix = f"，跳过 {missed} 条无法表示的" if missed else ""
        self.status_bar.show_message(f"已从 Zotero 导入 {added} 条批注{suffix} · Ctrl+Z 撤销")

    # ------------------------------------------------------------------ 写作素材
    def _glossary_for(self, text: str) -> list[tuple[str, str]]:
        """翻译用：原文里出现的术语（关掉「使用术语库」或库读不了时为空，不影响翻译）。"""
        if not self._settings.translate_use_glossary:
            return []
        try:
            return self._materials.glossary_for(text)
        except MaterialError as exc:
            _logger.warning("读取术语库失败：%s", exc)
            return []

    def _toggle_glossary(self) -> None:
        on = self.glossary_action.isChecked()
        self._settings.translate_use_glossary = on
        self._save_settings()
        self.status_bar.show_message("翻译时将按术语库统一译法" if on else "翻译时不再使用术语库")

    def _on_material_requested(self, kind: str) -> None:
        """右键「加入语料 / 引文 / 术语」：把选中的文字存进素材库（记下来源与页码）。"""
        text = clean_copied_text(self.pdf_view.selected_text)
        if not text:
            self.status_bar.show_message("请先用「选择」工具选中文字")
            return
        picked = self.pdf_view.selection_rects()
        page = picked[0] if picked is not None else None
        note = ""
        if kind == "term":
            if len(text) > MAX_TERM_CHARS:
                QMessageBox.information(
                    self, "加入术语", f"术语最多 {MAX_TERM_CHARS} 个字，请只选中术语本身。"
                )
                return
            existing = self._find_material("term", text)
            note, ok = QInputDialog.getText(
                self,
                "加入术语",
                f"「{text}」的译名（翻译时按它统一译法）：",
                text=existing.note if existing is not None else "",
            )
            if not ok or not note.strip():
                return
        elif kind == "quote":
            note = quote_template(text)
        path, title = self._doc_info(self._tab)
        try:
            self._materials.add(kind, text, note, doc_path=path, doc_title=title, page=page)
        except MaterialError as exc:
            QMessageBox.warning(self, "加入失败", str(exc))
            return
        self._refresh_materials()
        hints = {
            "corpus": "已加入语料库",
            "quote": "已加入引文库（附了一句转述，可在侧栏「素材」里编辑）",
            "term": f"已加入术语库：{text} → {note.strip()}",
        }
        self.status_bar.show_message(hints.get(kind, "已加入素材库"))

    def _find_material(self, kind: str, text: str) -> Material | None:
        try:
            return self._materials.find(kind, text)
        except MaterialError:
            return None

    def _doc_info(self, tab: DocumentTab) -> tuple[str, str]:
        """(路径, 标题)：收藏素材时记下来源。"""
        path = tab.path
        if path is None:
            return "", ""
        return str(path), display_title(tab.controller.title, path)

    def _on_sidebar_tab(self, index: int) -> None:
        if index == SidePanel.TAB_MATERIALS:
            self._refresh_materials()

    def _refresh_materials(self) -> None:
        """重新读当前类型的素材（素材页没打开时不读，免得无谓建库）。"""
        if self.sidebar.tabs.currentIndex() != SidePanel.TAB_MATERIALS:
            return
        try:
            items = self._materials.items(self.sidebar.material_kind_key)
        except MaterialError as exc:
            self.status_bar.show_message(f"素材库：{exc}")
            items = []
        self.sidebar.set_materials(items)

    def _on_material_activated(self, m: Material) -> None:
        """跳回素材的原文：文档已打开就切过去，否则打开它，再翻到那一页。"""
        if not m.doc_path:
            self.status_bar.show_message("这条素材没有记录来源文档")
            return
        path = Path(m.doc_path)
        if self._find_tab(path) is None and not path.is_file():
            self.status_bar.show_message(f"找不到原文档：{path.name}")
            return
        if not self.open_path(path):
            return
        if m.page is not None and m.page < self._controller.page_count:
            self._navigate_to(m.page, None)

    def _on_material_edit(self, m: Material) -> None:
        labels = {"word": "释义", "corpus": "备注", "quote": "转述", "term": "译名"}
        label = labels.get(m.kind, "备注")
        if m.kind in ("word", "term"):
            text, ok = QInputDialog.getText(self, f"编辑{label}", f"{m.text}：", text=m.note)
        else:
            text, ok = QInputDialog.getMultiLineText(self, f"编辑{label}", m.text[:200], m.note)
        if not ok:
            return
        try:
            self._materials.update_note(m.id, text)
        except MaterialError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_materials()

    def _on_material_delete(self, m: Material) -> None:
        try:
            self._materials.remove(m.id)
        except MaterialError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_materials()
        self.status_bar.show_message(f"已从{KIND_LABELS.get(m.kind, '素材')}里删除")

    def _on_materials_export(self, kind: str) -> None:
        names = {"word": "单词本", "corpus": "语料库", "quote": "引文库", "term": "术语库"}
        name = names.get(kind, "素材")
        default = Path.home() / "Documents" / f"Lumen_{name}.md"
        path, chosen = QFileDialog.getSaveFileName(
            self, f"导出{name}", str(default), "Markdown (*.md);;CSV 表格 (*.csv)"
        )
        if not path:
            return
        target = Path(path)
        as_csv = target.suffix.lower() == ".csv" or (chosen.startswith("CSV") and not target.suffix)
        try:
            if as_csv:
                # 带 BOM：Excel 直接双击打开不乱码
                text = self._materials.export_csv(kind)
                target.with_suffix(".csv").write_text(text, encoding="utf-8-sig", newline="")
                target = target.with_suffix(".csv")
            else:
                target.write_text(self._materials.export_markdown(kind), encoding="utf-8")
        except (OSError, MaterialError) as exc:
            QMessageBox.warning(self, "导出失败", f"无法导出：{exc}")
            return
        self.status_bar.show_message(f"已导出{name}到 {target.name}")

    # ------------------------------------------------------------------ 侧边栏 / 阅读模式 / 复制
    def _set_sidebar_visible(self, visible: bool, *, persist: bool = True) -> None:
        """侧边栏宽度动画显隐（设计稿 transition-[width,opacity] 500ms）。"""
        visible = visible and self._controller.has_document
        self.sidebar_action.setChecked(
            visible or (not self._controller.has_document and self.sidebar_action.isChecked())
        )
        if persist and self._controller.has_document:
            self._settings.sidebar_visible = visible
            self._save_settings()
        start = self.sidebar.width() if self.sidebar.isVisible() else 0
        end = self._sidebar_width() if visible else 0
        if visible:
            self.sidebar.setVisible(True)
        if not motion.enabled() or start == end:
            self.sidebar.setMaximumWidth(end)
            self.sidebar.setVisible(visible)
        else:
            anim = motion.animate_property(
                self.sidebar, b"maximumWidth", start, end, TOKENS.anim_sidebar, owner=self.sidebar
            )
            if anim is not None and not visible:
                anim.finished.connect(lambda: self.sidebar.setVisible(False))
        self._refresh_annotations_if_shown()

    def _render_thumbnails(self, pages: list[int]) -> None:
        """为侧边栏可见缩略图渲染小图（主线程渲染受控文档，与正文一致）。"""
        from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

        for page in pages:
            if not 0 <= page < self._controller.page_count:
                continue
            sizes = self._controller.get_page_bounds()
            width = sizes[page].width if page < len(sizes) else 595.0
            scale = 112 / max(1.0, width) * max(1.0, float(self.devicePixelRatioF()))
            try:
                data = self._controller.render_page(page, scale)
            except Exception:  # 缩略图失败不打断阅读，正文渲染会单独报错
                continue
            self.sidebar.set_thumbnail(page, pixmap_to_qpixmap(data))

    def _set_reading_mode(self, mode: str) -> None:
        for tab in self._tabs:  # 阅读模式是全局偏好：所有标签一致
            tab.view.set_reading_mode(mode)
        actual = self.pdf_view.reading_mode
        self.reading_mode_action.setChecked(actual != ReadingMode.NORMAL)
        for key, act in self._mode_actions.items():
            act.setChecked(key == actual)
        self.status_bar.set_reading_mode(_MODE_CHIP.get(actual, ""))
        self._settings.reading_mode = actual
        self._save_settings()
        label = next((lbl for m, lbl, _d in _READING_MODES if m == actual), actual)
        self.status_bar.show_message(f"阅读模式：{label}")

    def _copy_merged(self) -> None:
        """去换行复制（设计稿按钮 / Ctrl+Shift+C）。"""
        n = self.pdf_view.copy_selection(merge_lines=True)
        if n:
            self.status_bar.show_message(f"已复制 {n} 个字符（已合并换行）")
        else:
            self.status_bar.show_message("请先用「选择」工具选中文字")

    # ------------------------------------------------------------------ AI 助读
    def _selection_context(self) -> tuple[str, int | None, Rect | None]:
        # 发给 AI 前合并排版换行、展开连字（「ﬁ」会让模型把一个词当成两个）
        text = clean_copied_text(self.pdf_view.selected_text)
        picked = self.pdf_view.selection_rects()
        if picked is None or not picked[1]:
            return text, None, None
        page, rects = picked
        bound = Rect(
            min(r.x0 for r in rects),
            min(r.y0 for r in rects),
            max(r.x1 for r in rects),
            max(r.y1 for r in rects),
        )
        return text, page, bound

    def _on_translate(self) -> None:
        """AI 划词翻译（右键「AI 翻译」/ 功能区「翻译」）。"""
        text, page, rect = self._selection_context()
        if not text:
            self.status_bar.show_message("请先用「选择」工具选中文字")
            return
        self._ai.translate(text, page, rect)

    def _on_ai_explain(self) -> None:
        text, page, rect = self._selection_context()
        if not text:
            self.status_bar.show_message("请先用「选择」工具选中文字")
            return
        self._ai.explain(text, page, rect)

    def _on_ai_requested(self, kind: str) -> None:
        """右键菜单：AI 翻译 / 解释 / 问 AI（选中文字折叠在输入框上方，随下一问发送）。"""
        if kind == "translate":
            self._on_translate()
        elif kind == "explain":
            self._on_ai_explain()
        elif kind == "ask":
            text, page, _rect = self._selection_context()
            self._set_ai_panel_visible(True)
            label = f"第 {page + 1} 页" if page is not None else ""
            self.ai_panel.attach_context(text, label)

    def _start_capture(self) -> None:
        if not self._controller.has_document:
            self.status_bar.show_message("请先打开文档")
            return
        self._capture_translate = False
        self._annotation_controller.set_mode(AnnotationMode.CAPTURE)

    def _start_translate_capture(self) -> None:
        """划词翻译遇到乱码：框选那段文字，截图识别后在浮窗里翻译。"""
        if not self._controller.has_document:
            return
        self._annotation_controller.set_mode(AnnotationMode.CAPTURE)
        self._capture_translate = True  # 在 set_mode 之后：模式变化回调会清掉它
        self.tool_hint.show_tool("capture_translate")
        self._content.reposition_overlay()

    def _on_area_captured(self, page_num: int, rect: Rect) -> None:
        translate, self._capture_translate = self._capture_translate, False
        self._annotation_controller.set_mode(AnnotationMode.NONE)
        if translate:
            self._tab.translator.translate_area(page_num, rect)
        else:
            self._ai.explain_area(page_num, rect)

    # ------------------------------------------------------------------ 划词翻译
    def _set_selection_translate(self, mode: str) -> None:
        """选中文字后：显示「译」按钮 / 直接翻译 / 不出现（全局偏好，所有标签一致）。"""
        self._settings.selection_translate = mode
        self._save_settings()
        for tab in self._tabs:
            tab.translator.set_mode(mode)
        for key, act in self._selection_translate_actions.items():
            act.setChecked(key == mode)
        labels = {"button": "选中后显示「译」按钮", "auto": "选中即翻译", "off": "已关闭"}
        self.status_bar.show_message(f"划词翻译：{labels.get(mode, mode)}")

    def _set_translate_continuous(self, on: bool) -> None:
        """连翻开关（功能区 / Ctrl+Shift+L / 浮窗上的按钮）。"""
        changed = self._settings.translate_continuous != on
        self._settings.translate_continuous = on
        if changed:
            self._save_settings()
        for tab in self._tabs:
            tab.translator.set_continuous(on)
        self.translate_continuous_action.setChecked(on)
        if self.sender() is self.translate_continuous_action:
            self.status_bar.show_message(
                "连翻已开启：接着选的文字会拼在一起翻译" if on else "连翻已关闭"
            )

    def _set_ai_panel_visible(self, visible: bool) -> None:
        """右侧 AI 面板：与左侧边栏同样的宽度动画。"""
        self.ai_panel_action.setChecked(visible)
        dock = self._ai_dock
        if visible:
            self._ai.refresh_panel()
        start = dock.width() if dock.isVisible() else 0
        end = self._ai_panel_width() if visible else 0
        if visible:
            dock.setVisible(True)
        dock.setMinimumWidth(0)  # 动画期间放开下限，到位后再钉住宽度
        if not motion.enabled() or start == end:
            dock.setMaximumWidth(end)
            dock.setMinimumWidth(end)
            dock.setVisible(visible)
        else:
            anim = motion.animate_property(
                dock, b"maximumWidth", start, end, TOKENS.anim_sidebar, owner=dock
            )
            if anim is not None:
                anim.finished.connect(lambda: self._settle_ai_dock(visible))
        if visible:
            self.ai_panel.input.setFocus()

    def _settle_ai_dock(self, visible: bool) -> None:
        dock = self._ai_dock
        if visible and self.ai_panel_action.isChecked():
            dock.setMinimumWidth(dock.maximumWidth())
        else:
            dock.setVisible(self.ai_panel_action.isChecked())

    # ------------------------------------------------------------------ 侧栏宽度（拖动调整）
    def _reading_room(self, other: int) -> int:
        """另一侧占 ``other`` 时，当前这一侧最多能拿多宽（阅读区至少留 _MIN_READING_WIDTH）。"""
        return self._content.width() - other - _MIN_READING_WIDTH

    def _sidebar_width(self) -> int:
        return max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, self._settings.sidebar_width))

    def _ai_panel_width(self) -> int:
        return max(AI_PANEL_MIN_WIDTH, min(AI_PANEL_MAX_WIDTH, self._settings.ai_panel_width))

    def _resize_sidebar(self, width: int) -> None:
        other = self._ai_dock.width() if self._ai_dock.isVisible() else 0
        limit = min(SIDEBAR_MAX_WIDTH, self._reading_room(other))
        width = max(SIDEBAR_MIN_WIDTH, min(limit, width))
        self._settings.sidebar_width = width
        self.sidebar.set_panel_width(width)
        if self.sidebar.isVisible():
            self.sidebar.setMaximumWidth(width)

    def _resize_ai_panel(self, width: int) -> None:
        other = self.sidebar.width() if self.sidebar.isVisible() else 0
        limit = min(AI_PANEL_MAX_WIDTH, self._reading_room(other))
        width = max(AI_PANEL_MIN_WIDTH, min(limit, width))
        self._settings.ai_panel_width = width
        if self._ai_dock.isVisible():
            self._ai_dock.setMinimumWidth(width)
            self._ai_dock.setMaximumWidth(width)

    def _reset_panel_width(self, which: str) -> None:
        """双击拖动条：恢复默认宽度。"""
        if which == "sidebar":
            self._resize_sidebar(SIDEBAR_WIDTH)
        else:
            self._resize_ai_panel(AI_PANEL_WIDTH)
        self._save_settings()

    # ------------------------------------------------------------------ 撤销 / 橡皮擦 / 放置
    def _on_undo(self) -> None:
        if self._controller.undo():
            self._reload_after_change()

    def _on_redo(self) -> None:
        if self._controller.redo():
            self._reload_after_change()

    def _reload_after_change(self) -> None:
        """撤销/重做后整体刷新（可能影响任意页），保持当前阅读位置。"""
        self._refresh_after_edit()

    def _update_undo_actions(self) -> None:
        self.undo_action.setEnabled(self._controller.can_undo())
        self.redo_action.setEnabled(self._controller.can_redo())

    def _on_erase(self, page_num: int, point) -> None:  # type: ignore[no-untyped-def]
        idx = self._controller.annotation_index_at(page_num, point)
        if idx is None:
            self.status_bar.show_message("这里没有批注")
            return
        if self._controller.delete_annotation(page_num, idx):
            self._after_annotation_change(page_num)
        else:
            self._report_failure("删除批注")

    def _on_placement(self, page_num: int, point, kind: str, payload) -> None:  # type: ignore[no-untyped-def]
        if kind == "image":
            from PySide6.QtGui import QImage

            img = QImage(str(payload))
            if img.isNull():
                QMessageBox.warning(self, "插入失败", "无法读取签名图片。")
                return
            w = 160.0
            h = w * (img.height() / img.width()) if img.width() else 60.0
            rect = Rect(point.x, point.y, point.x + w, point.y + h)
            if self._controller.insert_image(page_num, rect, Path(str(payload))):
                self._after_annotation_change(page_num)
            else:
                self._report_failure("插入签名")
        elif kind == "text":
            if self._controller.insert_text(page_num, point, str(payload), fontsize=12):
                self._after_annotation_change(page_num)
            else:
                self._report_failure("插入文字")

    def _on_signature(self) -> None:
        from pdfreader.gui.dialogs import SignatureDialog

        dialog = SignatureDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.image_path():
            path = Path(str(dialog.image_path()))
            if dialog.is_temporary:
                self._temp_files.append(path)  # 手绘签名的临时 PNG：退出时清理
            self._annotation_controller.set_mode(AnnotationMode.SIGNATURE)
            self.pdf_view.arm_placement("image", str(path))
        else:
            self._annotation_controller.set_mode(AnnotationMode.NONE)
            self.select_action.setChecked(True)

    def _on_timestamp(self) -> None:
        from datetime import datetime

        text = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._annotation_controller.set_mode(AnnotationMode.TIMESTAMP)
        self.pdf_view.arm_placement("text", text)

    # ------------------------------------------------------------------ OCR
    def _on_ocr_current_page(self) -> None:
        self._start_ocr([self._current_page()])

    def _on_ocr_all_pages(self) -> None:
        self._start_ocr(None)

    def _start_ocr(self, page_nums: list[int] | None) -> None:
        if not self._controller.has_document:
            return
        if any(t.ocr_worker is not None and t.ocr_worker.isRunning() for t in self._tabs):
            # 已在运行（含其他标签的）：同一时间只跑一个 OCR，进度框也只有一个
            self.status_bar.show_message("OCR 正在进行中，请等当前任务结束")
            return
        source = self._worker_source()
        if source is None:
            return
        self._ocr_results.clear()  # 每轮独立，避免上一轮文本混进结果
        self._close_ocr_progress()
        self._ocr_progress = QProgressDialog("OCR 识别中…", "取消", 0, 100, self)
        self._ocr_progress_owner = self._tab
        self._ocr_progress.setWindowTitle("OCR")
        self._ocr_progress.setMinimumDuration(0)
        self._ocr_progress.show()

        worker = OcrWorker(self)
        worker.pageOcrCompleted.connect(self._on_ocr_page_done)
        worker.progress.connect(self._on_ocr_progress)
        worker.ocrCompleted.connect(self._on_ocr_all_done)
        worker.ocrCancelled.connect(self._on_ocr_cancelled)
        worker.ocrFailed.connect(self._on_ocr_failed)
        worker.finished.connect(self._on_ocr_worker_finished)
        worker.setup(source, page_nums=page_nums, password=self._controller.password)
        self._ocr_worker = worker
        self._ocr_progress.canceled.connect(worker.shutdown)
        worker.start()

    def _ocr_tab(self) -> DocumentTab:
        """发出当前 OCR 信号的线程属于哪个标签（直接调用时视为当前标签）。"""
        sender = self.sender()
        if sender is not None:
            for tab in self._tabs:
                if tab.ocr_worker is sender:
                    return tab
        return self._tab

    def _on_ocr_page_done(self, result) -> None:  # type: ignore[no-untyped-def]
        tab = self._ocr_tab()
        tab.ocr_results[result.page_num] = result.text
        tab.ai.add_ocr_text(result.page_num, result.text)

    def _on_ocr_progress(self, done: int, total: int) -> None:
        if self._ocr_progress is not None and total > 0:
            self._ocr_progress.setValue(int(done * 100 / total))

    def _on_ocr_all_done(self, count: int) -> None:
        tab = self._ocr_tab()
        self._close_ocr_progress()
        self._show_ocr_results(
            "OCR 完成", f"已识别 {count} 页，但未识别到任何文字。", False, tab=tab
        )

    def _on_ocr_failed(self, message: str) -> None:
        self._close_ocr_progress()
        QMessageBox.warning(self, "OCR 失败", message)

    def _on_ocr_cancelled(self, count: int) -> None:
        """用户取消：与「完成」区分开；已完成的页仍可查看。"""
        tab = self._ocr_tab()
        self._close_ocr_progress()
        self._show_ocr_results("OCR 已取消", f"已取消，取消前完成 {count} 页。", True, tab=tab)

    def _show_ocr_results(
        self, title: str, empty_message: str, cancelled: bool, *, tab: DocumentTab | None = None
    ) -> None:
        tab = tab or self._tab
        doc = tab.controller.document
        if tab is not self._tab and doc is not None:
            title = f"{title} · {doc.path.name}"  # 识别完成时用户已切到别的标签
        if not any(text.strip() for text in tab.ocr_results.values()):
            QMessageBox.information(self, title, empty_message)
            return
        from pdfreader.gui.dialogs import OcrResultDialog

        OcrResultDialog(
            dict(tab.ocr_results),
            self,
            title=title,
            default_dir=doc.path.parent if doc is not None else None,
            cancelled=cancelled,
        ).exec()

    def _close_ocr_progress(self) -> None:
        if self._ocr_progress is not None:
            self._ocr_progress.close()
            self._ocr_progress = None
        self._ocr_progress_owner = None

    def _on_ocr_worker_finished(self) -> None:
        """OCR 线程收尾：清引用并交给 Qt 删除（否则连续 OCR 会累积线程对象）。"""
        worker = self.sender()
        for tab in self._tabs:
            if worker is not None and tab.ocr_worker is worker:
                tab.ocr_worker = None
        if isinstance(worker, OcrWorker):
            with contextlib.suppress(ValueError):
                self._retired_ocr_workers.remove(worker)
            worker.deleteLater()

    def _retire_ocr_worker(self, worker: OcrWorker) -> None:
        """取消并回收 OCR 线程：断信号 + 请求取消（当前页跑完即停），不长时间阻塞。"""
        for signal in (
            worker.pageOcrCompleted,
            worker.progress,
            worker.ocrCompleted,
            worker.ocrCancelled,
            worker.ocrFailed,
        ):
            with contextlib.suppress(Exception):
                signal.disconnect()
        worker.shutdown()
        if worker.isRunning():
            worker.wait(500)
        if worker.isRunning():
            self._retired_ocr_workers.append(worker)
            worker.finished.connect(self._on_ocr_worker_finished)
        else:
            worker.deleteLater()

    def _wait_for_background_workers(self) -> None:
        """退出前等所有后台线程结束：QThread 运行中被销毁会让进程直接崩溃。

        OCR 的取消在页边界生效（单页识别不可中断，至多几秒），这里显示忙碌光标等待。
        """
        workers: list[SearchWorker | OcrWorker] = [
            *self._retired_search_workers,
            *self._retired_ocr_workers,
        ]
        for w in (self._search_worker, *(t.ocr_worker for t in self._tabs)):
            if w is not None:
                workers.append(w)
        for tab in (*self._tabs, *self._parked_tabs):
            tab.ai.shutdown()
            tab.translator.shutdown()
        running = [w for w in workers if w.isRunning()]
        if not running:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for w in running:
                w.shutdown()
                w.wait()
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------ 导出 / 关于 / 信息
    def _on_export(self) -> None:
        doc = self._controller.document
        if doc is None:
            return
        source = self._worker_source()
        if source is None:
            return
        from pdfreader.gui.dialogs import ExportDialog

        ExportDialog(
            source,
            self,
            display_path=doc.path,
            page_count=self._controller.page_count,
            password=self._controller.password,
        ).exec()

    def _on_about(self) -> None:
        from pdfreader.gui.dialogs import AboutDialog

        AboutDialog(self).exec()

    def _on_doc_info(self) -> None:
        doc = self._controller.document
        if doc is None:
            return
        meta = doc.meta
        rows = [
            ("文件", str(doc.path)),
            ("标题", meta.title or "—"),
            ("作者", meta.author or "—"),
            ("主题", meta.subject or "—"),
            ("关键词", meta.keywords or "—"),
            ("创建工具", meta.creator or "—"),
            ("生成工具", meta.producer or "—"),
            ("页数", str(doc.page_count)),
            ("加密", "是" if meta.is_encrypted else "否"),
        ]
        with contextlib.suppress(OSError):
            rows.insert(1, ("大小", f"{doc.path.stat().st_size / 1024 / 1024:.2f} MB"))
        QMessageBox.information(self, "文档信息", "\n".join(f"{k}：{v}" for k, v in rows))

    # ------------------------------------------------------------------ 打印
    def _on_print(self) -> None:
        if self._controller.document is None:
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setFromTo(1, self._controller.page_count)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("打印")
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return
        try:
            if self._print_document(printer):
                self.status_bar.show_message("已发送到打印机")
            else:
                self.status_bar.show_message("打印已取消")
        except Exception as exc:
            QMessageBox.warning(self, "打印失败", str(exc))

    def _print_document(self, printer) -> bool:  # type: ignore[no-untyped-def]
        """逐页渲染并送入打印机；返回 False 表示用户中途取消。

        进度框设为窗口模态：渲染期间要 processEvents 让「取消」能响应，非模态时
        用户还能顺手关掉文档或删页，导致后续页渲染越界。
        """
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import QPrinter

        from pdfreader.utils.qt_bridge import pixmap_to_qpixmap

        total = self._controller.page_count
        if printer.printRange() == QPrinter.PrintRange.PageRange:
            first = max(1, printer.fromPage()) - 1
            last = (printer.toPage() - 1) if printer.toPage() > 0 else total - 1
        else:
            first, last = 0, total - 1
        last = min(last, total - 1)
        render_dpi = min(200, printer.resolution())  # 兼顾质量与内存
        zoom = render_dpi / 72.0
        progress = QProgressDialog("正在渲染打印页…", "取消", 0, max(1, last - first + 1), self)
        progress.setWindowTitle("打印")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        painter = QPainter(printer)
        cancelled = False
        try:
            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            for i, page_num in enumerate(range(first, last + 1)):
                if progress.wasCanceled():
                    cancelled = True
                    break
                if i > 0:
                    printer.newPage()
                data = self._controller.render_page(page_num, zoom)
                pixmap = pixmap_to_qpixmap(data)
                scaled = pixmap.scaled(
                    int(page_rect.width()),
                    int(page_rect.height()),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = page_rect.x() + (page_rect.width() - scaled.width()) / 2
                y = page_rect.y() + (page_rect.height() - scaled.height()) / 2
                painter.drawPixmap(int(x), int(y), scaled)
                progress.setValue(i + 1)
                QApplication.processEvents()  # 让进度条与"取消"按钮能响应
        finally:
            progress.close()
            painter.end()
        return not cancelled

    # ------------------------------------------------------------------ 渲染（主线程，直接渲染受控文档）
    def _render_page(self, page_num: int, zoom: float) -> None:
        """按需渲染某页并填入视图。

        直接渲染**控制器持有的那份文档**（被编辑/批注的文档本身），改动立即可见。
        渲染倍率由视图给出（乘屏幕缩放比、受像素上限约束），``zoom`` 是逻辑缩放。
        """
        doc = self._controller.document
        if doc is None or doc.is_closed:
            return
        if page_num < 0 or page_num >= self._controller.page_count:
            return
        try:
            data = self._controller.render_page(page_num, self.pdf_view.render_scale(page_num))
        except Exception as exc:
            self.status_bar.show_message(f"第 {page_num + 1} 页渲染失败：{exc}")
            return
        self.pdf_view.on_page_rendered(page_num, zoom, data)

    # ------------------------------------------------------------------ 打开 / 关闭
    def _on_open(self) -> None:
        start_dir = ""
        if self._settings.recent_files:
            start_dir = str(Path(self._settings.recent_files[0]).parent)
        paths, _ = QFileDialog.getOpenFileNames(self, "打开 PDF", start_dir, _PDF_FILTER)
        self._open_paths(paths)

    def _on_open_recent(self, path: str) -> None:
        self.open_path(path)

    def _open_paths(self, paths: Sequence[str | Path]) -> None:
        for path in paths:
            if not self.open_path(path):
                break  # 失败 / 取消（如密码框点了取消）：不再接着打开后面的

    def open_path(self, path: str | Path) -> bool:
        """打开文件（菜单 / 最近文件 / 拖放 / 命令行共用）。加密文档会询问密码。

        已有文档时在新标签里打开（当前文档不受影响）；文件已在某个标签里打开则直接切过去。
        """
        target = Path(path)
        if not target.exists():
            QMessageBox.warning(self, "文件不存在", f"找不到文件：\n{target}")
            self._settings.remove_recent_file(target)
            self._refresh_library()
            self._save_settings()
            return False
        existing = self._find_tab(target)
        if existing is not None:
            self._activate(existing)
            self._show_reader()
            self.status_bar.show_message(f"“{target.name}”已经打开，已切换到它的标签")
            return True
        if self._tab.has_document:
            if sum(1 for t in self._tabs if t.has_document) >= MAX_TABS:
                QMessageBox.information(
                    self, "标签太多", f"最多同时打开 {MAX_TABS} 个文档，请先关闭一些标签。"
                )
                return False
            tab = self._new_tab()  # 打开成功才加入标签栏（见 _on_tab_opened）
        else:
            tab = self._tab
        self._remember_reading_position()
        opened = self._open_in(tab, target)
        if not opened and tab is not self._tab:
            self._discard_tab(tab)
        return opened

    def _discard_tab(self, tab: DocumentTab) -> None:
        """丢弃一个没能打开文档的新标签（从未加入标签栏）。"""
        tab.view.deleteLater()
        tab.ai.deleteLater()
        tab.panel.deleteLater()

    def _open_in(self, tab: DocumentTab, target: Path) -> bool:
        password: str | None = None
        for attempt in range(_PASSWORD_ATTEMPTS + 1):
            self._last_open_failure = ""
            if tab.controller.open_file(target, password):
                return True
            if self._last_open_failure not in (
                OpenResult.NEEDS_PASSWORD,
                OpenResult.WRONG_PASSWORD,
            ):
                return False
            if attempt == _PASSWORD_ATTEMPTS:
                break
            prompt = (
                f"“{target.name}”受密码保护，请输入密码："
                if self._last_open_failure == OpenResult.NEEDS_PASSWORD
                else "密码错误，请重新输入："
            )
            text, accepted = QInputDialog.getText(
                self, "需要密码", prompt, QLineEdit.EchoMode.Password
            )
            if not accepted:
                return False
            password = text
        QMessageBox.warning(self, "无法打开", "密码错误次数过多，已取消打开。")
        return False

    def _on_close_document(self) -> None:
        """关闭当前标签（Ctrl+W）；最后一个标签关闭后回到文献库。"""
        self._close_tab(self._tab)

    def _reset_document_session(self) -> None:
        """丢弃与上一个文档相关的一切会话状态（关闭与切换文档共用）。

        切换文档时控制器静默关闭旧文档、不发 ``documentClosed``；旧实现只在关闭时回收
        线程，于是旧文档的搜索结果会出现在新文档的面板里，OCR 结果也会串过来。
        """
        if self._search_worker is not None:
            self._retire_search_worker(self._search_worker)
            self._search_worker = None
        self._active_query = ""
        self.search_panel.clear()
        self.search_panel.setVisible(False)
        self.pdf_view.clear_search_hit()
        self.find_next_action.setEnabled(False)
        self.find_prev_action.setEnabled(False)
        if self._ocr_worker is not None:
            self._retire_ocr_worker(self._ocr_worker)
            self._ocr_worker = None
        if self._ocr_progress_owner in (None, self._tab):  # 别的标签的 OCR 进度框不动
            self._close_ocr_progress()
        self._ocr_results.clear()
        self._annotation_controller.set_mode(AnnotationMode.NONE)
        self.select_action.setChecked(True)
        self.pdf_view.clear_pending()
        self._back_stack.clear()
        self._forward_stack.clear()
        self.sidebar.clear()
        self._annotations_stale = True
        self._tab.annotation_count = None
        self._ai.reset_document()
        self._tab.translator.reset()

    def _on_document_opened(self, page_count: int) -> None:
        self._reset_document_session()
        self._show_reader()
        self._stack.setCurrentWidget(self.pdf_view)
        self._update_title()

        # 先定侧边栏宽度再布局：否则「适应宽度」按没有侧边栏的宽度算，随后视口变窄
        # 又要重排一次（中间还会闪出横向滚动条）。打开时直接到位，不做宽度动画。
        self.sidebar_action.setChecked(self._settings.sidebar_visible)
        self.sidebar.setMaximumWidth(self._sidebar_width() if self._settings.sidebar_visible else 0)
        self.sidebar.setVisible(self._settings.sidebar_visible)
        content_layout = self._content.layout()
        if content_layout is not None:
            content_layout.activate()

        doc = self._controller.document
        position = self._settings.position_for(doc.path) if doc is not None else None
        self.pdf_view.setup_document(page_count, self._controller.get_page_sizes())
        if position is not None and position.zoom > 0:
            self.pdf_view.set_zoom(position.zoom)
        else:
            self.pdf_view.fit_to_width()
        if position is not None and 0 <= position.page < page_count:
            self.pdf_view.restore_position(position.page, position.offset)
        self.pdf_view.setFocus()  # 打开后即可用 Ctrl+C 复制选中文本

        self._set_document_state(True)
        self._update_undo_actions()
        self._update_zoom_label()
        self._update_history_actions()
        self.status_bar.set_page(self._current_page() + 1, page_count)
        if position is not None and position.page > 0:
            message = f"已恢复到上次阅读位置：第 {position.page + 1} 页"
        else:
            message = f"共 {page_count} 页"
        self.status_bar.show_message(message)
        self._page_group_title.setText(f"当前页 · 第 {self._current_page() + 1} 页")

        # 侧边栏：目录 / 缩略图（批注列表在切到该页签时按需加载）
        toc = self._controller.get_toc()
        self.sidebar.set_toc(toc)
        self.sidebar.set_page_count(page_count)
        self._refresh_bookmarks()
        if toc or self.sidebar.bookmarks():
            self.sidebar.tabs.setCurrentIndex(SidePanel.TAB_TOC)
        self._refresh_annotations_if_shown()

        if doc is not None:
            self._settings.add_recent_file(doc.path, max_count=MAX_LIBRARY_FILES)
            self._record_meta(self._tab, opened=True)
            self._save_settings()
        self._sync_tab_bar()

    def _set_document_state(self, active: bool) -> None:
        """有无文档时统一切换所有相关动作与控件的可用性。"""
        for act in (
            self.close_doc_action,
            self.save_action,
            self.save_as_action,
            self.print_action,
            self.zotero_action,
            self.zoom_in_action,
            self.zoom_out_action,
            self.fit_action,
            self.fit_page_action,
            self.actual_size_action,
            self.sidebar_action,
            self.reading_mode_action,
            self.rotate_action,
            self.merge_action,
            self.watermark_action,
            self.export_action,
            self.export_notes_action,
            self.doc_info_action,
            self.search_action,
            self.copy_merge_action,
            self.translate_action,
            self.ai_summary_action,
            self.ai_tldr_action,
            self.ai_speedread_action,
            self.ai_suggest_action,
            self.ai_explain_action,
            self.capture_action,
            self.color_action,
            self.select_action,
            self.hand_action,
            self.highlight_action,
            self.underline_action,
            self.ink_action,
            self.note_action,
            self.freetext_action,
            self.bookmark_action,
            self.eraser_action,
            self.signature_action,
            self.timestamp_action,
        ):
            act.setEnabled(active)
        self.reading_mode_action.setChecked(self.pdf_view.reading_mode != ReadingMode.NORMAL)
        for key, act in self._mode_actions.items():
            act.setChecked(key == self.pdf_view.reading_mode)
        self.status_bar.set_document_active(active)
        if active:
            self.status_bar.set_reading_mode(_MODE_CHIP.get(self.pdf_view.reading_mode, ""))
        self._update_page_actions()
        self._update_history_actions()
        # OCR 仅在依赖可用时启用
        self.ocr_action.setEnabled(active and OCR_AVAILABLE)
        if not OCR_AVAILABLE:
            self.ocr_action.setToolTip("OCR 不可用：未安装 rapidocr-onnxruntime")
        if not active:
            self.find_next_action.setEnabled(False)
            self.find_prev_action.setEnabled(False)

    def _on_document_closed(self) -> None:
        self._reset_document_session()
        self.pdf_view.clear()
        self.sidebar.setMaximumWidth(0)
        self.sidebar.setVisible(False)
        self.tool_hint.hide()
        self._update_title()
        self._set_document_state(False)
        self._update_undo_actions()
        self._sync_tab_bar()
        self.status_bar.show_message("")
        self._show_library()

    def _on_open_failed(self, result: str, message: str) -> None:
        self._last_open_failure = result
        if result in (OpenResult.NEEDS_PASSWORD, OpenResult.WRONG_PASSWORD):
            return  # 由 open_path 弹密码框处理
        titles = {
            OpenResult.NOT_FOUND: "文件不存在",
            OpenResult.CORRUPT: "文件无法打开",
            OpenResult.UNKNOWN_ERROR: "打开失败",
        }
        QMessageBox.warning(self, titles.get(result, "打开失败"), message)

    def _remember_reading_position(self, tab: DocumentTab | None = None) -> None:
        """记住某个标签（默认当前）的阅读位置与缩放（下次打开回到这里）。"""
        tab = tab or self._tab
        doc = tab.controller.document
        if doc is None or tab.view.page_count == 0:
            return
        page, offset = tab.view.reading_position()
        self._settings.remember_position(
            doc.path, ReadingPosition(page, round(offset, 4), round(tab.view.zoom, 4))
        )
        self._save_settings()

    # ------------------------------------------------------------------ 文献库首页
    def _library_visible(self) -> bool:
        return self._shell.currentWidget() is self.library

    def _show_library(self, *, animate: bool = True) -> None:
        """切到文献库首页（数据按最近文件 / 摘要 / 阅读位置 / 打开的标签重建）。"""
        was_visible = self._library_visible()
        self.cite_popover.hide()
        self._refresh_library()
        self._set_reader_shortcuts(False)
        self._shell.setCurrentWidget(self.library)
        self.setWindowTitle("Lumen")
        if animate and not was_visible:
            self.library.animate_in()

    def _show_reader(self) -> None:
        if not self._library_visible():
            return
        self._set_reader_shortcuts(True)
        self._shell.setCurrentWidget(self._reader)
        if self._tab.has_document:
            self._update_title()
            motion.fade_up(self._stack)

    def _set_reader_shortcuts(self, enabled: bool) -> None:
        """文献库显示时停用阅读器快捷键（「打开」照常可用），回到阅读器时恢复。"""
        if enabled:
            for act, keys in self._parked_shortcuts.items():
                act.setShortcuts(keys)
            self._parked_shortcuts.clear()
            return
        for act in self.actions():
            if act is self.open_action or act in self._parked_shortcuts or not act.shortcuts():
                continue
            self._parked_shortcuts[act] = act.shortcuts()
            act.setShortcuts([])

    def _go_home(self) -> None:
        """功能区品牌 / 网格按钮：回文献库（标签与文档都保留，可「返回阅读」）。"""
        if self._library_visible():
            return
        self._remember_reading_position()
        self._show_library()

    def _return_to_reader(self) -> None:
        if self._tab.has_document:
            self._show_reader()
            self._tab.view.setFocus()

    def _refresh_library(self) -> None:
        """按最新设置重建文献库卡片，并更新「返回阅读」。"""
        self.library.set_entries(self._library_entries())
        doc = self._controller.document
        if doc is not None:
            self.library.set_can_return(True, f"回到 {doc.path.name}")
        else:
            self.library.set_can_return(False)

    def _library_entries(self) -> list[LibraryEntry]:
        """最近文件（按最近打开排序）→ 文献库卡片；找不到的文件不列出（设置里仍保留）。"""
        open_tabs = {
            os.path.normcase(str(t.path)): t
            for t in self._tabs
            if t.has_document and t.path is not None
        }
        entries: list[LibraryEntry] = []
        for raw in self._settings.recent_files:
            path = Path(raw)
            tab = open_tabs.get(os.path.normcase(raw))
            if tab is None and not path.exists():
                continue
            meta = self._settings.meta_for(raw) or DocMeta()
            pages = meta.pages
            if tab is not None:
                pages = tab.controller.page_count or pages
                page = self._page_of(tab)
            else:
                position = self._settings.position_for(raw)
                page = position.page if position is not None else -1
            progress = min(1.0, (page + 1) / pages) if pages > 0 and page >= 0 else 0.0
            entries.append(
                LibraryEntry(
                    path=raw,
                    title=display_title(meta.title, path),
                    author=meta.author,
                    pages=pages,
                    annotations=meta.annotations,
                    progress=progress,
                    opened_at=meta.opened_at,
                    favorite=self._settings.is_favorite(raw),
                    is_open=tab is not None,
                )
            )
        return entries

    def _record_meta(self, tab: DocumentTab, *, opened: bool) -> None:
        """记下文献库卡片要用的摘要（页数 / 批注数 / 标题 / 作者 / 打开时间）。"""
        doc = tab.controller.document
        if doc is None:
            return
        old = self._settings.meta_for(doc.path) or DocMeta()
        count = tab.annotation_count
        self._settings.remember_meta(
            doc.path,
            DocMeta(
                pages=tab.controller.page_count,
                annotations=count if count is not None else old.annotations,
                title=doc.meta.title,
                author=doc.meta.author,
                opened_at=time.time() if opened or old.opened_at <= 0 else old.opened_at,
            ),
        )

    def _on_favorite_toggled(self, path: str) -> None:
        # 反馈就是卡片星标本身（状态栏在阅读器里，首页看不到）
        self._settings.toggle_favorite(path)
        self._save_settings()
        self._refresh_library()

    def _on_library_view_changed(self, mode: str) -> None:
        self._settings.library_view = mode
        self._save_settings()

    def _on_explore_ai(self) -> None:
        """「探索 AI 助读」：有文档就回去打开 AI 面板，否则先打开最近一篇（或选文件）。"""
        if not self._tab.has_document:
            entries = self.library.entries()
            if entries:
                self.open_path(entries[0].path)
            else:
                self._on_open()
        if self._tab.has_document:
            self._show_reader()
            self._set_ai_panel_visible(True)

    # ------------------------------------------------------------------ 拖放打开
    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        mime = event.mimeData()
        if mime.hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in mime.urls()):
            event.acceptProposedAction()
            self.library.set_dragging(True)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().dragLeaveEvent(event)
        self.library.set_dragging(False)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.library.set_dragging(False)
        paths = [
            u.toLocalFile()
            for u in event.mimeData().urls()
            if u.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            event.acceptProposedAction()
            self._open_paths(paths)

    # ------------------------------------------------------------------ 持久化 / 关闭
    def _save_settings(self) -> None:
        with contextlib.suppress(OSError):
            self._settings_store.save(self._settings)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # 未保存改动：逐个标签切过去询问；任一处取消则不关闭
        for tab in list(self._tabs):
            if tab.has_document and tab.controller.is_modified:
                self._activate(tab)
                if not self._maybe_save():
                    event.ignore()
                    return
        for tab in self._tabs:
            self._remember_reading_position(tab)
        # 记录窗口状态
        self._settings.window_maximized = self.isMaximized()
        if not self.isMaximized():
            self._settings.window_width = self.width()
            self._settings.window_height = self.height()
        self._save_settings()
        self._shutting_down = True  # 下面逐个关文档时不再切换标签 / 刷新界面
        for tab in self._tabs:
            if tab.has_document:
                tab.controller.close_document()
        self._wait_for_background_workers()
        self._materials.close()
        self._cleanup_snapshots()
        super().closeEvent(event)


def _dot_icon(color: str) -> QIcon:
    """阅读模式菜单里的小色点（设计稿 w-3 h-3 rounded-full border）。"""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QColor("#A1A1AA"))
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, 12, 12)
    painter.end()
    return QIcon(pixmap)
