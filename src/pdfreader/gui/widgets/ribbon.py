"""顶部区域：Lumen Pure 统一顶栏 + 紧凑单行情境工具栏。

结构（高 40 + 40 = 80px，沉浸模式下折叠为 40px）::

    [l.] lumen.   开始  批注  页面  视图  工具  AI 助读 ………… [⌘K 命令] [沉浸] [☀] [▦]
    文件  [打开] [保存] [另存为] [打印] │ 编辑  [撤销] [重做] │ 工具  [选择] [抓手] …

* 品牌与右侧网格按钮都回文献库（``homeRequested``）；网格按钮提示显示当前文档名
  （有未保存改动时前缀「未保存 ·」）。
* 选项卡下方的森玉绿指示线**弹性**滑动：前沿先到、后沿延迟跟进（OutExpo）；
  切换后工具条按方向平滑滑入。
* 工具按钮采用单行胶囊式横排（图标在左、文字在右，高 30px），摒弃传统 Office 双层高按钮
  的臃肿感；勾选时浅森玉绿底色平滑铺开。
* 窗口变窄时，末尾动作自动收进同一行右侧的「更多」菜单；原 ``QAction`` 的状态、快捷键
  与子菜单完全共用。Tab 可进入工具按钮，Space 可执行；鼠标点击工具仍保留阅读器焦点。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import ThemeManager
from pdfreader.gui.theme.icons import app_logo_pixmap, render_pixmap
from pdfreader.gui.theme.manager import serif_font
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["Ribbon", "RibbonButton"]

_HEADER_HEIGHT = 40
_RIBBON_HEIGHT = 40
_BUTTON_HEIGHT = 30
_ICON_SIZE = 15
# 指示线弹性：前沿快、后沿慢且稍晚出发
_LEAD_MS = 240
_TRAIL_MS = 380
_TRAIL_DELAY = 55


class _KeyboardToolButton(QToolButton):
    """不抢鼠标阅读焦点，但为键盘导航绘制清晰的焦点轮廓。"""

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def _draw_focus_ring(self, painter: QPainter) -> None:
        if self.hasFocus():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(self._theme.palette.accent), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 5, 5)
            painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.hasFocus():
            p = QPainter(self)
            self._draw_focus_ring(p)
            p.end()


class _BrandButton(_KeyboardToolButton):
    """顶栏左上角品牌按钮：24×24 森玉绿徽标 + 衬线 lumen. 字样，像素级垂直居中。"""

    _LOGO_SIZE = 24
    _GAP = 8

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.setObjectName("ReaderBrand")
        self.setText("lumen.")
        self.setToolTip("返回文献库")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.sync_width()

    def _brand_font(self) -> QFont:
        font = serif_font(17)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def sync_width(self) -> None:
        fm = QFontMetricsF(self._brand_font())
        w = math.ceil(self._LOGO_SIZE + self._GAP + fm.horizontalAdvance(self.text()) + 10.0)
        self.setFixedWidth(max(100, w))

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 28)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        pal = self._theme.palette
        h = float(self.height())
        logo_y = (h - self._LOGO_SIZE) / 2.0
        logo = app_logo_pixmap(self._LOGO_SIZE, pal.brand_from, pal.brand_to)
        p.drawPixmap(
            QRectF(0.0, logo_y, self._LOGO_SIZE, self._LOGO_SIZE),
            logo,
            QRectF(logo.rect()),
        )
        font = self._brand_font()
        p.setFont(font)
        p.setPen(QColor(pal.accent_text if self.underMouse() else pal.text))
        fm = QFontMetricsF(font)
        tight = fm.tightBoundingRect(self.text())
        baseline_y = h / 2.0 - (tight.top() + tight.height() / 2.0)
        p.drawText(QPointF(float(self._LOGO_SIZE + self._GAP), baseline_y), self.text())
        self._draw_focus_ring(p)
        p.end()


class _HeaderTabButton(_KeyboardToolButton):
    """顶栏主模式页签按钮：自绘文字与悬停微底色，避免高分屏字重抖动或省略号截断。"""

    def __init__(self, title: str, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.setObjectName("HeaderTab")
        self.setText(title)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.sync_width()

    def _tab_font(self, *, checked: bool) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold if checked else QFont.Weight.Normal)
        return font

    def sync_width(self) -> None:
        fm = QFontMetricsF(self._tab_font(checked=True))
        w = math.ceil(fm.horizontalAdvance(self.text())) + 22
        self.setFixedWidth(max(46, w))

    def sizeHint(self) -> QSize:
        return QSize(self.width(), _HEADER_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        pal = self._theme.palette
        checked = self.isChecked()
        hovered = self.underMouse()
        if hovered and not checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.surface_hover))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(4, 6, -4, -6), 6.0, 6.0)
        p.setFont(self._tab_font(checked=checked))
        color = pal.accent_text if checked else (pal.text if hovered else pal.text_secondary)
        p.setPen(QColor(color))
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self.text())
        self._draw_focus_ring(p)
        p.end()


class RibbonButton(_KeyboardToolButton):
    """单行横排胶囊工具按钮：左图标 + 右文案垂直居中对齐，勾选态森玉绿柔光底平滑展开。"""

    _PAD_X = 8
    _ICON_TEXT_GAP = 5

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self._fill = 0.0
        self._hover = 0.0
        self.setObjectName("RibbonButton")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAutoRaise(True)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(46)
        self.setFixedHeight(_BUTTON_HEIGHT)
        self.toggled.connect(self._on_toggled)

    @property
    def fill(self) -> float:
        """勾选底色的展开程度（0 = 无，1 = 铺满）。"""
        return self._fill

    def _has_visual_icon(self) -> bool:
        act = self.defaultAction()
        if act is not None and (act.property("swatchColor") or act.property("iconName")):
            return True
        if self.objectName() == "RibbonOverflowButton":
            return True
        return not self.icon().isNull()

    def _calc_width(self) -> int:
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        text = self.text()
        text_w = QFontMetrics(font).horizontalAdvance(text) if text else 0
        icon_w = _ICON_SIZE if self._has_visual_icon() else 0
        gap = self._ICON_TEXT_GAP if (icon_w and text_w) else 0
        chev_w = 12 if self.property("dropdown") == "true" else 0
        return max(34, self._PAD_X * 2 + icon_w + gap + text_w + chev_w)

    def sync_width(self) -> None:
        """按当前文案与下拉箭头动态同步最小宽度，杜绝文字挤压截断。"""
        calc = self._calc_width()
        custom_min = getattr(self, "_custom_min_width", 0)
        super().setMinimumWidth(max(custom_min, calc))
        self.updateGeometry()

    def setMinimumWidth(self, minw: int) -> None:
        self._custom_min_width = minw
        super().setMinimumWidth(max(minw, self._calc_width()))

    def sizeHint(self) -> QSize:
        return QSize(max(self.minimumWidth(), self._calc_width()), _BUTTON_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def setText(self, text: str) -> None:
        super().setText(text)
        self.sync_width()

    def setDefaultAction(self, action: QAction) -> None:
        super().setDefaultAction(action)
        self._set_fill(1.0 if action.isChecked() else 0.0)
        self.sync_width()
        action.changed.connect(self.sync_width)

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().enterEvent(event)
        if self.isEnabled():
            motion.tween(
                self, self._hover, 1.0, TOKENS.anim_fast, self._set_hover, key="_btn_hover"
            )

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        motion.tween(self, self._hover, 0.0, TOKENS.anim_fast, self._set_hover, key="_btn_hover")

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        motion.tween(self, self._fill, 1.0 if checked else 0.0, 260, self._set_fill)

    def _set_fill(self, value: float) -> None:
        self._fill = value
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        pal = self._theme.palette
        w, h = float(self.width()), float(self.height())
        enabled = self.isEnabled()
        hovered = enabled and (self.underMouse() or self._hover > 0.02)
        pressed = enabled and self.isDown()
        danger = self.property("danger") == "true"

        if hovered and self._fill <= 0.02:
            bg = QColor(pal.danger_soft if danger else pal.surface_hover)
            alpha = 1.0 if self.underMouse() else min(1.0, self._hover)
            bg.setAlphaF(bg.alphaF() * alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(0.0, 0.0, w, h), 6.0, 6.0)

        if self._fill > 0.01:
            scale = 0.72 + 0.28 * self._fill
            bw, bh = w * scale, h * scale
            rect = QRectF((w - bw) / 2.0, (h - bh) / 2.0, bw, bh)
            color = QColor(pal.accent_soft)
            color.setAlphaF(min(1.0, self._fill))
            border_c = QColor(pal.accent)
            border_c.setAlphaF(0.28 * min(1.0, self._fill))
            p.setPen(QPen(border_c, 1.0))
            p.setBrush(color)
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0)

        if not enabled:
            icon_color = pal.text_disabled
            text_color = pal.text_disabled
            p.setOpacity(0.65)
        elif self.isChecked():
            icon_color = pal.icon_active
            text_color = pal.accent_text
        elif self.underMouse() and danger:
            icon_color = pal.danger
            text_color = pal.danger
        elif self.underMouse():
            icon_color = pal.text
            text_color = pal.text
        else:
            icon_color = pal.icon
            text_color = pal.text_secondary

        y_shift = 0.5 if pressed else 0.0
        act = self.defaultAction()
        swatch_color = str(act.property("swatchColor") or "") if act is not None else ""
        icon_name = str(act.property("iconName") or "") if act is not None else ""
        if not icon_name and self.objectName() == "RibbonOverflowButton":
            icon_name = "chevron_down"

        has_icon = bool(swatch_color or icon_name or not self.icon().isNull())
        text = self.text()
        is_dropdown = self.property("dropdown") == "true"

        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)
        p.setFont(font)
        fm = QFontMetricsF(font)
        text_w = fm.horizontalAdvance(text) if text else 0.0
        chev_sz = 10.0
        chev_gap = 2.0

        content_w = (
            (float(_ICON_SIZE) if has_icon else 0.0)
            + (float(self._ICON_TEXT_GAP) if (has_icon and text) else 0.0)
            + text_w
            + (chev_gap + chev_sz if is_dropdown else 0.0)
        )
        cur_x = (w - content_w) / 2.0

        if has_icon:
            icon_y = (h - _ICON_SIZE) / 2.0 + y_shift
            if swatch_color:
                p.setPen(QPen(QColor(pal.border_strong), 1.2))
                p.setBrush(QColor(swatch_color))
                p.drawEllipse(QRectF(cur_x + 1.5, icon_y + 1.5, _ICON_SIZE - 3.0, _ICON_SIZE - 3.0))
            elif icon_name:
                pm = render_pixmap(icon_name, icon_color, _ICON_SIZE)
                p.drawPixmap(QRectF(cur_x, icon_y, _ICON_SIZE, _ICON_SIZE), pm, QRectF(pm.rect()))
            else:
                pm = self.icon().pixmap(QSize(_ICON_SIZE, _ICON_SIZE))
                p.drawPixmap(QRectF(cur_x, icon_y, _ICON_SIZE, _ICON_SIZE), pm, QRectF(pm.rect()))
            cur_x += _ICON_SIZE + (self._ICON_TEXT_GAP if text else 0)

        if text:
            p.setPen(QColor(text_color))
            tight = fm.tightBoundingRect(text)
            baseline_y = h / 2.0 - (tight.top() + tight.height() / 2.0) + y_shift
            p.drawText(QPointF(cur_x, baseline_y), text)
            cur_x += text_w

        if is_dropdown:
            chev_pm = render_pixmap("chevron_down", text_color, int(chev_sz), stroke=2.2)
            chev_x = cur_x + chev_gap
            chev_y = (h - chev_sz) / 2.0 + 0.5 + y_shift
            p.drawPixmap(
                QRectF(chev_x, chev_y, chev_sz, chev_sz),
                chev_pm,
                QRectF(chev_pm.rect()),
            )

        p.setOpacity(1.0)
        self._draw_focus_ring(p)
        p.end()


@dataclass
class _RibbonGroup:
    widget: QWidget
    label: QLabel
    buttons: list[QToolButton]
    separator: QWidget


class _RibbonPage(QWidget):
    """单行水平情境工具条：组标题以内联微标签置左，动作胶囊按钮同排居中对齐，超宽自动收进「更多」。"""

    def __init__(
        self,
        groups: Sequence[tuple[str, Sequence[QAction]]],
        make_button: Callable[[QAction], QToolButton],
        theme: ThemeManager,
    ) -> None:
        super().__init__()
        self.setObjectName("RibbonPage")
        self._theme = theme
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._groups: list[_RibbonGroup] = []
        self._hidden: list[QAction] = []
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.timeout.connect(self._reflow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(0)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        for title, actions in groups:
            separator = QFrame()
            separator.setObjectName("RibbonSeparator")
            separator.setFixedSize(1, 16)
            separator_box = QWidget()
            separator_lay = QHBoxLayout(separator_box)
            separator_lay.setContentsMargins(5, 0, 5, 0)
            separator_lay.addWidget(separator, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(separator_box)

            group_box = QWidget()
            group_lay = QHBoxLayout(group_box)
            group_lay.setContentsMargins(2, 5, 2, 5)
            group_lay.setSpacing(2)

            label = QLabel(title)
            label.setObjectName("RibbonGroupTitle")
            label.setFixedHeight(_BUTTON_HEIGHT)
            label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            group_lay.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

            buttons = [make_button(action) for action in actions]
            for button in buttons:
                group_lay.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            for action in actions:
                action.changed.connect(self._schedule_reflow)

            lay.addWidget(group_box)
            self._groups.append(_RibbonGroup(group_box, label, buttons, separator_box))

        lay.addStretch(1)

        self._overflow_box = QWidget(self)
        overflow_lay = QHBoxLayout(self._overflow_box)
        overflow_lay.setContentsMargins(4, 5, 0, 5)
        overflow_lay.setSpacing(0)
        self.overflow = RibbonButton(theme, self._overflow_box)
        self.overflow.setObjectName("RibbonOverflowButton")
        self.overflow.setText("更多")
        self.overflow.setAccessibleName("更多功能区命令")
        self.overflow.setToolTip("显示当前宽度下收起的命令；可用键盘选择")
        self.overflow.setIcon(theme.icon("chevron_down", size=_ICON_SIZE))
        self.overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self.overflow)
        self._menu.aboutToShow.connect(self._populate_menu)
        self.overflow.setMenu(self._menu)
        overflow_lay.addWidget(self.overflow, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._overflow_box)

        self._ready = True
        theme.changed.connect(self._refresh_icon)
        self._schedule_reflow()

    @property
    def titles(self) -> list[QLabel]:
        return [group.label for group in self._groups]

    def minimumSizeHint(self) -> QSize:
        return QSize(80, _RIBBON_HEIGHT)

    def _schedule_reflow(self) -> None:
        if not self._reflow_timer.isActive():
            self._reflow_timer.start(0)

    def _refresh_icon(self) -> None:
        self.overflow.setIcon(self._theme.icon("chevron_down", size=_ICON_SIZE))

    def _required_width(self, visible: set[QToolButton]) -> int:
        widths: list[int] = []
        for group in self._groups:
            buttons = [button for button in group.buttons if button in visible]
            if buttons:
                row = sum(
                    max(button.minimumWidth(), button.sizeHint().width()) for button in buttons
                )
                row += 2 * (len(buttons) - 1)
                lbl_w = group.label.sizeHint().width()
                widths.append(6 + (lbl_w + 2 if lbl_w > 0 else 0) + row)
        return sum(widths) + max(0, len(widths) - 1) * 11

    def _reflow(self) -> None:
        candidates = [
            button
            for group in self._groups
            for button in group.buttons
            if button.defaultAction() is not None and button.defaultAction().isVisible()
        ]
        visible = set(candidates)
        available = max(0, self.width() - 20)
        focused = next((button for button in candidates if button.hasFocus()), None)
        overflow_focused = self.overflow.hasFocus()
        needs_overflow = self._required_width(visible) > available
        if needs_overflow:
            available -= max(self.overflow.minimumWidth(), self.overflow.sizeHint().width()) + 8
            for button in reversed(candidates):
                if self._required_width(visible) <= available:
                    break
                visible.remove(button)
        self._hidden = [
            button.defaultAction()
            for button in candidates
            if button not in visible and button.defaultAction() is not None
        ]
        first_group = True
        move_focus = focused is not None and focused not in visible
        for group in self._groups:
            shown = any(button in visible for button in group.buttons)
            group.widget.setVisible(shown)
            group.separator.setVisible(shown and not first_group)
            if shown:
                first_group = False
            for button in group.buttons:
                button.setVisible(button in visible)
        has_hidden = bool(self._hidden)
        self._overflow_box.setVisible(has_hidden)
        self.overflow.setVisible(has_hidden)
        if move_focus:
            self.overflow.setFocus(Qt.FocusReason.OtherFocusReason)
        elif overflow_focused and not self._hidden:
            target = next((button for button in candidates if button.isEnabled()), None)
            if target is not None:
                target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _populate_menu(self) -> None:
        self._menu.clear()
        hidden = set(self._hidden)
        for group in self._groups:
            actions = [
                button.defaultAction()
                for button in group.buttons
                if button.defaultAction() in hidden
            ]
            if actions:
                self._menu.addSection(group.label.text())
                for action in actions:
                    if action is not None:
                        self._menu.addAction(action)

    def event(self, event: QEvent) -> bool:
        result = bool(super().event(event))
        if event.type() == QEvent.Type.LayoutRequest and getattr(self, "_ready", False):
            self._schedule_reflow()
        return result

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._schedule_reflow()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._schedule_reflow()


class Ribbon(QWidget):
    """Lumen Pure 统一顶栏 + 紧凑单行工具栏（支持一键沉浸折叠）。

    Signals:
        themeToggleRequested(): 点击标题栏的浅 / 深色切换按钮
        homeRequested(): 点击品牌或网格按钮（回文献库）
        commandPaletteRequested(): 点击顶栏「命令搜索」胶囊
        focusModeToggled(bool): 切换「沉浸模式」（收起工具条与两侧抽屉）
    """

    themeToggleRequested = Signal()
    homeRequested = Signal()
    commandPaletteRequested = Signal()
    focusModeToggled = Signal(bool)

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
        self._toolbar_collapsed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------- 统一顶栏（40px） ----------------
        bar = QWidget()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(_HEADER_HEIGHT)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(14, 0, 12, 0)
        bar_lay.setSpacing(12)

        self._brand = _BrandButton(theme)
        self._brand.clicked.connect(self.homeRequested.emit)
        bar_lay.addWidget(self._brand, 0, Qt.AlignmentFlag.AlignVCenter)
        bar_lay.addSpacing(6)

        # 主模式页签（下方弹性森玉绿指示线）
        self._tabs = QWidget()
        self._tabs.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self._tab_lay = QHBoxLayout(self._tabs)
        self._tab_lay.setContentsMargins(0, 0, 0, 0)
        self._tab_lay.setSpacing(2)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_group.idClicked.connect(self._on_tab_clicked)
        self._indicator = QFrame(self._tabs)
        self._indicator.setObjectName("TabIndicator")
        self._indicator.setFixedHeight(2)
        self._indicator.hide()
        bar_lay.addWidget(self._tabs)
        bar_lay.addStretch(1)

        # 右侧快捷操作：命令搜索胶囊 · 沉浸模式 · 主题切换 · 文献库
        self._cmd_btn = _KeyboardToolButton(theme)
        self._cmd_btn.setObjectName("HeaderCmdPill")
        self._cmd_btn.setText("搜索命令  Ctrl+Shift+P")
        self._cmd_btn.setToolTip("打开命令面板 (Ctrl+Shift+P)")
        self._cmd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cmd_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._cmd_btn.setIconSize(QSize(13, 13))
        self._cmd_btn.clicked.connect(self.commandPaletteRequested.emit)

        self._focus_btn = _KeyboardToolButton(theme)
        self._focus_btn.setObjectName("HeaderTextButton")
        self._focus_btn.setText("沉浸")
        self._focus_btn.setCheckable(True)
        self._focus_btn.setToolTip("沉浸阅读：收起工具栏与两侧面板 (Alt+Z)")
        self._focus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._focus_btn.toggled.connect(self._on_focus_toggled)

        self._theme_btn = _KeyboardToolButton(theme)
        self._theme_btn.setObjectName("HeaderIconButton")
        self._theme_btn.setToolTip("切换浅色 / 深色主题")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.setIconSize(QSize(15, 15))
        self._theme_btn.clicked.connect(self.themeToggleRequested.emit)

        self._home_btn = _KeyboardToolButton(theme)
        self._home_btn.setObjectName("HeaderHomeButton")
        self._home_btn.setToolTip("文献库")
        self._home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._home_btn.setIconSize(QSize(15, 15))
        self._home_btn.clicked.connect(self.homeRequested.emit)

        self._trailing = QHBoxLayout()
        self._trailing.setSpacing(6)
        self._trailing.addWidget(self._cmd_btn)
        self._trailing.addWidget(self._focus_btn)
        self._trailing.addWidget(self._theme_btn)
        self._trailing.addWidget(self._home_btn)
        bar_lay.addLayout(self._trailing)

        # ---------------- 紧凑单行情境工具栏（40px） ----------------
        self._stack = QStackedWidget()
        self._stack.setObjectName("RibbonStack")
        self._stack.setFixedHeight(_RIBBON_HEIGHT)
        self._stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        root.addWidget(bar)
        root.addWidget(self._stack)

        self._refresh_brand()
        theme.changed.connect(self._refresh_icons)

    # ------------------------------------------------------------------ 构建
    def add_tab(self, title: str, groups: Sequence[tuple[str, Sequence[QAction]]]) -> list[QLabel]:
        """新增一个选项卡；``groups`` 为 ``(组标题, 动作列表)``，返回各组的标题标签。"""
        index = self._stack.count()

        tab_btn = _HeaderTabButton(title, self._theme)
        self._tab_group.addButton(tab_btn, index)
        self._tab_lay.addWidget(tab_btn)

        page = _RibbonPage(groups, self._make_button, self._theme)
        titles = page.titles
        self._stack.addWidget(page)
        self._group_titles.append(titles)
        if index == 0:
            tab_btn.setChecked(True)
        return titles

    def add_trailing_action(self, act: QAction) -> None:
        """标题栏右侧（主题按钮之前）的快捷切换按钮。"""
        btn = _KeyboardToolButton(self._theme)
        btn.setObjectName("HeaderTextButton")
        btn.setDefaultAction(act)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        idx = max(0, self._trailing.indexOf(self._theme_btn))
        self._trailing.insertWidget(idx, btn)
        self._buttons[act] = btn

    def finish(self) -> None:
        """构建完成：选中第一页并定位弹性指示线。"""
        if self._stack.count():
            self._stack.setCurrentIndex(0)
        self._move_indicator(animate=False)

    def set_toolbar_collapsed(self, collapsed: bool) -> None:
        """展开或收起第二排工具栏（带阻尼高度动画）。"""
        if self._toolbar_collapsed == collapsed:
            return
        self._toolbar_collapsed = collapsed
        start = self._stack.height() if self._stack.isVisible() else 0
        end = 0 if collapsed else _RIBBON_HEIGHT
        if not collapsed:
            self._stack.setVisible(True)
        if not motion.enabled() or start == end:
            self._stack.setFixedHeight(end)
            self._stack.setVisible(not collapsed)
            return

        def apply_h(v: float) -> None:
            h = max(0, round(v))
            self._stack.setFixedHeight(h)
            if collapsed and h == 0:
                self._stack.setVisible(False)

        motion.tween(
            self._stack,
            float(start),
            float(end),
            TOKENS.anim_drop,
            apply_h,
            key="_toolbar_collapse",
        )

    @property
    def focus_mode(self) -> bool:
        return bool(self._focus_btn.isChecked())

    def set_focus_mode(self, enabled: bool) -> None:
        if self._focus_btn.isChecked() != enabled:
            self._focus_btn.setChecked(enabled)
        else:
            self.set_toolbar_collapsed(enabled)

    def _on_focus_toggled(self, checked: bool) -> None:
        self.set_toolbar_collapsed(checked)
        self.focusModeToggled.emit(checked)

    def button_for(self, act: QAction) -> QToolButton | None:
        """取某 action 对应的按钮（用于附加下拉菜单等）。"""
        return self._buttons.get(act)

    def set_menu(self, act: QAction, menu: QMenu) -> None:
        """给按钮挂下拉菜单：整颗按钮点击即弹出，右侧绘制小箭头。"""
        btn = self._buttons.get(act)
        if btn is None:
            return
        act.setMenu(None)
        btn.setMenu(menu)
        act.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setProperty("dropdown", "true")
        if isinstance(btn, RibbonButton):
            btn.sync_width()

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
        self.addAction(act)
        btn = RibbonButton(self._theme)
        btn.setDefaultAction(act)
        if act.property("danger"):
            btn.setProperty("danger", "true")
        name = str(act.property("iconName") or "doc")
        act.setIcon(self._theme.icon(name, checkable=act.isCheckable(), size=_ICON_SIZE))
        self._buttons[act] = btn
        self._actions.append(act)
        return btn

    def _on_tab_clicked(self, index: int) -> None:
        if self._toolbar_collapsed and not self._focus_btn.isChecked():
            self.set_toolbar_collapsed(False)
        elif self._focus_btn.isChecked():
            # 沉浸模式下点击任意主选项卡自动展开该组工具
            self._focus_btn.setChecked(False)
        current = self._stack.currentIndex()
        if index == current:
            return
        self._stack.setCurrentIndex(index)
        page = self._stack.currentWidget()
        if page is not None:
            motion.slide_in(page, 14 if index > current else -14, duration=TOKENS.anim_up)
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
        self._cmd_btn.setVisible(self.width() >= 960)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._move_indicator(animate=False)
        self._cmd_btn.setVisible(self.width() >= 960)

    def _refresh_brand(self) -> None:
        self._brand.sync_width()
        self._brand.setIcon(QIcon(_logo_pixmap(self._theme, 24)))
        self._brand.update()
        for tab_btn in self._tab_group.buttons():
            if isinstance(tab_btn, _HeaderTabButton):
                tab_btn.sync_width()
        icon = "sun" if self._theme.is_dark else "moon"
        self._theme_btn.setIcon(self._theme.icon(icon, size=15))
        self._home_btn.setIcon(self._theme.icon("grid", size=15))
        self._cmd_btn.setIcon(self._theme.icon("search", size=13))

    def _refresh_icons(self) -> None:
        for act in self._actions:
            name = str(act.property("iconName") or "doc")
            act.setIcon(self._theme.icon(name, checkable=act.isCheckable(), size=_ICON_SIZE))
        for btn in self._buttons.values():
            btn.update()
        self._refresh_brand()
        motion.drop_in(self._theme_btn, duration=TOKENS.anim_drop)


def _logo_pixmap(theme: ThemeManager, size: int) -> QPixmap:
    """阅读器品牌标：森玉绿圆角方块内衬线白色「l.」。"""
    p = theme.palette
    return app_logo_pixmap(size, p.brand_from, p.brand_to)
