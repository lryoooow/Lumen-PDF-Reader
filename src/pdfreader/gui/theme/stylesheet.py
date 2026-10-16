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

/* ================================================================ 标题栏（品牌 + 选项卡 + 主题 / 文献库） */
#AppHeader {
    background-color: $surface;
    border-bottom: 1px solid $border;
}
#HeaderBar, #RibbonStack, #RibbonPage { background-color: $surface; }
QToolButton#ReaderBrand {
    background: transparent;
    border: none;
    padding: 0px;
    spacing: 10px;
    font-family: $serif_family;
    font-size: 18px;
    font-weight: 600;
    color: $text;
}
QToolButton#ReaderBrand:hover { color: $accent_text; }
QToolButton#HeaderTab {
    background: transparent;
    border: none;
    padding: 0px 12px;
    font-size: 13px;
    color: $text_secondary;
}
QToolButton#HeaderTab:hover { color: $text; }
QToolButton#HeaderTab:checked { color: $text; font-weight: 500; }
#TabIndicator { background-color: $accent; border-radius: 1px; }
QToolButton#HeaderIconButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
}
QToolButton#HeaderIconButton:hover { background-color: $surface_hover; }
QToolButton#HeaderHomeButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0px 10px;
    min-height: 32px; max-height: 32px;
}
QToolButton#HeaderHomeButton:hover { background-color: $surface_hover; }
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
    padding: 4px 8px 4px 8px;
    min-width: 34px;
    font-size: 11px;
    color: $text_secondary;
}
QToolButton#RibbonButton:hover   { background-color: $surface_hover; color: $text; }
QToolButton#RibbonButton:pressed { background-color: $surface_hover; padding-top: 5px; }
/* 勾选底色由 RibbonButton 自绘（自中心放大），这里只改字色 */
QToolButton#RibbonButton:checked, QToolButton#RibbonButton:checked:hover {
    background: transparent;
    color: $accent_text;
}
QToolButton#RibbonButton:disabled { color: $border_strong; }
QToolButton#RibbonButton[danger="true"]:hover { color: $danger; }
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
#DocTabStrip { background-color: $surface; border-bottom: 1px solid $border; }
QTabBar#DocTabs { background: transparent; border: none; }
QTabBar#DocTabs::tab {
    background: transparent;
    border: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 0px 6px 0px 14px;
    margin-right: 2px;
    font-size: 12px;
    color: $text_secondary;
}
QTabBar#DocTabs::tab:hover:!selected { background-color: $surface_hover; }
QTabBar#DocTabs::tab:selected {
    background-color: $window;
    color: $text;
    border-bottom: 2px solid $accent;
}
QToolButton#DocTabAdd {
    background: transparent;
    border: none;
    border-radius: 6px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
}
QToolButton#DocTabAdd:hover { background-color: $surface_hover; }
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

/* ================================================================ 文献库首页（Studio） */
#LibraryRoot, #StudioMain, #StudioScroll, #StudioScrollBody, #StudioContent { background-color: $window; }
#StudioNav { background-color: $nav; }
#StudioNav QLabel { color: $nav_text; }
#StudioNav QLabel#StudioBrand {
    font-family: $serif_family;
    font-size: 34px;
    color: #F3F3E9;
}
#StudioNav QLabel#NavCaption { color: $nav_muted; font-size: 11px; letter-spacing: 1.2px; padding: 0px 12px; }
#StudioNav QLabel#NavStrong { color: $nav_text; font-size: 13px; }
#StudioNav QLabel#NavSmall { color: $nav_muted; font-size: 11px; }
QPushButton#WorkspaceSwitch, QPushButton#ProfileRow {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0px;
    text-align: left;
}
QPushButton#WorkspaceSwitch:hover { background-color: rgba(255, 255, 255, 0.03); }
#StudioNav QLabel#WorkspaceAvatar {
    background-color: #505A48;
    color: #DCE3D2;
    border: 1px solid rgba(117, 129, 106, 0.33);
    border-radius: 7px;
    font-family: $serif_family;
    font-size: 18px;
}
QLabel#ProfileAvatar, QLabel#MiniAvatar, #StudioNav QLabel#ProfileAvatar {
    background-color: #D7C2AA;
    color: #67584A;
    border-radius: 15px;
    font-family: $serif_family;
    font-size: 14px;
}
QLabel#MiniAvatar { border-radius: 14px; font-size: 12px; }
QPushButton#ImportButton {
    background-color: $import_button;
    border: none;
    border-radius: 7px;
    padding: 0px;
}
QPushButton#ImportButton:hover { background-color: $import_button_hover; }
QPushButton#ImportButton:pressed { background-color: $import_button; }
#StudioNav QLabel#ImportText { color: #FFF6ED; font-size: 13px; }
#StudioNav QLabel#ImportKey { color: rgba(255, 246, 237, 0.6); font-size: 11px; }
QPushButton#NavItem, QPushButton#CollectionItem {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0px;
    text-align: left;
}
QPushButton#NavItem:hover, QPushButton#CollectionItem:hover { background-color: rgba(255, 255, 255, 0.03); }
QPushButton#NavItem:checked { background-color: $nav_active; }
#StudioNav QLabel#NavItemText { color: $nav_text; font-size: 13px; }
#StudioNav QPushButton#NavItem:checked QLabel#NavItemText { color: $nav_bright; }
#StudioNav QLabel#NavItemCount { color: rgba(194, 199, 189, 0.55); font-size: 11px; }
#StudioNav QLabel#CollectionText { color: $nav_text; font-size: 12px; }
#StudioNav QPushButton#CollectionItem:checked QLabel#CollectionText { color: #FFF3D9; }
#PrivacyCard {
    background-color: rgba(255, 255, 255, 0.012);
    border: 1px solid rgba(137, 148, 122, 0.14);
    border-radius: 8px;
}
#StudioNav QLabel#PrivacyTitle { color: $nav_text; font-size: 11px; }
#StudioNav QLabel#PrivacyBody { color: $nav_muted; font-size: 11px; }
QFrame#NavRule { background-color: rgba(255, 255, 255, 0.07); border: none; }
QToolButton#NavIconButton {
    background: transparent;
    border: none;
    border-radius: 7px;
    min-width: 40px; min-height: 40px;
}
QToolButton#NavIconButton:hover { background-color: rgba(255, 255, 255, 0.05); }

