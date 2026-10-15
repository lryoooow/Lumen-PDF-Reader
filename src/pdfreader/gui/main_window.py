"""主窗口（Lumen 设计稿）。

- 顶部：标题栏（品牌 / 文档名 · 选项卡 · 主题切换 · 关于）+ 按组标题分组的功能区
- 中部：左侧边栏（目录 / 批注 / 缩略图，宽度动画显隐）+ 欢迎页 ⇄ 阅读区；
  阅读区上叠加搜索浮层、工具提示胶囊，点击引用弹出参考文献预览卡
- 底部：状态栏（翻页 · 阅读进度 · 自动消失的消息 · 阅读模式标签 · 缩放滑杆）

职责：把用户动作接到控制器 / 后台线程，把结果反馈到界面。领域逻辑在 core / controllers，
失败原因经 ``DocumentController.last_error`` 如实展示（不静默）。
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Callable
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

from pdfreader.config.settings import Bookmark, ReadingPosition, SettingsStore
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
from pdfreader.gui.panels import AiPanel, SearchPanel, SidePanel
from pdfreader.gui.panels.ai_panel import AI_PANEL_WIDTH
from pdfreader.gui.panels.sidebar import SIDEBAR_WIDTH
from pdfreader.gui.theme import get_theme
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import PdfView, Ribbon, StatusBar, WelcomeWidget, motion
from pdfreader.gui.widgets.overlays import CitePopover, ToolHint
from pdfreader.gui.widgets.pdf_view import ReadingMode
from pdfreader.services.ocr import OCR_AVAILABLE
from pdfreader.utils.text import is_cjk_char
from pdfreader.workers import OcrWorker, SearchWorker

__all__ = ["MainWindow"]

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
        self._controller = DocumentController()
        self._annotation_controller = AnnotationController()
        self._search_worker: SearchWorker | None = None
        self._retired_search_workers: list[SearchWorker] = []
        self._active_query: str = ""
        self._ocr_worker: OcrWorker | None = None
        self._retired_ocr_workers: list[OcrWorker] = []
        self._ocr_results: dict[int, str] = {}
        self._ocr_progress: QProgressDialog | None = None
        self._snapshot_dir = Path(tempfile.gettempdir()) / f"pdfreader_snapshots_{os.getpid()}"
        self._snapshot_paths: list[Path] = []
        self._temp_files: list[Path] = []  # 其他本进程生成的临时文件（如手绘签名）
        self._last_open_failure: str = ""
        # 导航历史（链接 / 目录 / 搜索结果跳转后可「后退」）：(页, 页内偏移)
        self._back_stack: list[tuple[int, float]] = []
        self._forward_stack: list[tuple[int, float]] = []
        self._annotations_stale = True

        # 设置持久化（主题、窗口尺寸、最近文件、阅读位置……）
        self._settings_store = SettingsStore()
        self._settings = self._settings_store.load()

        # 依据设置应用主题（在建 UI 之前，避免闪烁）
        self._theme.set_theme(self._settings.theme)
        self._theme.apply()

        self._setup_window()
        self._create_actions()
        self._setup_ui()
        self._ai = AiAssistant(
            self,
            self._controller,
            self.ai_panel,
            current_page=self._current_page,
            navigate=self._navigate_to,
            show_panel=lambda: self._set_ai_panel_visible(True),
            notify=self.status_bar.show_message,
            on_annotation_added=self._after_annotation_change,
            start_ocr=self._on_ocr_all_pages if OCR_AVAILABLE else None,
        )
        self._connect_signals()
        self._theme.changed.connect(self._on_theme_changed)

        self.pdf_view.set_annotation_controller(self._annotation_controller)
        self.pdf_view.highlight_color = self._settings.highlight_color
        self.pdf_view.set_reading_mode(self._settings.reading_mode)
        self.welcome.set_recent(self._settings.recent_files)
        self._set_document_state(False)

        if self._settings.window_maximized:
            QTimer.singleShot(0, self.showMaximized)

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
        root = QWidget()
        root.setObjectName("MainRoot")
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
                (
                    "划词与框选",
                    [self.translate_action, self.ai_explain_action, self.capture_action],
                ),
                ("模型", [self.ai_settings_action]),
            ],
        )
        self.ribbon.add_trailing_action(self.about_action)
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
        self.ribbon.set_menu(self.color_action, self._build_swatch_menu())

        # 内容区：侧边栏（宽度动画）| 欢迎页 / 阅读区
        self._content = _ContentArea()
        content_layout = QHBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.sidebar = SidePanel()
        self.sidebar.setMaximumWidth(0)
        self.sidebar.setVisible(False)
        self._stack = QStackedWidget()
        self.welcome = WelcomeWidget(self._theme)
        self.pdf_view = PdfView()
        self._stack.addWidget(self.welcome)
        self._stack.addWidget(self.pdf_view)
        content_layout.addWidget(self.sidebar)
        content_layout.addWidget(self._stack, stretch=1)
        self.ai_panel = AiPanel()
        self.ai_panel.setMaximumWidth(0)
        self.ai_panel.setVisible(False)
        content_layout.addWidget(self.ai_panel)
        layout.addWidget(self._content, stretch=1)

        # 浮层：搜索卡片、工具提示胶囊、参考文献预览卡
        self.search_panel = SearchPanel("搜索", self._content)
        self.search_panel.searchRequested.connect(self._on_search_requested)
        self.search_panel.resultClicked.connect(self._on_search_result_clicked)
        self.search_panel.closed.connect(self.pdf_view.clear_search_hit)
        self.search_panel.closed.connect(lambda: self.pdf_view.setFocus())
        self._content.set_overlay(self.search_panel)
        self.tool_hint = ToolHint(self._content)
        self._content.tool_hint = self.tool_hint
        self._content.reading_area = self._stack
        self.cite_popover = CitePopover(self)
        self.cite_popover.jumpRequested.connect(self._on_cite_jump)
        self._pending_link: tuple[int, float | None] | None = None

        # 状态栏
        self.status_bar = StatusBar(self._theme)
        layout.addWidget(self.status_bar)

        self.setCentralWidget(root)

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
        self._controller.documentOpened.connect(self._on_document_opened)
        self._controller.documentClosed.connect(self._on_document_closed)
        self._controller.openFailed.connect(self._on_open_failed)

        self.welcome.openRequested.connect(self._on_open)
        self.welcome.openRecentRequested.connect(self._on_open_recent)

        self.pdf_view.pageChanged.connect(self._on_page_changed)
        self.pdf_view.renderRequested.connect(self._render_page)
        self.pdf_view.zoomChanged.connect(lambda _z: self._update_zoom_label())
        self.pdf_view.set_text_provider(self._controller.get_words)
        self.pdf_view.set_link_provider(self._controller.get_links)
        self.pdf_view.textCopied.connect(
            lambda n: self.status_bar.show_message(f"已复制 {n} 个字符")
        )
        self.pdf_view.linkActivated.connect(self._on_link_activated)
        self.pdf_view.markupRequested.connect(self._on_markup_requested)
        self.pdf_view.placementFinished.connect(self._on_placement_finished)
        self.pdf_view.aiRequested.connect(self._on_ai_requested)
        self.pdf_view.areaCaptured.connect(self._on_area_captured)
        self.ai_panel.closeRequested.connect(lambda: self._set_ai_panel_visible(False))
        self.ai_panel.captureRequested.connect(self._start_capture)

        self.status_bar.prevRequested.connect(self._prev_page)
        self.status_bar.nextRequested.connect(self._next_page)
        self.status_bar.pageEntered.connect(self._goto_page)
        self.status_bar.zoomInRequested.connect(self._zoom_in)
        self.status_bar.zoomOutRequested.connect(self._zoom_out)
        self.status_bar.fitRequested.connect(self._fit_width)
        self.status_bar.zoomPercentRequested.connect(self._on_zoom_percent)
        self.ribbon.themeToggleRequested.connect(self._toggle_theme)

        # 批注提交由视图在松开鼠标后发出（拖拽时已实时预览）
        self.pdf_view.annotationHighlight.connect(self._on_annot_highlight)
        self.pdf_view.annotationInk.connect(self._on_annot_ink)
        self.pdf_view.annotationNote.connect(self._on_annot_text)
        self.pdf_view.eraseRequested.connect(self._on_erase)
        self.pdf_view.placementRequested.connect(self._on_placement)
        self._annotation_controller.modeChanged.connect(self._on_annot_mode_changed)
        self._controller.undoStateChanged.connect(self._update_undo_actions)

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
        self.sidebar.tabs.currentChanged.connect(lambda _i: self._refresh_annotations_if_shown())

    # ------------------------------------------------------------------ 主题
    def _on_theme_mode(self, mode: str) -> None:
        self._theme.set_mode(mode)

    def _toggle_theme(self) -> None:
        """标题栏的浅 / 深色切换（设计稿只有这一个开关）。"""
        self._theme.set_mode("light" if self._theme.is_dark else "dark")

    def _on_theme_changed(self, _name: str) -> None:
        self.pdf_view.apply_palette(self._theme.palette)
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
        page = self.pdf_view.current_page_num
        count = self._controller.page_count
        if page is None or count == 0:
            return 0
        return min(max(page, 0), count - 1)

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
            self._settings.add_recent_file(Path(path))
            self._save_settings()
            return True
        QMessageBox.warning(self, "保存失败", self._controller.last_error or "保存失败")
        return False

    def _after_saved(self) -> None:
        doc = self._controller.document
        if doc is not None:
            self.status_bar.show_message(f"已保存到 {doc.path}")
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
        self.setWindowTitle(f"{'* ' if dirty else ''}{doc.path.name} — Lumen")
        self.ribbon.set_document_title(f"{doc.path.name}{' *' if dirty else ''}")

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
        self.pdf_view.highlight_color = color
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
            self.sidebar.set_annotations(self._controller.list_all_annotations())
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
        end = SIDEBAR_WIDTH if visible else 0
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
        self.pdf_view.set_reading_mode(mode)
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
        text = self.pdf_view.selected_text.strip()
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
        """右键菜单：AI 翻译 / 解释 / 问 AI（把选中文字引用进输入框）。"""
        if kind == "translate":
            self._on_translate()
        elif kind == "explain":
            self._on_ai_explain()
        elif kind == "ask":
            text, _page, _rect = self._selection_context()
            self._set_ai_panel_visible(True)
            self.ai_panel.prefill(f"关于「{text}」：" if text else "")

    def _start_capture(self) -> None:
        if not self._controller.has_document:
            self.status_bar.show_message("请先打开文档")
            return
        self._annotation_controller.set_mode(AnnotationMode.CAPTURE)

    def _on_area_captured(self, page_num: int, rect: Rect) -> None:
        self._annotation_controller.set_mode(AnnotationMode.NONE)
        self._ai.explain_area(page_num, rect)

    def _set_ai_panel_visible(self, visible: bool) -> None:
        """右侧 AI 面板：与左侧边栏同样的宽度动画。"""
        self.ai_panel_action.setChecked(visible)
        panel = self.ai_panel
        if visible:
            self._ai.refresh_panel()
        start = panel.width() if panel.isVisible() else 0
        end = AI_PANEL_WIDTH if visible else 0
        if visible:
            panel.setVisible(True)
        if not motion.enabled() or start == end:
            panel.setMaximumWidth(end)
            panel.setVisible(visible)
        else:
            anim = motion.animate_property(
                panel, b"maximumWidth", start, end, TOKENS.anim_sidebar, owner=panel
            )
            if anim is not None and not visible:
                anim.finished.connect(lambda: panel.setVisible(self.ai_panel_action.isChecked()))
        if visible:
            panel.input.setFocus()

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
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            # 已在运行：再点一次不能开第二个线程（旧 worker 引用被覆盖后 wait 不到）
            self.status_bar.show_message("OCR 正在进行中，请等当前任务结束")
            return
        source = self._worker_source()
        if source is None:
            return
        self._ocr_results.clear()  # 每轮独立，避免上一轮文本混进结果
        self._ocr_progress = QProgressDialog("OCR 识别中…", "取消", 0, 100, self)
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

    def _on_ocr_page_done(self, result) -> None:  # type: ignore[no-untyped-def]
        self._ocr_results[result.page_num] = result.text
        self._ai.add_ocr_text(result.page_num, result.text)

    def _on_ocr_progress(self, done: int, total: int) -> None:
        if self._ocr_progress is not None and total > 0:
            self._ocr_progress.setValue(int(done * 100 / total))

    def _on_ocr_all_done(self, count: int) -> None:
        self._close_ocr_progress()
        self._show_ocr_results("OCR 完成", f"已识别 {count} 页，但未识别到任何文字。", False)

    def _on_ocr_failed(self, message: str) -> None:
        self._close_ocr_progress()
        QMessageBox.warning(self, "OCR 失败", message)

    def _on_ocr_cancelled(self, count: int) -> None:
        """用户取消：与「完成」区分开；已完成的页仍可查看。"""
        self._close_ocr_progress()
        self._show_ocr_results("OCR 已取消", f"已取消，取消前完成 {count} 页。", True)

    def _show_ocr_results(self, title: str, empty_message: str, cancelled: bool) -> None:
        if not any(text.strip() for text in self._ocr_results.values()):
            QMessageBox.information(self, title, empty_message)
            return
        from pdfreader.gui.dialogs import OcrResultDialog

        doc = self._controller.document
        OcrResultDialog(
            dict(self._ocr_results),
            self,
            title=title,
            default_dir=doc.path.parent if doc is not None else None,
            cancelled=cancelled,
        ).exec()

    def _close_ocr_progress(self) -> None:
        if self._ocr_progress is not None:
            self._ocr_progress.close()
            self._ocr_progress = None

    def _on_ocr_worker_finished(self) -> None:
        """OCR 线程收尾：清引用并交给 Qt 删除（否则连续 OCR 会累积线程对象）。"""
        worker = self.sender()
        if worker is self._ocr_worker:
            self._ocr_worker = None
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
        for w in (self._search_worker, self._ocr_worker):
            if w is not None:
                workers.append(w)
        self._ai.shutdown()
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
        if not self._maybe_save():
            return
        start_dir = ""
        if self._settings.recent_files:
            start_dir = str(Path(self._settings.recent_files[0]).parent)
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", start_dir, _PDF_FILTER)
        if path:
            self.open_path(path)

    def _on_open_recent(self, path: str) -> None:
        if not self._maybe_save():
            return
        self.open_path(path)

    def open_path(self, path: str | Path) -> bool:
        """打开文件（菜单 / 最近文件 / 拖放 / 命令行共用）。加密文档会询问密码。

        调用方负责事先处理当前文档的未保存改动（见 :meth:`_maybe_save`）。
        """
        target = Path(path)
        if not target.exists():
            QMessageBox.warning(self, "文件不存在", f"找不到文件：\n{target}")
            self._settings.remove_recent_file(target)
            self.welcome.set_recent(self._settings.recent_files)
            self._save_settings()
            return False
        self._remember_reading_position()
        password: str | None = None
        for attempt in range(_PASSWORD_ATTEMPTS + 1):
            self._last_open_failure = ""
            if self._controller.open_file(target, password):
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
        if not self._maybe_save():
            return
        self._remember_reading_position()
        self._controller.close_document()

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
        self._close_ocr_progress()
        self._ocr_results.clear()
        self._annotation_controller.set_mode(AnnotationMode.NONE)
        self.select_action.setChecked(True)
        self.pdf_view.clear_pending()
        self._back_stack.clear()
        self._forward_stack.clear()
        self.sidebar.clear()
        self._annotations_stale = True
        self._ai.reset_document()

    def _on_document_opened(self, page_count: int) -> None:
        self._reset_document_session()
        self._stack.setCurrentWidget(self.pdf_view)
        self._update_title()

        # 先定侧边栏宽度再布局：否则「适应宽度」按没有侧边栏的宽度算，随后视口变窄
        # 又要重排一次（中间还会闪出横向滚动条）。打开时直接到位，不做宽度动画。
        self.sidebar_action.setChecked(self._settings.sidebar_visible)
        self.sidebar.setMaximumWidth(SIDEBAR_WIDTH if self._settings.sidebar_visible else 0)
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
            self._settings.add_recent_file(doc.path)
            self.welcome.set_recent(self._settings.recent_files)
            self._save_settings()

    def _set_document_state(self, active: bool) -> None:
        """有无文档时统一切换所有相关动作与控件的可用性。"""
        for act in (
            self.close_doc_action,
            self.save_action,
            self.save_as_action,
            self.print_action,
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
        self._stack.setCurrentWidget(self.welcome)
        self.welcome.set_recent(self._settings.recent_files)
        self._update_title()
        self._set_document_state(False)
        self._update_undo_actions()
        self.status_bar.show_message("")

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

    def _remember_reading_position(self) -> None:
        """记住当前文档的阅读位置与缩放（下次打开回到这里）。"""
        doc = self._controller.document
        if doc is None or self.pdf_view.page_count == 0:
            return
        page, offset = self.pdf_view.reading_position()
        self._settings.remember_position(
            doc.path, ReadingPosition(page, round(offset, 4), round(self.pdf_view.zoom, 4))
        )
        self._save_settings()

    # ------------------------------------------------------------------ 拖放打开
    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        mime = event.mimeData()
        if mime.hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in mime.urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".pdf"):
                if not self._maybe_save():
                    return
                self.open_path(local)
                event.acceptProposedAction()
                return

    # ------------------------------------------------------------------ 持久化 / 关闭
    def _save_settings(self) -> None:
        with contextlib.suppress(OSError):
            self._settings_store.save(self._settings)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # 未保存改动：询问；取消则不关闭
        if not self._maybe_save():
            event.ignore()
            return
        self._remember_reading_position()
        # 记录窗口状态
        self._settings.window_maximized = self.isMaximized()
        if not self.isMaximized():
            self._settings.window_width = self.width()
            self._settings.window_height = self.height()
        self._save_settings()
        if self._controller.has_document:
            self._controller.close_document()
        self._wait_for_background_workers()
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
