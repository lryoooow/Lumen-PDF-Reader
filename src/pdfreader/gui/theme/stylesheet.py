"""从配色板生成全局 QSS（Lumen Pure 设计规范）。

控件通过 ``objectName`` 与动态属性（``variant`` / ``danger`` / ``current``）命中样式。
字号按 px 书写，保证 Windows 多倍率缩放（100% / 125% / 150% / 200%）下排版比例精准一致。
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
QLabel[variant="heading"]  { font-size: 17px; font-weight: 600; }
QLabel[variant="title"]    { font-size: 24px; font-weight: 600; }
QLabel[variant="subtitle"] { font-size: 13px; color: $text_secondary; }
QLabel[variant="muted"]    { color: $text_muted; }
QLabel[variant="secondary"]{ color: $text_secondary; }
QLabel[variant="serif"]    { font-family: $serif_family; }
QToolTip {
    background-color: $popover;
    color: $text;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 5px 9px;
    font-size: 12px;
}

/* ================================================================ 统一顶栏与单行轻工具栏（Lumen Pure） */
#AppHeader {
    background-color: $surface;
    border-bottom: 1px solid $border;
}
#HeaderBar {
    background-color: $surface;
    border-bottom: 1px solid $border;
}
#RibbonStack, #RibbonPage { background-color: $surface; }
QToolButton#ReaderBrand {
    background: transparent;
    border: none;
    padding: 0px 4px 0px 0px;
    spacing: 8px;
    font-family: $serif_family;
    font-size: 16px;
    font-weight: 600;
    color: $text;
}
QToolButton#ReaderBrand:hover { color: $accent_text; }
QToolButton#HeaderTab {
    background: transparent;
    border: none;
    padding: 0px 11px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#HeaderTab:hover { color: $text; }
QToolButton#HeaderTab:checked { color: $accent_text; font-weight: 600; }
#TabIndicator { background-color: $accent; border-radius: 1px; }
QToolButton#HeaderCmdPill {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 0px 10px;
    min-height: 26px; max-height: 26px;
    font-size: 12px;
    color: $text_muted;
}
QToolButton#HeaderCmdPill:hover {
    background-color: $surface_hover;
    border-color: $border_strong;
    color: $text;
}
QToolButton#HeaderIconButton, QToolButton#HeaderHomeButton {
    background: transparent;
    border: none;
    border-radius: 7px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
}
QToolButton#HeaderIconButton:hover, QToolButton#HeaderHomeButton:hover {
    background-color: $surface_hover;
}
QToolButton#HeaderIconButton:checked {
    background-color: $accent_soft;
    color: $accent_text;
}
QToolButton#HeaderTextButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 8px;
    min-height: 26px; max-height: 26px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#HeaderTextButton:hover { background-color: $surface_hover; color: $text; }
QToolButton#HeaderTextButton:checked {
    background-color: $accent_soft;
    border-color: transparent;
    color: $accent_text;
    font-weight: 500;
}

/* ================================================================ 紧凑单行胶囊工具按钮 */
QToolButton#RibbonButton, QToolButton#RibbonOverflowButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px 8px;
    min-width: 28px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#RibbonButton:hover, QToolButton#RibbonOverflowButton:hover {
    background-color: $surface_hover;
    color: $text;
}
QToolButton#RibbonButton:pressed, QToolButton#RibbonOverflowButton:pressed {
    background-color: $surface_active;
}
/* 勾选底色由 RibbonButton 自绘（平滑阻尼过渡），此处改字色与字重 */
QToolButton#RibbonButton:checked, QToolButton#RibbonButton:checked:hover {
    background: transparent;
    color: $accent_text;
    font-weight: 500;
}
QToolButton#RibbonButton:disabled, QToolButton#RibbonOverflowButton:disabled {
    color: $text_disabled;
}
QToolButton#RibbonButton[danger="true"]:hover {
    color: $danger;
    background-color: $danger_soft;
}
QToolButton#RibbonButton::menu-indicator, QToolButton#RibbonOverflowButton::menu-indicator {
    image: none;
    width: 0;
}
QLabel#RibbonGroupTitle {
    font-size: 11px;
    color: $text_muted;
    padding: 0px 4px 0px 2px;
}
QFrame#RibbonSeparator { background-color: $border; border: none; }

/* 高亮颜色色板（弹出层） */
#SwatchPopup {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 10px;
}
QToolButton#Swatch {
    border: none;
    border-radius: 11px;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
}
QToolButton#Swatch[current="true"] { border: 2px solid $accent; }

/* ================================================================ 文档标签栏（轻量胶囊页签） */
#DocTabStrip { background-color: $window; border-bottom: 1px solid $border; }
QTabBar#DocTabs { background: transparent; border: none; }
QTabBar#DocTabs::tab {
    background: transparent;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 0px 6px 0px 12px;
    margin-right: 3px;
    font-size: 12px;
    color: $text_muted;
}
QTabBar#DocTabs::tab:hover:!selected {
    background-color: $surface_hover;
    color: $text_secondary;
}
QTabBar#DocTabs::tab:selected {
    background-color: $surface;
    border-color: $border;
    color: $text;
    font-weight: 500;
    border-bottom: 2px solid $accent;
}
QToolButton#DocTabAdd {
    background: transparent;
    border: none;
    border-radius: 6px;
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
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
QTabBar#DocTabs QToolButton { background-color: $window; border: none; }

/* ================================================================ 左侧导航栏与分段指示器 */
#SidePanel {
    background-color: $surface;
    border-right: 1px solid $border;
}
#Segmented { background-color: $window; border: 1px solid $border; border-radius: 8px; }
#SegmentedThumb {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 6px;
}
QToolButton#SegmentButton {
    background: transparent;
    border: none;
    padding: 5px 4px;
    font-size: 12px;
    color: $text_muted;
}
QToolButton#SegmentButton:hover { color: $text_secondary; }
QToolButton#SegmentButton:checked { color: $text; font-weight: 500; }
QToolButton#SegmentButton[badged="true"] { padding-right: 20px; }
QLabel#CountBadge {
    background-color: $accent_soft;
    color: $accent_text;
    border-radius: 4px;
    padding: 0px 4px;
    font-size: 10px;
    font-weight: 600;
}
QTreeWidget#TocTree, QTreeWidget#BookmarkList, QListWidget#AnnotList, QListWidget#ThumbList {
    background: transparent;
    border: none;
    outline: none;
}
QTreeWidget#TocTree::item, QTreeWidget#BookmarkList::item {
    padding: 6px 6px;
    border-radius: 6px;
}
QTreeWidget#TocTree::item:hover, QTreeWidget#BookmarkList::item:hover { background-color: $surface_hover; }
QTreeWidget#TocTree::item:selected, QTreeWidget#BookmarkList::item:selected {
    background-color: $accent_soft;
    color: $accent_text;
}
QTreeWidget#TocTree::branch, QTreeWidget#BookmarkList::branch { background: transparent; image: none; border: none; }
QListWidget#AnnotList::item, QListWidget#ThumbList::item {
    background: transparent;
    border: none;
}
QPushButton#ExportNotesButton {
    background: transparent;
    border: 1px dashed $border_strong;
    border-radius: 7px;
    color: $accent_text;
    padding: 7px;
    font-size: 12px;
}
QPushButton#ExportNotesButton:hover { background-color: $accent_soft; border-color: $accent; }
QPushButton#ExportNotesButton:disabled { border-color: $border; color: $text_disabled; }

/* ================================================================ 底部极简状态栏 */
#StatusBar {
    background-color: $surface;
    border-top: 1px solid $border;
}
#StatusBar QLabel { font-size: 11px; color: $text_muted; }
QToolButton#FlatButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#FlatButton:hover   { background-color: $surface_hover; color: $text; }
QToolButton#FlatButton:disabled { color: $text_disabled; }
QToolButton#StatusTextButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 11px;
    color: $text_secondary;
}
QToolButton#StatusTextButton:hover { background-color: $surface_hover; color: $text; }
QToolButton#StatusTextButton:disabled { color: $text_disabled; }
#PageEdit {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 1px 2px;
    font-size: 11px;
    min-width: 32px; max-width: 40px;
}
#PageEdit:focus { border: 1px solid $accent; background-color: $surface; }
#StatusBar QLabel#StatusMessage { color: $text_secondary; }
QLabel#StatusDot { background-color: $success; border-radius: 3px; }
#StatusBar QLabel#ModeChip {
    background-color: $accent_soft;
    color: $accent_text;
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 500;
}
#StatusBar QLabel#ZoomLabel { color: $text_secondary; min-width: 38px; font-size: 11px; }
QProgressBar#ReadingProgress {
    background-color: $border;
    border: none;
    border-radius: 2px;
    max-height: 3px; min-height: 3px;
}
QProgressBar#ReadingProgress::chunk { background-color: $accent; border-radius: 2px; }
QSlider#ZoomSlider::groove:horizontal {
    height: 3px;
    background: $border;
    border-radius: 1px;
}
QSlider#ZoomSlider::sub-page:horizontal { background: $accent; border-radius: 1px; }
QSlider#ZoomSlider::handle:horizontal {
    background: $accent;
    width: 10px; height: 10px;
    margin: -4px 0;
    border-radius: 5px;
}
QSlider#ZoomSlider::handle:horizontal:hover { background: $accent_hover; }

/* ================================================================ 文献库工作台（Lumen Pure 清爽学术风） */
#LibraryRoot, #StudioMain, #StudioScroll, #StudioScrollBody, #StudioContent { background-color: $window; }
#StudioNavScroll {
    background-color: $nav;
    border-right: 1px solid $border;
}
#StudioNav { background-color: $nav; }
#StudioNav QLabel { color: $nav_text; }
#StudioNav QLabel#StudioBrand {
    font-family: $serif_family;
    font-size: 22px;
    font-weight: 600;
    color: $nav_bright;
}
#StudioNav QLabel#NavCaption {
    color: $nav_muted;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.6px;
    padding: 0px 10px;
}
#StudioNav QLabel#NavStrong { color: $nav_bright; font-size: 12px; font-weight: 600; }
#StudioNav QLabel#NavSmall { color: $nav_muted; font-size: 11px; }
QPushButton#WorkspaceSwitch, QPushButton#ProfileRow {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0px;
    text-align: left;
}
QPushButton#WorkspaceSwitch:hover, QPushButton#ProfileRow:hover {
    background-color: $surface_hover;
}
#StudioNav QLabel#WorkspaceAvatar {
    background-color: $accent_soft;
    color: $accent_text;
    border: 1px solid $border;
    border-radius: 7px;
    font-weight: 600;
    font-size: 14px;
}
QLabel#ProfileAvatar, QLabel#MiniAvatar, #StudioNav QLabel#ProfileAvatar {
    background-color: $accent_soft;
    color: $accent_text;
    border-radius: 14px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#MiniAvatar { border-radius: 13px; font-size: 11px; }
QPushButton#ImportButton {
    background-color: $import_button;
    border: none;
    border-radius: 8px;
    padding: 0px;
}
QPushButton#ImportButton:hover { background-color: $import_button_hover; }
QPushButton#ImportButton:pressed { background-color: $accent_pressed; }
#StudioNav QLabel#ImportText { color: $on_accent; font-size: 13px; font-weight: 600; }
#StudioNav QLabel#ImportKey { color: $on_accent; font-size: 11px; }
QPushButton#NavItem, QPushButton#CollectionItem {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0px;
    text-align: left;
}
QPushButton#NavItem:hover, QPushButton#CollectionItem:hover { background-color: $surface_hover; }
QPushButton#NavItem:checked, QPushButton#CollectionItem:checked { background-color: $nav_active; }
#StudioNav QLabel#NavItemText { color: $nav_text; font-size: 13px; }
#StudioNav QPushButton#NavItem:checked QLabel#NavItemText { color: $accent_text; font-weight: 600; }
#StudioNav QLabel#NavItemCount { color: $nav_muted; font-size: 11px; }
#StudioNav QPushButton#NavItem:checked QLabel#NavItemCount { color: $accent_text; font-weight: 600; }
#StudioNav QLabel#CollectionText { color: $nav_text; font-size: 12px; }
#StudioNav QPushButton#CollectionItem:checked QLabel#CollectionText { color: $accent_text; font-weight: 600; }
#PrivacyCard {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
}
#StudioNav QLabel#PrivacyTitle { color: $nav_bright; font-size: 11px; font-weight: 600; }
#StudioNav QLabel#PrivacyBody { color: $nav_muted; font-size: 11px; }
QFrame#NavRule { background-color: $border; border: none; }
QToolButton#NavIconButton {
    background: transparent;
    border: none;
    border-radius: 7px;
    min-width: 38px; min-height: 38px;
}
QToolButton#NavIconButton:hover { background-color: $surface_hover; }

#StudioTopbar { background-color: $surface; border-bottom: 1px solid $border; }
QLabel#CrumbMuted { color: $text_muted; font-size: 12px; }
QLabel#CrumbCurrent { color: $text; font-size: 12px; font-weight: 600; }
QLabel#LocalStatus { color: $text_muted; font-size: 11px; }
QFrame#TopbarDivider { background-color: $border; border: none; }
QToolButton#TopbarIcon {
    background: transparent;
    border: none;
    border-radius: 7px;
    min-width: 30px; min-height: 30px;
}
QToolButton#TopbarIcon:hover { background-color: $surface_hover; }
QPushButton#ReturnButton {
    background-color: $accent_soft;
    border: 1px solid transparent;
    border-radius: 7px;
    color: $accent_text;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#ReturnButton:hover { border-color: $accent; }

/* 崩溃恢复提示条 */
QFrame#RecoveryBanner {
    background-color: $accent_soft;
    border: none;
    border-bottom: 1px solid $border;
}
QLabel#RecoveryTitle { color: $text; font-size: 12px; font-weight: 600; }
QLabel#RecoveryText { color: $text_secondary; font-size: 12px; }
QPushButton#RecoveryRestore {
    background-color: $accent;
    border: none;
    border-radius: 6px;
    color: $on_accent;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#RecoveryRestore:hover { background-color: $accent_hover; }
QPushButton#RecoveryDiscard {
    background: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $danger;
    padding: 5px 12px;
    font-size: 12px;
}
QPushButton#RecoveryDiscard:hover { background-color: $danger_soft; }
QPushButton#RecoveryLater {
    background: transparent;
    border: none;
    color: $text_muted;
    padding: 5px 8px;
    font-size: 12px;
}
QPushButton#RecoveryLater:hover { color: $text; }

QLabel#Eyebrow { color: $accent_text; font-size: 11px; font-weight: 600; letter-spacing: 1.2px; }
QLabel#GreetingTitle {
    font-size: 22px;
    font-weight: 600;
    color: $text;
}
QLabel#GreetingTitle[size="md"] { font-size: 20px; }
QLabel#GreetingTitle[size="sm"] { font-size: 18px; }
QLabel#GreetingTitle[size="xs"] { font-size: 17px; }
QLabel#GreetingBody { color: $text_muted; font-size: 12px; }
QLabel#GreetingAside { color: $text_muted; font-size: 11px; }

#ContinueCard {
    background-color: $hero;
    border: 1px solid $border;
    border-radius: 10px;
}
#ContinueCopy QLabel { color: $hero_ink; }
#ContinueCopy QLabel#SectionKicker { color: $accent_text; font-size: 11px; font-weight: 600; }
#ContinueCopy QLabel#ContinueTitle {
    font-size: 16px;
    font-weight: 600;
    color: $text;
}
#ContinueCopy QLabel#ContinueTitle[size="xl"] { font-size: 17px; }
#ContinueCopy QLabel#ContinueTitle[size="md"] { font-size: 15px; }
#ContinueCopy QLabel#ContinueTitle[size="xs"] { font-size: 15px; }
#ContinueCopy QLabel#ContinueSubtitle { font-size: 12px; color: $text_muted; }
#ContinueCopy QLabel#ContinueMeta { font-size: 11px; color: $text_secondary; }
#ContinueCopy QLabel#ContinueRead { font-size: 11px; color: $accent_text; font-weight: 500; }
QPushButton#ContinueButton {
    background-color: $hero_button;
    color: $on_accent;
    border: none;
    border-radius: 7px;
    padding: 7px 14px;
    min-width: 92px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#ContinueButton:hover { background-color: $hero_button_hover; }

#InsightCard {
    background-color: $insight;
    border: 1px solid $border;
    border-radius: 10px;
}
QLabel#InsightKicker { color: $accent_text; font-size: 10px; font-weight: 600; letter-spacing: 0.8px; }
QLabel#InsightTitle {
    font-size: 15px;
    font-weight: 600;
    color: $text;
}
QLabel#InsightBody { color: $text_secondary; font-size: 12px; }
QPushButton#InsightButton {
    background: transparent;
    border: none;
    padding: 0px;
    color: $insight_ink;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#InsightButton:hover { color: $accent; }
QLabel#InsightFootnote { color: $text_muted; font-size: 11px; }

QLabel#LibraryHeading { font-size: 16px; font-weight: 600; color: $text; }
QLabel#LibraryCount {
    background-color: $surface_hover;
    color: $text_secondary;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#LibraryDesc { font-size: 12px; color: $text_muted; }
#LibrarySearch {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 7px;
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
    border-radius: 4px;
    padding: 0px 5px;
    background-color: $window;
}
QComboBox#LibrarySort {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 7px;
    color: $text_secondary;
    font-size: 12px;
    padding: 4px 8px;
    min-width: 82px;
}
QComboBox#LibrarySort:hover { border-color: $border_strong; color: $text; }
QComboBox#LibrarySort::drop-down { width: 16px; }
QComboBox#LibrarySort::down-arrow { image: url($chevron); width: 10px; height: 10px; }
#ViewToggle { border: 1px solid $border; border-radius: 7px; background-color: $surface; }
QToolButton#ViewToggleButton {
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 4px;
}
QToolButton#ViewToggleButton:hover { background-color: $surface_hover; }
QToolButton#ViewToggleButton:checked { background-color: $accent_soft; }
QPushButton#FilterChip {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 11px;
    font-size: 12px;
    color: $text_muted;
}
QPushButton#FilterChip:hover { color: $text; background-color: $surface_hover; }
QPushButton#FilterChip:checked {
    background-color: $accent_soft;
    border-color: transparent;
    color: $accent_text;
    font-weight: 600;
}
QLabel#LibraryHint { font-size: 11px; color: $text_muted; }

#BookCard { background-color: $card; border: 1px solid $border; border-radius: 9px; }
#BookCard:hover { border-color: $border_strong; }
QLabel#BookTag { font-size: 10px; color: $text_muted; }
QLabel#BookTagPdf {
    font-size: 9px;
    color: $accent_text;
    background-color: $accent_soft;
    border-radius: 3px;
    padding: 1px 5px;
    font-weight: 600;
}
QPushButton#BookTitle {
    background: transparent;
    border: none;
    padding: 0px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
    color: $text;
}
QPushButton#BookTitle[compact="true"] { font-size: 13px; }
QPushButton#BookTitle:hover { color: $accent_text; }
QLabel#BookInfo { font-size: 11px; color: $text_muted; }
QToolButton#BookStar { background: transparent; border: none; }

#LibraryEmpty QLabel#EmptyTitle { font-size: 15px; font-weight: 600; color: $text; }
#LibraryEmpty QLabel#EmptyBody { font-size: 12px; color: $text_muted; }
QPushButton#EmptyAction {
    background-color: $accent_soft;
    border: none;
    border-radius: 6px;
    color: $accent_text;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
}
QPushButton#EmptyAction:hover { background-color: $accent; color: $on_accent; }

QPushButton#QuietImport {
    background-color: $surface;
    border: 1px dashed $border_strong;
    border-radius: 8px;
    padding: 0px;
}
QPushButton#QuietImport:hover, QPushButton#QuietImport[dragging="true"] {
    background-color: $accent_soft;
    border-color: $accent;
}
QLabel#QuietImportText { font-size: 12px; color: $text_muted; }
QLabel#QuietImportCta { font-size: 12px; font-weight: 600; color: $accent_text; }
QLabel#QuietImportMuted { font-size: 11px; color: $text_muted; }

QLabel#FooterMark { font-family: $serif_family; font-size: 16px; font-weight: 600; color: $text_secondary; }
QLabel#FooterText, QLabel#FooterDot { font-size: 11px; color: $text_muted; }
QPushButton#FooterLink {
    background: transparent;
    border: none;
    padding: 0px;
    font-size: 11px;
    color: $text_muted;
}
QPushButton#FooterLink:hover { color: $accent_text; }

/* ================================================================ 搜索浮层 */
#SearchPanel {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 12px;
}
#SearchHeader { border-bottom: 1px solid $border; background: transparent; }
QLineEdit#SearchInput {
    background: transparent;
    border: none;
    padding: 2px 0px;
    font-size: 13px;
}
QLabel#SearchCount { font-size: 11px; color: $text_muted; }
QLabel#SearchFooter {
    font-size: 11px;
    color: $text_muted;
    border-top: 1px solid $border;
    padding: 7px 12px;
}
QListWidget#SearchResults { background: transparent; border: none; outline: none; }
QListWidget#SearchResults::item { border-radius: 7px; padding: 0px; margin: 1px 0px; }
QListWidget#SearchResults::item:hover { background-color: $surface_hover; }
QListWidget#SearchResults::item:selected { background-color: $accent_soft; }

/* ================================================================ 阅读区浮层：工具提示 / 参考文献卡片 */
QLabel#ToolHint {
    background-color: rgba(24, 24, 27, 0.88);
    color: #FFFFFF;
    border-radius: 14px;
    padding: 6px 14px;
    font-size: 12px;
}
#CitePopover {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 10px;
}
QLabel#CiteTitle { color: $accent_text; font-size: 11px; font-weight: 600; }
QLabel#CiteBody { font-family: $serif_family; font-size: 13px; color: $text; }
QPushButton#LinkButton {
    background: transparent;
    border: none;
    color: $accent_text;
    padding: 0px;
    text-align: left;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#LinkButton:hover { text-decoration: underline; }

/* ================================================================ 划词情境悬浮胶囊与翻译浮窗 */
#SelectionFloatingBar {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 9px;
}
QToolButton#SelectionSwatchDot {
    border: 1px solid rgba(0, 0, 0, 0.12);
    border-radius: 7px;
    min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px;
}
QToolButton#SelectionSwatchDot:hover { border: 2px solid $text; }
QFrame#SelectionBarSep { background-color: $border; border: none; }
QToolButton#SelectionActionBtn {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#SelectionActionBtn:hover { background-color: $surface_hover; color: $text; }
QToolButton#TranslatePill {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 2px 8px 2px 6px;
    font-size: 12px;
    font-weight: 500;
    color: $accent_text;
}
QToolButton#TranslatePill[embedded="true"] {
    background: transparent;
    border: none;
    border-radius: 6px;
}
QToolButton#TranslatePill:hover { background-color: $accent_soft; border-color: $accent; }

#TranslatePopup {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 10px;
}
QLabel#TranslateTitle { color: $accent_text; font-size: 11px; font-weight: 600; }
QLabel#TranslateSource { font-family: $serif_family; font-size: 12px; color: $text_muted; }
QLabel#TranslateText { font-size: 13px; color: $text; background: transparent; }
QScrollArea#TranslateScroll { background: transparent; border: none; }
QScrollArea#TranslateScroll > QWidget > QWidget { background: transparent; }
QLabel#TranslateNotice { font-size: 12px; color: $text_secondary; }
QToolButton#TranslateTool {
    background: transparent;
    border: none;
    border-radius: 6px;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
}
QToolButton#TranslateTool:hover { background-color: $surface_hover; }
QToolButton#TranslateTool:checked { background-color: $accent_soft; }
QLabel#WordTitle { font-family: $serif_family; font-size: 18px; font-weight: 600; color: $text; }
QLabel#WordPhonetic { font-size: 12px; color: $text_muted; }
QLabel#WordNote { font-size: 11px; color: $text_muted; }
QLabel#WordSenses { font-size: 13px; color: $text; }

/* ================================================================ 右侧多合一学术助读坞（翻译 · AI 导读 · 笔记） */
#AiDock, #RightStudioHeader {
    background-color: $surface;
}
#AiDock {
    border-left: 1px solid $border;
}
#RightStudioHeader {
    border-bottom: 1px solid $border;
}
#TranslateStudioPane {
    background-color: $surface;
}
#StudioCard {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 9px;
}
#StudioAnswerCard {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 9px;
}
QLabel#StudioSectionLabel {
    font-size: 11px;
    font-weight: 600;
    color: $accent_text;
}
QLabel#StudioMutedHint {
    font-size: 12px;
    color: $text_muted;
}
QLabel#StudioTermChip {
    background-color: $accent_soft;
    color: $accent_text;
    border-radius: 5px;
    padding: 3px 8px;
    font-size: 11px;
}
QDockWidget {
    background-color: $surface;
    border-left: 1px solid $border;
    font-size: 12px;
}
QDockWidget::title {
    background-color: $surface;
    border-bottom: 1px solid $border;
    padding: 6px 10px;
}

/* ================================================================ AI 助读面板 */
#AiPanel {
    background-color: $surface;
    border-left: 1px solid $border;
}
#AiPanel[hosted="true"] {
    border-left: none;
}
QLabel#AiTitle { font-size: 13px; font-weight: 600; }
QLabel#AiProvider { font-size: 11px; color: $text_muted; }
QToolButton#AiChip {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 4px 10px;
    font-size: 12px;
    color: $text_secondary;
}
QToolButton#AiChip:hover { background-color: $accent_soft; border-color: $accent; color: $accent_text; }
QToolButton#AiChip:disabled { color: $text_disabled; border-color: $border; }
QToolButton#AiChip::menu-indicator { image: none; width: 0; }
QScrollArea#AiChat, QWidget#AiChatBody { background: transparent; border: none; }
#AiUserBubble {
    background-color: $accent_soft;
    border-radius: 12px;
    border-top-right-radius: 3px;
}
QLabel#AiUserText { font-size: 13px; color: $text; }
#AiBotBubble {
    background-color: $popover;
    border: 1px solid $border;
    border-radius: 12px;
    border-top-left-radius: 3px;
}
QLabel#AiAnswer { font-size: 13px; color: $text; }
QLabel#AiWaitText { font-size: 12px; color: $text_muted; }
QLabel#AiError { font-size: 12px; color: $danger; }
QLabel#AiQuestion {
    background-color: $window;
    border: 1px solid $border;
    border-radius: 8px;
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
#AiContext { background-color: $window; border: 1px solid $border; border-radius: 8px; }
#AiContext[attached="true"] { background-color: $accent_soft; border-color: $accent; }
#AiContextBar { background-color: $accent; border-radius: 1px; margin: 8px 0px 8px 8px; }
QLabel#AiContextInfo { font-size: 11px; color: $accent_text; font-weight: 600; }
QLabel#AiContextText { font-family: $serif_family; font-size: 12px; color: $text_secondary; }
QToolButton#AiContextButton {
    background: transparent;
    border: none;
    border-radius: 5px;
    min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
}
QToolButton#AiContextButton:hover { background-color: $surface_hover; }
#AiEmptyIcon { background-color: $accent_soft; border-radius: 14px; }
QLabel#AiEmptyTitle { font-size: 14px; font-weight: 600; color: $text; }
QLabel#AiEmptyText { font-size: 12px; color: $text_muted; }
QLabel#AiEmptyNote { font-size: 11px; color: $text_muted; padding-top: 8px; }
#AiInputBox {
    background-color: $popover;
    border: 1px solid $border_strong;
    border-radius: 10px;
}
QPlainTextEdit#AiInput { background: transparent; border: none; padding: 0px; }
QToolButton#AiSendButton {
    background-color: $accent;
    border: none;
    border-radius: 7px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
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
    border-radius: 7px;
    padding: 6px 9px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: $text_muted; background-color: $window; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url($chevron); width: 12px; height: 12px; }
QSpinBox { padding-right: 22px; }
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border;
    width: 18px;
    border: none;
    background: transparent;
}
QSpinBox::up-button { subcontrol-position: top right; margin-top: 2px; margin-right: 2px; }
QSpinBox::down-button { subcontrol-position: bottom right; margin-bottom: 2px; margin-right: 2px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: $surface_hover; border-radius: 4px; }
QSpinBox::up-arrow { image: url($chevron_up); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url($chevron); width: 10px; height: 10px; }
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
    border-radius: 7px;
    padding: 6px 14px;
}
QPushButton:hover   { background-color: $surface_hover; }
QPushButton:pressed { background-color: $surface_active; }
QPushButton:disabled { color: $text_disabled; background-color: $window; }
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
    border-radius: 10px;
    padding: 5px;
}
QMenu::item {
    padding: 7px 14px 7px 11px;
    border-radius: 6px;
    font-size: 12px;
}
QMenu::item:selected { background-color: $surface_hover; color: $text; }
QMenu::item:checked { color: $accent_text; font-weight: 500; }
QMenu::indicator { width: 0px; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }

/* ================================================================ 进度条（对话框用） */
QProgressBar {
    background-color: $surface_hover;
    border: none;
    border-radius: 3px;
    text-align: center;
    max-height: 5px;
}
QProgressBar::chunk { background-color: $accent; border-radius: 3px; }

/* ================================================================ 纤细滚动条 */
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(120, 126, 140, 0.28);
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: rgba(120, 126, 140, 0.52); }
QScrollBar:horizontal { background: transparent; height: 7px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: rgba(120, 126, 140, 0.28);
    border-radius: 3px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: rgba(120, 126, 140, 0.52); }
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
        values["chevron_up"] = _svg_file("chevron_up", "m18 15-6-6-6 6", palette.text_muted)
        values["close_icon"] = _svg_file("close", "M7 7l10 10M17 7 7 17", palette.text_muted)
    except OSError:
        values["chevron"] = ""
        values["chevron_up"] = ""
        values["close_icon"] = ""
    return _TEMPLATE.substitute(values)
