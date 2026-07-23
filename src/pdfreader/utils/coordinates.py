"""坐标转换工具。

PDF 页面坐标系与屏幕像素坐标系之间的换算。

关键概念：
- PDF 点：1 点 = 1/72 英寸，PDF 内容的原生坐标单位
- 屏幕像素：渲染后的位图像素坐标
- zoom=1.0 对应 72 DPI；zoom=2.0 对应 144 DPI

转换公式：
    像素坐标 = PDF 点坐标 × zoom
    PDF 点坐标 = 像素坐标 ÷ zoom
"""

from __future__ import annotations

from dataclasses import dataclass

from pdfreader.engines.types import Rect

__all__ = ["ZoomLevel", "pdf_rect_to_pixel", "pdf_to_pixel", "pixel_rect_to_pdf", "pixel_to_pdf"]

# PDF 标准 DPI
PDF_DPI = 72.0


def pdf_to_pixel(pdf_coord: float, zoom: float) -> float:
    """PDF 点坐标 → 像素坐标。"""
    return pdf_coord * zoom


def pixel_to_pdf(pixel_coord: float, zoom: float) -> float:
    """像素坐标 → PDF 点坐标。"""
    if zoom == 0:
        raise ValueError("zoom 不能为 0")
    return pixel_coord / zoom


def pdf_rect_to_pixel(rect: Rect, zoom: float) -> Rect:
    """PDF 矩形 → 像素矩形。"""
    return Rect(
        x0=rect.x0 * zoom,
        y0=rect.y0 * zoom,
        x1=rect.x1 * zoom,
        y1=rect.y1 * zoom,
    )


def pixel_rect_to_pdf(rect: Rect, zoom: float) -> Rect:
    """像素矩形 → PDF 矩形。"""
    if zoom == 0:
        raise ValueError("zoom 不能为 0")
    return Rect(
        x0=rect.x0 / zoom,
        y0=rect.y0 / zoom,
        x1=rect.x1 / zoom,
        y1=rect.y1 / zoom,
    )


@dataclass(frozen=True, slots=True)
class ZoomLevel:
    """缩放档位（用于预设缩放级别）。"""

    label: str
    zoom: float


# 常用缩放预设
ZOOM_PRESETS: tuple[ZoomLevel, ...] = (
    ZoomLevel("50%", 0.5),
    ZoomLevel("75%", 0.75),
    ZoomLevel("100%", 1.0),
    ZoomLevel("125%", 1.25),
    ZoomLevel("150%", 1.5),
    ZoomLevel("200%", 2.0),
    ZoomLevel("适应宽度", -1.0),  # 负值表示特殊模式，由视图层处理
    ZoomLevel("适应页面", -2.0),
)