#StudioTopbar { background-color: $window; border-bottom: 1px solid $border; }
QLabel#CrumbMuted { color: $text_muted; font-size: 12px; }
QLabel#CrumbCurrent { color: $text; font-size: 12px; font-weight: 500; }
QLabel#LocalStatus { color: $text_secondary; font-size: 11px; }
QFrame#TopbarDivider { background-color: $border; border: none; }
QToolButton#TopbarIcon {
    background: transparent;
    border: none;
    border-radius: 8px;
    min-width: 30px; min-height: 30px;
}
QToolButton#TopbarIcon:hover { background-color: $surface_hover; }
QPushButton#ReturnButton {
    background-color: $accent_soft;
    border: 1px solid transparent;
    border-radius: 7px;
    color: $accent_text;
    padding: 6px 12px;
    font-size: 12px;
}
QPushButton#ReturnButton:hover { border-color: $accent; }

QLabel#Eyebrow { color: $text_muted; font-size: 11px; letter-spacing: 2.1px; }
QLabel#GreetingTitle {
    font-family: $serif_family;
    font-size: 36px;
    color: $text;
    letter-spacing: 1px;
}
QLabel#GreetingTitle[size="md"] { font-size: 32px; }
QLabel#GreetingTitle[size="sm"] { font-size: 28px; }
QLabel#GreetingTitle[size="xs"] { font-size: 23px; }
QLabel#GreetingBody { color: $text_muted; font-size: 13px; letter-spacing: 0.3px; }
QLabel#GreetingAside { color: $text_muted; font-size: 11px; }

#ContinueCopy QLabel { color: $hero_ink; }
#ContinueCopy QLabel#SectionKicker { font-size: 11px; letter-spacing: 0.6px; }
#ContinueCopy QLabel#ContinueTitle {
    font-family: $serif_family;
    font-size: 29px;
}
#ContinueCopy QLabel#ContinueTitle[size="xl"] { font-size: 34px; }
#ContinueCopy QLabel#ContinueTitle[size="md"] { font-size: 25px; }
#ContinueCopy QLabel#ContinueTitle[size="xs"] { font-size: 24px; }
#ContinueCopy QLabel#ContinueSubtitle { font-size: 12px; }
#ContinueCopy QLabel#ContinueMeta { font-size: 11px; }
#ContinueCopy QLabel#ContinueRead { font-size: 10px; }
QPushButton#ContinueButton {
    background-color: $hero_button;
    color: #F0F0E7;
    border: none;
    border-radius: 5px;
    padding: 10px 14px;
    min-width: 96px;
    font-size: 12px;
}
QPushButton#ContinueButton:hover { background-color: $hero_button_hover; }

QLabel#InsightKicker { color: #9C8B72; font-size: 10px; letter-spacing: 1.4px; }
QLabel#InsightTitle {
    font-family: $serif_family;
    font-size: 27px;
    color: $text;
    letter-spacing: 1px;
}
QLabel#InsightBody { color: $text_secondary; font-size: 12px; }
QPushButton#InsightButton {
    background: transparent;
    border: none;
    padding: 0px;
    color: $insight_ink;
    font-size: 12px;
}
QPushButton#InsightButton:hover { color: $accent; }
QLabel#InsightFootnote { color: #9B8B77; font-size: 10px; }

