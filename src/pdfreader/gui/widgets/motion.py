"""动效工具（对应设计稿 index.css 中的关键帧）。

* ``fade_up``  ≈ ``.a-fade-up``：0.5s，透明度 0→1，同时自下 8px 移入
* ``drop_in``  ≈ ``.a-drop``：0.32s，透明度 0→1，同时自上 10px 落下
* ``pop_in`` / ``pop_out`` ≈ ``.a-pop`` / ``.a-out``：弹出层 0.32s 浮现、0.18s 收起后隐藏
* ``slide_in`` ≈ ``.a-slide``：0.36s，自左 / 右 ``dx`` 像素滑入（功能区换页随方向）
* ``tween``：任意数值插值回调（自绘控件的填充、指示线、滚动数字）
* 缓动统一为 OutExpo（≈ ``cubic-bezier(.16,1,.3,1)``）；收起用 InCubic

动画只作用于显示层：结束后移除 ``QGraphicsOpacityEffect``（常驻的不透明度效果会让控件
每次重绘都走离屏缓冲，拖慢界面）。``set_enabled(False)`` 可整体关闭动效（测试、
或系统「减少动态效果」时使用），此时所有函数立即呈现最终状态。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from pdfreader.gui.theme.palette import TOKENS

__all__ = [
    "EASE",
    "EASE_IN",
    "animate_property",
    "drop_in",
    "enabled",
    "fade_up",
    "pop_in",
    "pop_out",
    "set_enabled",
    "slide_in",
    "tween",
]

EASE = QEasingCurve.Type.OutExpo
EASE_IN = QEasingCurve.Type.InCubic
_enabled = True


def set_enabled(value: bool) -> None:
    """全局开关动效。"""
    global _enabled
    _enabled = value


def enabled() -> bool:
    return _enabled


def _keep(widget: QWidget, anim: QAbstractAnimation) -> None:
    """把动画挂在控件上，避免被垃圾回收；同一控件的上一段同类动画先停掉。"""
    previous = getattr(widget, "_motion_anim", None)
    if isinstance(previous, QAbstractAnimation):
        with contextlib.suppress(RuntimeError):  # 已随 DeleteWhenStopped 释放
            previous.stop()
    widget._motion_anim = anim


def _appear(widget: QWidget, dy: int, duration: int, delay: int, dx: int = 0) -> None:
    """淡入 +（可选）纵向位移。

    先立即把不透明度设为 0，再在下一轮事件循环（或 ``delay`` 毫秒后）读取控件**此刻**的
    位置作为终点开始动画——控件刚显示时布局可能还没算好，提前取的位置是错的。
    """
    if not _enabled or not widget.isVisible():
        widget.setGraphicsEffect(None)
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    def start() -> None:
        try:
            if widget.graphicsEffect() is not effect:
                return  # 期间已被新动画或清理替换
        except RuntimeError:  # 控件已销毁
            return
        group = QParallelAnimationGroup(widget)
        fade = QPropertyAnimation(effect, b"opacity", group)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(duration)
        fade.setEasingCurve(EASE)
        group.addAnimation(fade)
        if dy or dx:
            end = widget.pos()
            slide = QPropertyAnimation(widget, b"pos", group)
            slide.setStartValue(end + QPoint(dx, dy))
            slide.setEndValue(end)
            slide.setDuration(duration)
            slide.setEasingCurve(EASE)
            group.addAnimation(slide)

        def finish() -> None:
            with contextlib.suppress(RuntimeError):
                if widget.graphicsEffect() is effect:
                    widget.setGraphicsEffect(None)

        group.finished.connect(finish)
        _keep(widget, group)
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    QTimer.singleShot(max(0, delay), widget, start)


def fade_up(widget: QWidget, *, delay: int = 0, duration: int = TOKENS.anim_up) -> None:
    """自下 8px 淡入（选项卡内容、欢迎页、列表项、批注卡片）。"""
    _appear(widget, 8, duration, delay)


def drop_in(widget: QWidget, *, duration: int = TOKENS.anim_drop) -> None:
    """自上 10px 落下淡入（浮层、弹出卡片、工具提示）。"""
    _appear(widget, -10, duration, 0)


def pop_in(widget: QWidget, *, duration: int = TOKENS.anim_drop) -> None:
    """弹出层浮现（色板、阅读模式菜单、搜索卡片）：自上 4px 淡入。"""
    _appear(widget, -4, duration, 0)


def slide_in(widget: QWidget, dx: int, *, duration: int = 360) -> None:
    """自 ``dx`` 像素处横向滑入淡入（正数自右、负数自左）。"""
    _appear(widget, 0, duration, 0, dx)


def pop_out(
    widget: QWidget, *, duration: int = 180, on_done: Callable[[], None] | None = None
) -> None:
    """收起：淡出后隐藏（≈ ``.a-out``，InCubic 0.18s）；动效关闭时立即隐藏。"""

    def done() -> None:
        with contextlib.suppress(RuntimeError):
            widget.hide()
            widget.setGraphicsEffect(None)
        if on_done is not None:
            on_done()

    if not _enabled or not widget.isVisible():
        done()
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)
    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setStartValue(1.0)
    fade.setEndValue(0.0)
    fade.setDuration(duration)
    fade.setEasingCurve(EASE_IN)
    fade.finished.connect(done)
    _keep(widget, fade)
    fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def tween(
    owner: QObject,
    start: float,
    end: float,
    duration: int,
    on_value: Callable[[float], None],
    *,
    delay: int = 0,
    easing: QEasingCurve.Type = EASE,
    key: str = "_motion_tween",
) -> QVariantAnimation | None:
    """数值插值：每帧回调 ``on_value``；同一 ``owner`` + ``key`` 的上一段先停掉。

    动效关闭时直接回调终值并返回 None。
    """
    previous = getattr(owner, key, None)
    if isinstance(previous, QAbstractAnimation):
        with contextlib.suppress(RuntimeError):
            previous.stop()
            previous.deleteLater()  # 还在延迟里没开始的那段不会自动删除
    if not _enabled or duration <= 0:
        on_value(end)
        setattr(owner, key, None)
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(lambda v: on_value(float(v)))
    setattr(owner, key, anim)
    if delay > 0:
        on_value(start)
        QTimer.singleShot(
            delay,
            owner,
            lambda: _start_if_current(owner, key, anim),
        )
    else:
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def _start_if_current(owner: QObject, key: str, anim: QVariantAnimation) -> None:
    with contextlib.suppress(RuntimeError):
        if getattr(owner, key, None) is anim:
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def animate_property(
    target: QObject,
    prop: bytes,
    start: object,
    end: object,
    duration: int,
    *,
    owner: QWidget | None = None,
) -> QPropertyAnimation | None:
    """通用属性动画；动效关闭时直接设为终值并返回 None。"""
    if not _enabled:
        target.setProperty(prop.decode(), end)
        return None
    anim = QPropertyAnimation(target, prop, owner or target)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(duration)
    anim.setEasingCurve(EASE)
    if owner is not None:
        _keep(owner, anim)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim
