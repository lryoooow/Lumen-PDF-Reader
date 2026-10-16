"""顶部区域：标题栏（品牌 · 选项卡 · 主题 / 文献库）+ 功能区（Lumen 设计稿）。

结构（高 58 + 68）::

    [l.] lumen.   开始 批注 页面 视图 工具 AI 助读 ……………………… [☀] [▦]
    ┌ 文件 ─────────┐┌ 编辑 ─┐┌ 工具 ─┐┌ 查找与复制 ──────┐
    │ 打开 保存 打印 ││ 撤销 重做 ││ 选择 抓手 ││ 搜索 去换行复制 翻译 │
    └───────────────┘└────────┘└────────┘└──────────────────┘   ← 组标题在按钮下方

* 品牌与右侧网格按钮都回文献库（``homeRequested``）；网格按钮的提示是当前文档名
  （有未保存改动时前缀「未保存 ·」）。
* 选项卡下方的强调色细线**弹性**滑动：前沿 320ms 先到、后沿 500ms 延迟 80ms 跟上；
  切换后工具组按方向**横向滑入**（±18px），组标题自上落下。
* 按钮由 ``QAction`` 驱动：启用 / 勾选 / 快捷键提示随 action 自动联动；勾选时浅强调色
  底**自中心放大**铺满（420ms）。图标名取 action 的 ``iconName`` 属性，主题切换时重新着色。
* 下拉按钮（OCR 范围 / 阅读模式 / 高亮颜色）整颗按钮点击即弹出菜单。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import ThemeManager
from pdfreader.gui.theme.manager import serif_font
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["Ribbon", "RibbonButton"]

_HEADER_HEIGHT = 58
_RIBBON_HEIGHT = 68
_BUTTON_HEIGHT = 46
# 指示线弹性：前沿快、后沿慢且稍晚出发（设计稿 0.32s / 0.5s + 0.08s）
_LEAD_MS = 320
_TRAIL_MS = 500
_TRAIL_DELAY = 80


class RibbonButton(QToolButton):
    """功能区按钮：勾选时浅强调色底自中心放大淡入（取消时缩回淡出）。"""

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._fill = 0.0
        self.setObjectName("RibbonButton")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoRaise(True)
        self.setMinimumWidth(50)
        self.setFixedHeight(_BUTTON_HEIGHT)
        self.toggled.connect(self._on_toggled)

    @property
    def fill(self) -> float:
        """勾选底色的展开程度（0 = 无，1 = 铺满）。"""
        return self._fill

    def setDefaultAction(self, action: QAction) -> None:
        super().setDefaultAction(action)
        self._set_fill(1.0 if action.isChecked() else 0.0)

    def _on_toggled(self, checked: bool) -> None:
        motion.tween(self, self._fill, 1.0 if checked else 0.0, 420, self._set_fill)

    def _set_fill(self, value: float) -> None:
        self._fill = value
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._fill > 0.01:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            scale = 0.5 + 0.5 * self._fill
            w, h = self.width() * scale, self.height() * scale
            rect = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            color = QColor(self._theme.palette.accent_soft)
            color.setAlphaF(min(1.0, self._fill))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(rect, 8 * scale, 8 * scale)
            p.end()
        super().paintEvent(event)


class Ribbon(QWidget):
    """标题栏 + 选项卡式分组功能区。

    Signals:
        themeToggleRequested(): 点击标题栏的浅 / 深色切换按钮
        homeRequested(): 点击品牌或网格按钮（回文献库）
    """

    themeToggleRequested = Signal()
    homeRequested = Signal()

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = theme
        self._actions: list[QAction] = []
        self._buttons: dict[QAction, QToolButton] = {}
        self._group_titles: list[list[QLabel]] = []
        self._ind_left = 0.0
        self._ind_right = 0.0
        self._doc_name = ""
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(0)

        # ---------------- 标题栏 ----------------
        bar = QWidget()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(_HEADER_HEIGHT)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 0, 12, 0)
        bar_lay.setSpacing(20)

        self._brand = QToolButton()
        self._brand.setObjectName("ReaderBrand")
        self._brand.setText("lumen.")
        self._brand.setToolTip("返回文献库")
        self._brand.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._brand.setIconSize(QSize(38, 28))  # 28 的标 + 10 的留白
        self._brand.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brand.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._brand.clicked.connect(self.homeRequested.emit)
        bar_lay.addWidget(self._brand)
        bar_lay.addSpacing(20)  # 设计稿 .reader-brand { margin-right: 20px }

        # 选项卡（下方弹性指示线是 _tabs 的子控件，左右边分别插值）
        self._tabs = QWidget()
        self._tab_lay = QHBoxLayout(self._tabs)
        self._tab_lay.setContentsMargins(0, 0, 0, 0)
        self._tab_lay.setSpacing(0)
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
        self._theme_btn.setToolTip("切换主题")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._theme_btn.setIconSize(QSize(16, 16))
        self._theme_btn.clicked.connect(self.themeToggleRequested.emit)
        self._home_btn = QToolButton()
        self._home_btn.setObjectName("HeaderHomeButton")
        self._home_btn.setToolTip("文献库")
        self._home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._home_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._home_btn.setIconSize(QSize(16, 16))
        self._home_btn.clicked.connect(self.homeRequested.emit)
        self._trailing = QHBoxLayout()
        self._trailing.setSpacing(8)
        self._trailing.addWidget(self._theme_btn)
        self._trailing.addWidget(self._home_btn)
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
        tab_btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._tab_group.addButton(tab_btn, index)
        self._tab_lay.addWidget(tab_btn)

        page = QWidget()
        page.setObjectName("RibbonPage")
        lay = QHBoxLayout(page)
        lay.setContentsMargins(8, 0, 8, 0)
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
            column.setContentsMargins(8, 4, 8, 2)
            column.setSpacing(0)
            row = QHBoxLayout()
            row.setSpacing(2)
            for act in actions:
                row.addWidget(self._make_button(act))
            column.addLayout(row)
            column.addStretch(1)
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
        """标题栏右侧（网格按钮之前）的文字按钮。"""
        btn = QToolButton()
        btn.setObjectName("HeaderTextButton")
        btn.setDefaultAction(act)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._trailing.insertWidget(self._trailing.count() - 1, btn)
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

    def set_document_title(self, name: str, *, dirty: bool = False) -> None:
        """当前文档名（网格按钮的提示）；无文档时传空串。"""
        self._doc_name = name
        self._dirty = dirty and bool(name)
        self._home_btn.setToolTip(self.home_tooltip())

    def home_tooltip(self) -> str:
        if not self._doc_name:
            return "文献库"
        return f"{'未保存 · ' if self._dirty else ''}{self._doc_name}"

    def group_titles(self, tab_index: int) -> list[QLabel]:
        return self._group_titles[tab_index]

    def current_index(self) -> int:
        return self._stack.currentIndex()

    # ------------------------------------------------------------------ 内部
    def _make_button(self, act: QAction) -> QToolButton:
        btn = RibbonButton(self._theme)
        btn.setDefaultAction(act)
        if act.property("danger"):
            btn.setProperty("danger", "true")
        name = str(act.property("iconName") or "doc")
        act.setIcon(self._theme.icon(name, checkable=act.isCheckable(), size=18))
        self._buttons[act] = btn
        self._actions.append(act)
        return btn

    def _on_tab_clicked(self, index: int) -> None:
        current = self._stack.currentIndex()
        if index == current:
            return
        self._stack.setCurrentIndex(index)
        page = self._stack.currentWidget()
        if page is not None:
            motion.slide_in(page, 18 if index > current else -18, duration=360)
        for label in self._group_titles[index]:
            motion.drop_in(label, duration=TOKENS.anim_drop)
        self._move_indicator(animate=True, forward=index > current)

    def _indicator_target(self) -> tuple[float, float] | None:
        btn = self._tab_group.checkedButton()
        if btn is None:
            return None
        geo = btn.geometry()
        return float(geo.x() + 12), float(geo.x() + geo.width() - 12)

    def _place_indicator(self) -> None:
        left = round(self._ind_left)
        width = max(8, round(self._ind_right) - left)
        self._indicator.setGeometry(QRect(left, self._tabs.height() - 2, width, 2))

    def _set_left(self, value: float) -> None:
        self._ind_left = value
        self._place_indicator()

    def _set_right(self, value: float) -> None:
        self._ind_right = value
        self._place_indicator()

    def _move_indicator(self, *, animate: bool, forward: bool = True) -> None:
        target = self._indicator_target()
        if target is None:
            return
        left, right = target
        self._indicator.show()
        self._indicator.raise_()
        if not animate or not motion.enabled():
            motion.tween(self._indicator, left, left, 0, self._set_left, key="_ind_l")
            motion.tween(self._indicator, right, right, 0, self._set_right, key="_ind_r")
            return
        # 往右走：右边先到、左边后跟；往左反之（弹性拉伸感）
        lead, trail = (
            (
                ("_ind_r", self._ind_right, right, self._set_right),
                ("_ind_l", self._ind_left, left, self._set_left),
            )
            if forward
            else (
                ("_ind_l", self._ind_left, left, self._set_left),
                ("_ind_r", self._ind_right, right, self._set_right),
            )
        )
        motion.tween(self._indicator, lead[1], lead[2], _LEAD_MS, lead[3], key=lead[0])
        motion.tween(
            self._indicator,
            trail[1],
            trail[2],
            _TRAIL_MS,
            trail[3],
            delay=_TRAIL_DELAY,
            key=trail[0],
        )

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._move_indicator(animate=False)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._move_indicator(animate=False)

    def _refresh_brand(self) -> None:
        self._brand.setIcon(QIcon(_logo_pixmap(self._theme, 28)))
        icon = "sun" if self._theme.is_dark else "moon"
        self._theme_btn.setIcon(self._theme.icon(icon, size=16))
        self._home_btn.setIcon(self._theme.icon("grid", size=16))

    def _refresh_icons(self) -> None:
        for act in self._actions:
            name = str(act.property("iconName") or "doc")
            act.setIcon(self._theme.icon(name, checkable=act.isCheckable(), size=18))
        self._refresh_brand()
        motion.drop_in(self._theme_btn, duration=TOKENS.anim_drop)


def _logo_pixmap(theme: ThemeManager, size: int) -> QPixmap:
    """阅读器品牌标：强调色圆角方块里一个白色衬线「l.」（设计稿 .reader-logo）。"""
    ratio = 2.0
    pixmap = QPixmap(int((size + 10) * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.palette.accent))
    p.drawRoundedRect(QRectF(0, 0, size, size), 6, 6)
    p.setPen(QColor("#FFFFFF"))
    p.setFont(serif_font(round(size * 22 / 28)))
    p.drawText(QRectF(0, -1, size, size), int(Qt.AlignmentFlag.AlignCenter), "l.")
    p.end()
    return pixmap
