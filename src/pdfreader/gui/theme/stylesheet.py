"""从配色板生成全局 QSS（Lumen 设计稿）。

控件通过 ``objectName`` 与动态属性（``variant`` / ``danger`` / ``current``）命中样式。
设计稿中的 Tailwind 尺寸（px）在这里换算为 Qt 像素；字号按 px 写，避免 pt 在高分屏上放大失真。
"""

from __future__ import annotations

from dataclasses import asdict
from string import Template

from pdfreader.gui.theme.palette import TOKENS, Palette

__all__ = ["build_stylesheet"]


_TEMPLATE = Template(
    """
/* ================================================================ 基础 */
QWidget {
    color: $text;
    font-family: $font_family;
    font-size: 13px;
}
QMainWindow, QDialog, #MainRoot, #ContentArea { background-color: $window; }
QLabel { background: transparent; }
QLabel[variant="heading"]  { font-size: 18px; font-weight: 600; }
QLabel[variant="title"]    { font-size: 28px; font-weight: 600; }
QLabel[variant="subtitle"] { font-size: 14px; color: $text_secondary; }
QLabel[variant="muted"]    { color: $text_muted; }
QLabel[variant="secondary"]{ color: $text_secondary; }
QLabel[variant="serif"]    { font-family: $serif_family; }
QToolTip {
    background-color: $popover;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 5px 8px;
    font-size: 12px;
}

/* ================================================================ 标题栏（品牌 + 选项卡 + 主题 / 关于） */
#AppHeader {
    background-color: $surface;
    border-bottom: 1px solid $border;
}
#HeaderBar, #RibbonStack, #RibbonPage { background-color: $surface; }
#BrandName { font-size: 13px; font-weight: 600; color: $text; }
#BrandDoc  { font-size: 13px; color: $text_muted; }
QToolButton#HeaderTab {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    color: $text_muted;
}
QToolButton#HeaderTab:hover { background-color: $surface_hover; }
QToolButton#HeaderTab:checked { color: $text; }
#TabIndicator { background-color: $accent; border-radius: 1px; }
QToolButton#HeaderIconButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
}
QToolButton#HeaderIconButton:hover { background-color: $surface_hover; }
QToolButton#HeaderTextButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
    color: $text_muted;
}
QToolButton#HeaderTextButton:hover { background-color: $surface_hover; color: $text; }

/* ================================================================ 功能区按钮与分组 */
QToolButton#RibbonButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 8px 4px 8px;
    min-width: 40px;
    font-size: 11px;
    color: $text;
}
QToolButton#RibbonButton:hover   { background-color: $surface_hover; }
QToolButton#RibbonButton:pressed { background-color: $surface_hover; padding-top: 7px; }
QToolButton#RibbonButton:checked { background-color: $accent_soft; color: $accent_text; }
QToolButton#RibbonButton:disabled { color: $border_strong; }
QToolButton#RibbonButton[danger="true"] { color: $danger; }
QToolButton#RibbonButton[danger="true"]:hover { background-color: $danger_soft; }
QToolButton#RibbonButton::menu-indicator { image: none; width: 0; }
QToolButton#RibbonButton[dropdown="true"] { padding-right: 14px; }
QToolButton#RibbonButton[dropdown="true"]::menu-indicator {
    image: url($chevron);
    subcontrol-origin: padding;
    subcontrol-position: right bottom;
    width: 10px; height: 10px;
    right: 3px; bottom: 6px;
}
QLabel#RibbonGroupTitle { font-size: 10px; color: $text_muted; }
QFrame#RibbonSeparator { background-color: $border; border: none; }

/* 高亮颜色色板（弹出层） */
#SwatchPopup {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 12px;
}
QToolButton#Swatch {
    border: none;
    border-radius: 12px;
    min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
}
QToolButton#Swatch[current="true"] { border: 2px solid $accent; }

/* ================================================================ 文档标签栏 */
QTabBar#DocTabs {
    background-color: $window;
    border-bottom: 1px solid $border;
}
QTabBar#DocTabs::tab {
    background: transparent;
    border: none;
    border-right: 1px solid $border;
    padding: 6px 8px 6px 14px;
    min-width: 80px;
    max-width: 220px;
    font-size: 12px;
    color: $text_muted;
}
QTabBar#DocTabs::tab:hover { background-color: $surface_hover; color: $text; }
QTabBar#DocTabs::tab:selected {
    background-color: $surface;
    color: $text;
    border-bottom: 2px solid $accent;
}
QTabBar#DocTabs::close-button {
    image: url($close_icon);
    subcontrol-position: right;
    border-radius: 4px;
    width: 14px; height: 14px;
    margin: 2px;
}
QTabBar#DocTabs::close-button:hover { background-color: $surface_active; }
QTabBar#DocTabs QToolButton { background-color: $surface; border: none; }

/* ================================================================ 侧边栏 */
#SidePanel {
    background-color: $surface;
    border-right: 1px solid $border;
}
#Segmented { background-color: $surface_hover; border-radius: 8px; }
#SegmentedThumb { background-color: $surface_active; border-radius: 6px; }
QToolButton#SegmentButton {
    background: transparent;
    border: none;
    padding: 6px 4px;
    font-size: 12px;
    color: $text_muted;
}
QToolButton#SegmentButton:checked { color: $text; }
QLabel#CountBadge {
    background-color: $accent_soft;
    color: $accent_text;
    border-radius: 4px;
    padding: 0px 4px;
    font-size: 10px;
}
QTreeWidget#TocTree, QTreeWidget#BookmarkList, QListWidget#AnnotList, QListWidget#ThumbList {
    background: transparent;
    border: none;
    outline: none;
}
QTreeWidget#TocTree::item, QTreeWidget#BookmarkList::item {
    padding: 6px 4px;
    border-radius: 6px;
}
QTreeWidget#TocTree::item:hover, QTreeWidget#BookmarkList::item:hover { background-color: $surface_hover; }
QTreeWidget#TocTree::item:selected, QTreeWidget#BookmarkList::item:selected { background: transparent; color: $text; }
QTreeWidget#TocTree::branch, QTreeWidget#BookmarkList::branch { background: transparent; image: none; border: none; }
QListWidget#AnnotList::item, QListWidget#ThumbList::item {
    background: transparent;
    border: none;
}
QPushButton#ExportNotesButton {
    background: transparent;
    border: 1px dashed $accent;
    border-radius: 8px;
    color: $accent_text;
    padding: 8px;
    font-size: 12px;
}
QPushButton#ExportNotesButton:hover { background-color: $accent_soft; }
QPushButton#ExportNotesButton:disabled { border-color: $border_strong; color: $text_muted; }

/* ================================================================ 状态栏 */
#StatusBar {
    background-color: $surface;
    border-top: 1px solid $border;
}
#StatusBar QLabel { font-size: 12px; color: $text_muted; }
QToolButton#FlatButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px;
    font-size: 13px;
    color: $text;
}
QToolButton#FlatButton:hover   { background-color: $surface_hover; }
QToolButton#FlatButton:disabled { color: $border_strong; }
QToolButton#StatusTextButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 12px;
    color: $text;
}
QToolButton#StatusTextButton:hover { background-color: $surface_hover; }
QToolButton#StatusTextButton:disabled { color: $border_strong; }
#PageEdit {
    background-color: $surface_hover;
    border: 2px solid transparent;
    border-radius: 4px;
    padding: 1px 2px;
    font-size: 12px;
    min-width: 32px; max-width: 40px;
}
#PageEdit:focus { border: 2px solid $accent_hover; }
#StatusBar QLabel#StatusMessage { color: $text_secondary; }
QLabel#StatusDot { background-color: $success; border-radius: 3px; }
#StatusBar QLabel#ModeChip {
    background-color: $accent_soft;
    color: $accent_text;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
}
#StatusBar QLabel#ZoomLabel { color: $text; min-width: 40px; }
QProgressBar#ReadingProgress {
    background-color: $border;
    border: none;
    border-radius: 2px;
    max-height: 4px; min-height: 4px;
}
QProgressBar#ReadingProgress::chunk { background-color: $accent; border-radius: 2px; }
QSlider#ZoomSlider::groove:horizontal {
    height: 4px;
    background: $border;
    border-radius: 2px;
}
QSlider#ZoomSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider#ZoomSlider::handle:horizontal {
    background: $accent;
    width: 12px; height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
QSlider#ZoomSlider::handle:horizontal:hover { background: $accent_hover; }

/* ================================================================ 欢迎页 */
#WelcomeRoot { background-color: $window; }
QLabel#WelcomeTitle { font-family: $serif_family; font-size: 36px; color: $text; }
QLabel#WelcomeSubtitle { font-size: 14px; color: $text_muted; }
QPushButton#DropZone {
    background: transparent;
    border: 2px dashed $border_strong;
    border-radius: 16px;
    padding: 0px;
}
QPushButton#DropZone:hover { border-color: $accent_hover; background-color: $accent_soft; }
QLabel#DropZoneTitle { font-size: 14px; font-weight: 500; color: $text; }
QLabel#DropZoneHint { font-size: 12px; color: $text_muted; }
QLabel#RecentHeader { font-size: 11px; color: $text_muted; }
QPushButton#RecentRow {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: left;
    font-size: 13px;
}
QPushButton#RecentRow:hover { background-color: $surface_hover; }

/* ================================================================ 搜索浮层 */
#SearchPanel {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 16px;
}
#SearchHeader { border-bottom: 1px solid $border; background: transparent; }
QLineEdit#SearchInput {
    background: transparent;
    border: none;
    padding: 2px 0px;
    font-size: 14px;
}
QLabel#SearchCount { font-size: 11px; color: $text_muted; }
QLabel#SearchFooter {
    font-size: 11px;
    color: $text_muted;
    border-top: 1px solid $border;
    padding: 8px 12px;
}
QListWidget#SearchResults { background: transparent; border: none; outline: none; }
QListWidget#SearchResults::item { border-radius: 8px; padding: 0px; margin: 1px 0px; }
QListWidget#SearchResults::item:hover { background-color: $surface_hover; }
QListWidget#SearchResults::item:selected { background-color: $accent_soft; }

/* ================================================================ 阅读区浮层：工具提示 / 参考文献卡片 */
QLabel#ToolHint {
    background-color: rgba(24, 24, 27, 0.86);
    color: #FFFFFF;
    border-radius: 14px;
    padding: 6px 14px;
    font-size: 12px;
}
#CitePopover {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 12px;
}
QLabel#CiteTitle { color: $accent_text; font-size: 11px; }
QLabel#CiteBody { font-family: $serif_family; font-size: 13px; color: $text; }
QPushButton#LinkButton {
    background: transparent;
    border: none;
    color: $accent_text;
    padding: 0px;
    text-align: left;
    font-size: 12px;
}
QPushButton#LinkButton:hover { text-decoration: underline; }

/* ================================================================ AI 助读面板（右侧抽屉） */
#AiPanel {
    background-color: $surface;
    border-left: 1px solid $border;
}
QLabel#AiTitle { font-size: 14px; font-weight: 600; }
QLabel#AiProvider { font-size: 11px; color: $text_muted; }
QLabel#AiStatus { font-size: 11px; color: $text_muted; }
QToolButton#AiChip {
    background-color: $surface_hover;
    border: none;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#AiChip:hover { background-color: $accent_soft; color: $accent_text; }
QToolButton#AiChip:disabled { color: $border_strong; }
QToolButton#AiChip::menu-indicator { image: none; width: 0; }
QTextBrowser#AiTranscript {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 6px 8px;
    font-size: 13px;
}
#AiInputBox {
    background-color: $popover;
    border: 1px solid $border_strong;
    border-radius: 12px;
}
QPlainTextEdit#AiInput { background: transparent; border: none; padding: 0px; }
QToolButton#AiSendButton {
    background-color: $accent;
    border: none;
    border-radius: 8px;
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
}
QToolButton#AiSendButton:hover { background-color: $accent_hover; }
QToolButton#AiSendButton:disabled { background-color: $border_strong; }
QListWidget#AiProviderList {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
}
QLabel#AiHint { font-size: 11px; color: $text_muted; }

/* ================================================================ 输入控件 */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
    background-color: $popover;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 6px 9px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled, QComboBox:disabled { color: $text_muted; background-color: $window; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
    outline: none;
}

/* ================================================================ 按钮 */
QPushButton {
    background-color: $popover;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 7px 16px;
}
QPushButton:hover   { background-color: $surface_hover; }
QPushButton:pressed { background-color: $surface_hover; }
QPushButton:disabled { color: $text_muted; background-color: $window; }
QPushButton[variant="primary"] {
    background-color: $accent;
    border: 1px solid $accent;
    color: $on_accent;
    font-weight: 600;
}
QPushButton[variant="primary"]:hover   { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background-color: $accent_pressed; border-color: $accent_pressed; }
QPushButton[variant="primary"]:disabled { background-color: $border_strong; border-color: $border_strong; color: $window; }
QPushButton[variant="ghost"] { background: transparent; border: 1px solid transparent; color: $accent_text; }
QPushButton[variant="ghost"]:hover { background-color: $accent_soft; }

/* ================================================================ 通用列表 / 树 */
QListWidget, QTreeWidget {
    background-color: transparent;
    border: none;
    outline: none;
}
QListWidget::item { border-radius: 6px; padding: 6px 8px; }
QListWidget::item:hover    { background-color: $surface_hover; }
QListWidget::item:selected { background-color: $accent_soft; color: $accent_text; }

/* ================================================================ 菜单（下拉） */
QMenu {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 14px 8px 12px;
    border-radius: 8px;
    font-size: 13px;
}
QMenu::item:selected { background-color: $surface_hover; color: $text; }
QMenu::item:checked { color: $accent_text; }
QMenu::indicator { width: 0px; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }

/* ================================================================ 进度条（对话框用） */
QProgressBar {
    background-color: $surface_hover;
    border: none;
    border-radius: 3px;
    text-align: center;
    max-height: 6px;
}
QProgressBar::chunk { background-color: $accent; border-radius: 3px; }

/* ================================================================ 纤细滚动条 */
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(128, 128, 140, 0.30);
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: rgba(128, 128, 140, 0.55); }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: rgba(128, 128, 140, 0.30);
    border-radius: 4px;
    min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: rgba(128, 128, 140, 0.55); }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ================================================================ PDF 视图 */
#PdfView { border: none; background-color: $canvas; }
"""
)


def _svg_file(name: str, path_d: str, color: str) -> str:
    """QSS 用的小图标 SVG（写到临时目录，QSS 的 image 只能引用文件 / 资源）。"""
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.gettempdir()) / "pdfreader_ui"
    path = folder / f"{name}_{color.lstrip('#')}.svg"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2.4" stroke-linecap="round" '
            f'stroke-linejoin="round"><path d="{path_d}"/></svg>',
            encoding="utf-8",
        )
    return path.as_posix()


def _chevron_file(color: str) -> str:
    """下拉按钮的小箭头。"""
    return _svg_file("chevron", "m6 9 6 6 6-6", color)


def build_stylesheet(palette: Palette) -> str:
    """据配色板与令牌生成完整 QSS。"""
    values: dict[str, object] = {**asdict(TOKENS), **asdict(palette)}
    try:
        values["chevron"] = _chevron_file(palette.text_muted)
        values["close_icon"] = _svg_file("close", "M7 7l10 10M17 7 7 17", palette.text_muted)
    except OSError:  # 临时目录不可写：不画图标，不影响功能
        values["chevron"] = ""
        values["close_icon"] = ""
    return _TEMPLATE.substitute(values)
