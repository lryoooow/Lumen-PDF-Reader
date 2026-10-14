"""顶部区域：标题栏（品牌 · 选项卡 · 主题 / 关于）+ 功能区（Lumen 设计稿）。

结构（高 44 + 68）::

    [L] Lumen / 文件名 *   开始 批注 页面 视图 工具 ……………… [☾] 关于
    ┌ 文件 ─────────┐┌ 编辑 ─┐┌ 工具 ─┐┌ 查找与复制 ──────┐
    │ 打开 保存 打印 ││ 撤销 重做 ││ 选择 抓手 ││ 搜索 去换行复制 翻译 │
    └───────────────┘└────────┘└────────┘└──────────────────┘   ← 组标题在按钮下方

* 选项卡下方的强调色细线随切换**滑动**（320ms，OutExpo）；切换后工具组**自下淡入**。
* 按钮由 ``QAction`` 驱动：启用 / 勾选 / 快捷键提示随 action 自动联动；图标名取
  action 的 ``iconName`` 属性，主题切换时统一重新着色。
* 下拉按钮（OCR 范围 / 阅读模式 / 高亮颜色）整颗按钮点击即弹出菜单。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import ThemeManager
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["Ribbon"]

_HEADER_HEIGHT = 44
_RIBBON_HEIGHT = 68


class Ribbon(QWidget):
    """标题栏 + 选项卡式分组功能区。

    Signals:
        themeToggleRequested(): 点击标题栏的浅 / 深色切换按钮
    """

    themeToggleRequested = Signal()

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = theme
        self._actions: list[QAction] = []
        self._buttons: dict[QAction, QToolButton] = {}
        self._group_titles: list[list[QLabel]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------- 标题栏 ----------------
        bar = QWidget()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(_HEADER_HEIGHT)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 0, 12, 0)
        bar_lay.setSpacing(20)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        self._logo = QLabel()
        self._logo.setFixedSize(24, 24)
        brand.addWidget(self._logo)
        self._brand = QLabel("Lumen")
        self._brand.setObjectName("BrandName")
        brand.addWidget(self._brand)
        self._doc_label = QLabel("")
        self._doc_label.setObjectName("BrandDoc")
        self._doc_label.setMaximumWidth(420)
        brand.addWidget(self._doc_label)
        bar_lay.addLayout(brand)

        # 选项卡（下方滑动指示线是 _tabs 的子控件，按几何动画移动）
        self._tabs = QWidget()
        self._tab_lay = QHBoxLayout(self._tabs)
        self._tab_lay.setContentsMargins(0, 0, 0, 0)
        self._tab_lay.setSpacing(4)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_group.idClicked.connect(self._on_tab_clicked)
        self._indicator = QFrame(self._tabs)
        self._indicator.setObjectName("TabIndicator")
        self._indicator.setFixedHeight(2)
        self._indicator.hide()
        bar_lay.addWidget(self._tabs)
        bar_lay.addStretch(1)

        self._theme_btn = QToolButton()
        self._theme_btn.setObjectName("HeaderIconButton")
        self._theme_btn.setToolTip("切换浅色 / 深色")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._theme_btn.setIconSize(QSize(16, 16))
        self._theme_btn.clicked.connect(self.themeToggleRequested.emit)
        self._trailing = QHBoxLayout()
        self._trailing.setSpacing(8)
        self._trailing.addWidget(self._theme_btn)
        bar_lay.addLayout(self._trailing)

        # ---------------- 功能区 ----------------
        self._stack = QStackedWidget()
        self._stack.setObjectName("RibbonStack")
        self._stack.setFixedHeight(_RIBBON_HEIGHT)

        root.addWidget(bar)
        root.addWidget(self._stack)

        self._refresh_brand()
        theme.changed.connect(self._refresh_icons)

    # ------------------------------------------------------------------ 构建
    def add_tab(self, title: str, groups: Sequence[tuple[str, Sequence[QAction]]]) -> list[QLabel]:
        """新增一个选项卡；``groups`` 为 ``(组标题, 动作列表)``，返回各组的标题标签。"""
        index = self._stack.count()

        tab_btn = QToolButton()
        tab_btn.setObjectName("HeaderTab")
        tab_btn.setText(title)
        tab_btn.setCheckable(True)
        tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tab_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tab_group.addButton(tab_btn, index)
        self._tab_lay.addWidget(tab_btn)

        page = QWidget()
        page.setObjectName("RibbonPage")
        lay = QHBoxLayout(page)
        lay.setContentsMargins(8, 4, 8, 2)
        lay.setSpacing(0)
        titles: list[QLabel] = []
        for i, (group_title, actions) in enumerate(groups):
            if i > 0:
                sep = QFrame()
                sep.setObjectName("RibbonSeparator")
                sep.setFixedWidth(1)
                sep_box = QVBoxLayout()
                sep_box.setContentsMargins(0, 8, 0, 20)
                sep_box.addWidget(sep)
                lay.addLayout(sep_box)
            column = QVBoxLayout()
            column.setContentsMargins(8, 0, 8, 0)
            column.setSpacing(0)
            row = QHBoxLayout()
            row.setSpacing(2)
            for act in actions:
                row.addWidget(self._make_button(act))
            column.addLayout(row)
            label = QLabel(group_title)
            label.setObjectName("RibbonGroupTitle")
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            column.addWidget(label)
            lay.addLayout(column)
            titles.append(label)
        lay.addStretch(1)
        self._stack.addWidget(page)
        self._group_titles.append(titles)
        if index == 0:
            tab_btn.setChecked(True)
        return titles

    def add_trailing_action(self, act: QAction) -> None:
        """标题栏右侧的文字按钮（如「关于」）。"""
        btn = QToolButton()
        btn.setObjectName("HeaderTextButton")
        btn.setDefaultAction(act)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._trailing.addWidget(btn)
        self._buttons[act] = btn

    def finish(self) -> None:
        """构建完成：选中第一页并放好指示线。"""
        if self._stack.count():
            self._stack.setCurrentIndex(0)
        self._move_indicator(animate=False)

    def button_for(self, act: QAction) -> QToolButton | None:
        """取某 action 对应的按钮（用于附加下拉菜单等）。"""
        return self._buttons.get(act)

    def set_menu(self, act: QAction, menu: QMenu) -> None:
        """给按钮挂下拉菜单：整颗按钮点击即弹出（设计稿的 dd 按钮），标签右下画 SVG 小箭头。"""
        btn = self._buttons.get(act)
        if btn is None:
            return
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setProperty("dropdown", "true")  # 样式表据此在标签右下画 SVG 小箭头

    def set_document_title(self, text: str) -> None:
        """标题栏品牌名后的文档名（含未保存标记），无文档时传空串。"""
        self._doc_label.setText(f" / {text}" if text else "")
        self._doc_label.setToolTip(text)

    def group_titles(self, tab_index: int) -> list[QLabel]:
        return self._group_titles[tab_index]

    # ------------------------------------------------------------------ 内部
    def _make_button(self, act: QAction) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("RibbonButton")
        btn.setDefaultAction(act)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setIconSize(QSize(18, 18))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoRaise(True)
        btn.setMinimumWidth(52)
        if act.property("danger"):
            btn.setProperty("danger", "true")
        name = str(act.property("iconName") or "doc")
        act.setIcon(self._theme.icon(name, checkable=act.isCheckable(), size=18))
        self._buttons[act] = btn
        self._actions.append(act)
        return btn

    def _on_tab_clicked(self, index: int) -> None:
        if index == self._stack.currentIndex():
            return
        self._stack.setCurrentIndex(index)
        page = self._stack.currentWidget()
        if page is not None:
            motion.fade_up(page, duration=TOKENS.anim_drop)
        self._move_indicator(animate=True)

    def _indicator_rect(self) -> QRect | None:
        btn = self._tab_group.checkedButton()
        if btn is None:
            return None
        geo = btn.geometry()
        y = self._tabs.height() - 2
        return QRect(geo.x() + 12, y, max(8, geo.width() - 24), 2)

    def _move_indicator(self, *, animate: bool) -> None:
        target = self._indicator_rect()
        if target is None:
            return
        self._indicator.show()
        self._indicator.raise_()
        if animate and self._indicator.isVisible():
            motion.animate_property(
                self._indicator,
                b"geometry",
                self._indicator.geometry(),
                target,
                TOKENS.anim_drop,
                owner=self._indicator,
            )
        else:
            self._indicator.setGeometry(target)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._move_indicator(animate=False)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._move_indicator(animate=False)

    def _refresh_brand(self) -> None:
        self._logo.setPixmap(self._theme.logo_pixmap(24))
        icon = "sun" if self._theme.is_dark else "moon"
        self._theme_btn.setIcon(self._theme.icon(icon, size=16))

    def _refresh_icons(self) -> None:
        for act in self._actions:
            name = str(act.property("iconName") or "doc")
            act.setIcon(self._theme.icon(name, checkable=act.isCheckable(), size=18))
        self._refresh_brand()
        motion.drop_in(self._theme_btn, duration=TOKENS.anim_drop)
