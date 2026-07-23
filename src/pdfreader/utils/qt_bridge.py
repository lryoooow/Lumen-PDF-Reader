"""Qt 与引擎之间的适配工具。

把引擎层的纯数据结构（:class:`PixmapData`）转换为 Qt 的 :class:`QPixmap`。
这是 GUI 层唯一直接处理引擎原始字节的地方。

注意三个关键点（来自 PyMuPDF pixmap 桥接的已知坑）：
1. ``stride`` 必须作为 ``bytesPerLine`` 传给 QImage，不能用 width*n
2. 构造 QPixmap 前必须 ``.copy()``，否则 QImage 只引用字节不复制，会被回收
3. n=3 用 RGB888，n=4 用 RGBA8888
"""

from __future__ import annotations

from pdfreader.engines.types import PixmapData

__all__ = ["pixmap_to_qimage", "pixmap_to_qpixmap"]


def pixmap_to_qimage(data: PixmapData):  # type: ignore[no-untyped-def]
    """将 :class:`PixmapData` 转换为 :class:`QImage`。

    返回的 QImage **共享**像素字节（不复制）。若需长期持有，应转 QPixmap 或自行 copy。
    """
    from PySide6.QtGui import QImage

    if data.n == 3:
        fmt = QImage.Format_RGB888  # type: ignore[attr-defined]
    elif data.n == 4:
        fmt = QImage.Format_RGBA8888  # type: ignore[attr-defined]
    else:
        raise ValueError(f"不支持的像素格式：每像素 {data.n} 字节（仅支持 3=RGB / 4=RGBA）")

    return QImage(data.samples, data.width, data.height, data.stride, fmt)


def pixmap_to_qpixmap(data: PixmapData):  # type: ignore[no-untyped-def]
    """将 :class:`PixmapData` 转换为 :class:`QPixmap`（含 copy，安全持有）。"""
    from PySide6.QtGui import QPixmap

    img = pixmap_to_qimage(data)
    return QPixmap.fromImage(img.copy())