QLabel#LibraryHeading { font-size: 18px; font-weight: 500; color: $text; }
QLabel#LibraryCount { font-family: $serif_family; font-size: 14px; color: $text_muted; }
QLabel#LibraryDesc { font-size: 12px; color: $text_muted; }
#LibrarySearch {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 6px;
}
QLineEdit#LibrarySearchInput {
    background: transparent;
    border: none;
    padding: 0px;
    font-size: 12px;
}
QLabel#Kbd {
    color: $text_muted;
    font-size: 10px;
    border: 1px solid $border;
    border-radius: 3px;
    padding: 0px 4px;
}
QComboBox#LibrarySort {
    background: transparent;
    border: none;
    color: $text_secondary;
    font-size: 12px;
    padding: 4px 4px;
    min-width: 76px;
}
QComboBox#LibrarySort::drop-down { width: 16px; }
QComboBox#LibrarySort::down-arrow { image: url($chevron); width: 10px; height: 10px; }
#ViewToggle { border: 1px solid $border; border-radius: 6px; background: transparent; }
QToolButton#ViewToggleButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 4px;
}
QToolButton#ViewToggleButton:hover { background-color: $surface_hover; }
QToolButton#ViewToggleButton:checked { background-color: $cream; }
QPushButton#FilterChip {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 6px 12px;
    font-size: 12px;
    color: $text_muted;
}
QPushButton#FilterChip:hover { color: $accent; }
QPushButton#FilterChip:checked { background-color: $card; border-color: $border; color: $text; }
QLabel#LibraryHint { font-size: 11px; color: $text_muted; letter-spacing: 0.5px; }

#BookCard { background-color: $card; border: 1px solid $border; border-radius: 9px; }
QLabel#BookTag { font-size: 10px; color: $text_muted; }
QLabel#BookTagPdf { font-size: 9px; color: $text_muted; letter-spacing: 0.7px; }
QPushButton#BookTitle {
    background: transparent;
    border: none;
    padding: 0px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
    color: $text;
}
QPushButton#BookTitle[compact="true"] { font-size: 14px; }
QPushButton#BookTitle:hover { color: $accent; }
QLabel#BookInfo { font-size: 10px; color: $text_muted; }
QToolButton#BookStar { background: transparent; border: none; }

#LibraryEmpty QLabel#EmptyTitle { font-size: 16px; color: $text; }
#LibraryEmpty QLabel#EmptyBody { font-size: 12px; color: $text_muted; }
QPushButton#EmptyAction {
    background: transparent;
    border: none;
    color: $accent;
    font-size: 12px;
    padding: 4px 8px;
}
QPushButton#EmptyAction:hover { text-decoration: underline; }

QPushButton#QuietImport {
    background: transparent;
    border: 1px dashed $border_strong;
    border-radius: 7px;
    padding: 0px;
}
QPushButton#QuietImport:hover, QPushButton#QuietImport[dragging="true"] {
    background-color: $accent_soft;
    border-color: $accent;
}
QLabel#QuietImportText { font-size: 12px; color: $text_muted; }
QLabel#QuietImportCta { font-size: 12px; color: $accent; }
QLabel#QuietImportMuted { font-size: 11px; color: $text_muted; }

QLabel#FooterMark { font-family: $serif_family; font-size: 19px; color: $text_secondary; }
QLabel#FooterText, QLabel#FooterDot { font-size: 11px; color: $text_muted; }
QPushButton#FooterLink {
    background: transparent;
    border: none;
    padding: 0px;
    font-size: 11px;
    color: $text_muted;
}
QPushButton#FooterLink:hover { color: $accent; }

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
QScrollArea#AiChat, QWidget#AiChatBody { background: transparent; border: none; }
#AiUserBubble {
    background-color: $accent_soft;
    border-radius: 14px;
    border-top-right-radius: 4px;
}
QLabel#AiUserText { font-size: 13px; color: $text; }
#AiBotBubble {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 14px;
    border-top-left-radius: 4px;
}
QLabel#AiAnswer { font-size: 13px; color: $text; }
QLabel#AiWaitText { font-size: 12px; color: $text_muted; }
QLabel#AiError { font-size: 12px; color: $danger; }
QLabel#AiQuestion {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 6px 10px;
    font-size: 12px;
    color: $text_secondary;
}
QLabel#AiQuestion:hover { border-color: $accent; color: $accent_text; background-color: $accent_soft; }
QToolButton#AiBubbleAction {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 11px;
    color: $text_muted;
}
QToolButton#AiBubbleAction:hover { background-color: $surface_hover; color: $accent_text; }
#AiContext { background-color: $popover; border-radius: 10px; }
#AiContext[attached="true"] { background-color: $surface_hover; }
#AiContextBar { background-color: $accent; border-radius: 1px; margin: 8px 0px 8px 8px; }
QLabel#AiContextInfo { font-size: 11px; color: $accent_text; }
QLabel#AiContextText { font-family: $serif_family; font-size: 12px; color: $text_secondary; }
QToolButton#AiContextButton {
    background: transparent;
    border: none;
    border-radius: 5px;
    min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
}
QToolButton#AiContextButton:hover { background-color: $surface_hover; }
#AiEmptyIcon { background-color: $accent_soft; border-radius: 16px; }
QLabel#AiEmptyTitle { font-family: $serif_family; font-size: 15px; color: $text; }
QLabel#AiEmptyText { font-size: 12px; color: $text_muted; }
QLabel#AiEmptyNote { font-size: 11px; color: $text_muted; padding-top: 8px; }
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
