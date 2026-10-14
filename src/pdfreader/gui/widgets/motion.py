"""动效工具（对应设计稿 index.css 中的关键帧）。

* ``fade_up``  ≈ ``.anim-up``：0.5s，透明度 0→1，同时自下 8px 移入
* ``drop_in``  ≈ ``.anim-drop``：0.32s，透明度 0→1，同时自上 10px 落下
* 缓动统一为 OutExpo（≈ ``cubic-bezier(.22,1,.36,1)``）

动画只作用于显示层：结束后移除 ``QGraphicsOpacityEffect``（常驻的不透明度效果会让控件
每次重绘都走离屏缓冲，拖慢界面）。``set_enabled(False)`` 可整体关闭动效（测试、
或系统「减少动态效果」时使用），此时所有函数立即呈现最终状态。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QTimer,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from pdfreader.gui.theme.palette import TOKENS

__all__ = ["EASE", "animate_property", "drop_in", "enabled", "fade_up", "set_enabled"]

EASE = QEasingCurve.Type.OutExpo
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


def _appear(widget: QWidget, dy: int, duration: int, delay: int) -> None:
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
        if dy:
            end = widget.pos()
            slide = QPropertyAnimation(widget, b"pos", group)
            slide.setStartValue(end + QPoint(0, dy))
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
