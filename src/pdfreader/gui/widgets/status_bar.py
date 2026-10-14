"""底部状态栏（Lumen 设计稿，高 40）。

左：‹ [页码] / 总页 › + 阅读进度条（随页码平滑增长）
中：状态消息（绿点 + 文字，淡入出现，2.8 秒后自动消失）
右：阅读模式标签（夜间 / 护眼）· 缩放（− 滑杆 + 百分比 适宽）

主题切换已移到标题栏（设计稿），这里不再提供主题菜单。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QSlider,
    QToolButton,
    QWidget,
)

from pdfreader.gui.theme import ThemeManager
from pdfreader.gui.theme.palette import TOKENS
from pdfreader.gui.widgets import motion

__all__ = ["StatusBar"]

# 缩放滑杆范围（百分比，设计稿 25–300）；按钮 / 快捷键仍可到 10%–800%
SLIDER_MIN = 25
SLIDER_MAX = 300
# 消息自动消失时间（毫秒，设计稿 2800）
MESSAGE_TIMEOUT = 2800
_PROGRESS_SCALE = 1000


class StatusBar(QWidget):
    """自绘底部状态栏。

    Signals:
        prevRequested / nextRequested: 上/下一页
        pageEntered(int): 用户输入页码并回车（1 基）
        zoomInRequested / zoomOutRequested / fitRequested: 缩放按钮
        zoomPercentRequested(int): 拖动缩放滑杆（百分比）
    """

    prevRequested = Signal()
    nextRequested = Signal()
    pageEntered = Signal(int)
    zoomInRequested = Signal()
    zoomOutRequested = Signal()
    fitRequested = Signal()
    zoomPercentRequested = Signal(int)

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = theme
        self._total = 0
        self.setFixedHeight(40)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(MESSAGE_TIMEOUT)
        self._hide_timer.timeout.connect(self._hide_message)
        self._slider_timer = QTimer(self)
        self._slider_timer.setSingleShot(True)
        self._slider_timer.setInterval(120)
        self._slider_timer.timeout.connect(self._emit_slider_zoom)
        self._pending_percent = 100
        self._build()
        theme.changed.connect(self._refresh_icons)
        self.set_document_active(False)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(4)

        # --- 翻页 + 阅读进度 ---
        self._nav = QWidget()
        nav = QHBoxLayout(self._nav)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(2)
        self._prev_btn = self._flat_button("prev", "上一页", self.prevRequested.emit)
        self._page_edit = QLineEdit()
        self._page_edit.setObjectName("PageEdit")
        self._page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_edit.setValidator(QIntValidator(1, 999999, self))
        self._page_edit.returnPressed.connect(self._on_page_entered)
        self._total_lbl = QLabel("/ 0")
        self._next_btn = self._flat_button("next", "下一页", self.nextRequested.emit)
        self._progress = QProgressBar()
        self._progress.setObjectName("ReadingProgress")
        self._progress.setRange(0, _PROGRESS_SCALE)
        self._progress.setTextVisible(False)
        self._progress.setFixedWidth(96)
        for w in (self._prev_btn, self._page_edit, self._total_lbl, self._next_btn):
            nav.addWidget(w)
        nav.addSpacing(8)
        nav.addWidget(self._progress)
        lay.addWidget(self._nav)

        # --- 消息（绿点 + 文字） ---
        lay.addSpacing(10)
        self._message_box = QWidget()
        msg = QHBoxLayout(self._message_box)
        msg.setContentsMargins(0, 0, 0, 0)
        msg.setSpacing(6)
        self._dot = QLabel()
        self._dot.setObjectName("StatusDot")
        self._dot.setFixedSize(6, 6)
        self._message = QLabel("")
        self._message.setObjectName("StatusMessage")
        msg.addWidget(self._dot)
        msg.addWidget(self._message)
        self._message_box.setVisible(False)
        lay.addWidget(self._message_box)
        lay.addStretch(1)

        # --- 阅读模式标签 ---
        self._mode_chip = QLabel("")
        self._mode_chip.setObjectName("ModeChip")
        self._mode_chip.setVisible(False)
        self._mode_chip.setFixedHeight(18)
        lay.addWidget(self._mode_chip, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(8)

        # --- 缩放 ---
        self._zoom_box = QWidget()
        zoom = QHBoxLayout(self._zoom_box)
        zoom.setContentsMargins(0, 0, 0, 0)
        zoom.setSpacing(4)
        self._zoom_out_btn = self._text_button("−", "缩小", self.zoomOutRequested.emit)
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setObjectName("ZoomSlider")
        self._zoom_slider.setRange(SLIDER_MIN, SLIDER_MAX)
        self._zoom_slider.setFixedWidth(96)
        self._zoom_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._zoom_slider.valueChanged.connect(self._on_slider)
        self._zoom_in_btn = self._text_button("+", "放大", self.zoomInRequested.emit)
        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setObjectName("ZoomLabel")
        self._zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fit_btn = self._text_button("适宽", "适应宽度", self.fitRequested.emit)
        for w in (
            self._zoom_out_btn,
            self._zoom_slider,
            self._zoom_in_btn,
            self._zoom_lbl,
            self._fit_btn,
        ):
            zoom.addWidget(w)
        lay.addWidget(self._zoom_box)

    def _flat_button(self, icon_name: str, tip: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("FlatButton")
        btn.setIcon(self._theme.icon(icon_name, size=14))
        btn.setIconSize(QSize(14, 14))
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(slot)
        btn.setProperty("iconName", icon_name)
        return btn

    def _text_button(self, text: str, tip: str, slot) -> QToolButton:  # type: ignore[no-untyped-def]
        btn = QToolButton()
        btn.setObjectName("StatusTextButton")
        btn.setText(text)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _on_page_entered(self) -> None:
        text = self._page_edit.text().strip()
        if text.isdigit():
            self.pageEntered.emit(int(text))
            self._page_edit.clearFocus()

    def _on_slider(self, value: int) -> None:
        # set_zoom 同步滑杆时屏蔽了信号，能走到这里的都是用户操作；拖动中合并成一次缩放。
        # 记下用户选的值：防抖期间若有适宽重排把滑杆同步回去，发出的仍是用户的选择。
        self._pending_percent = value
        self._slider_timer.start()

    def _emit_slider_zoom(self) -> None:
        self.zoomPercentRequested.emit(self._pending_percent)

    # ------------------------------------------------------------------ 公开 API
    def show_message(self, text: str) -> None:
        """显示一条状态消息：淡入出现，2.8 秒后自动消失（设计稿 toast）。"""
        self._message.setText(text)
        self._message.setToolTip(text)
        self._message_box.setVisible(bool(text))
        if text:
            motion.fade_up(self._message_box, duration=TOKENS.anim_drop)
            self._hide_timer.start()

    def message(self) -> str:
        """当前显示的消息（已消失时为空串）。"""
        return self._message.text() if self._message_box.isVisible() else ""

    def _hide_message(self) -> None:
        self._message_box.setVisible(False)

    def set_page(self, current_1based: int, total: int) -> None:
        self._total = total
        self._total_lbl.setText(f"/ {total}")
        if not self._page_edit.hasFocus():  # 用户正在输入时不打断
            self._page_edit.setText(str(current_1based))
        target = int(_PROGRESS_SCALE * current_1based / total) if total > 0 else 0
        motion.animate_property(
            self._progress,
            b"value",
            self._progress.value(),
            target,
            TOKENS.anim_up,
            owner=self._progress,
        )

    def set_zoom(self, percent: int) -> None:
        self._zoom_lbl.setText(f"{percent}%")
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(min(max(percent, SLIDER_MIN), SLIDER_MAX))
        self._zoom_slider.blockSignals(False)

    def set_reading_mode(self, label: str) -> None:
        """阅读模式标签：传 "夜间" / "护眼"；正常模式传空串隐藏。"""
        self._mode_chip.setText(label)
        visible = bool(label)
        if visible and not self._mode_chip.isVisible():
            self._mode_chip.setVisible(True)
            motion.drop_in(self._mode_chip, duration=TOKENS.anim_drop)
        elif not visible:
            self._mode_chip.setVisible(False)

    def set_document_active(self, active: bool) -> None:
        """有无文档时统一启用/禁用翻页与缩放控件（设计稿：无文档时淡显不可点）。"""
        for w in (self._nav, self._zoom_box):
            w.setEnabled(active)
        if not active:
            self._page_edit.clear()
            self._total_lbl.setText("/ 0")
            self._zoom_lbl.setText("100%")
            self._progress.setValue(0)
            self.set_reading_mode("")

    def _refresh_icons(self) -> None:
        for btn in (self._prev_btn, self._next_btn):
            name = str(btn.property("iconName"))
            btn.setIcon(self._theme.icon(name, size=14))
