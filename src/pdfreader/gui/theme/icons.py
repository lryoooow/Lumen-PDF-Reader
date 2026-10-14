"""图标系统（Lumen 设计稿图标集）。

内置一套线性 SVG 图标（路径取自设计稿），运行时按主题色渲染为 :class:`QIcon`，
支持"常态 / 勾选"双色（勾选态用强调色）。

设计：24×24 视框、1.7 描边、圆角端点。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

__all__ = ["ICON_NAMES", "app_icon", "render_icon", "render_pixmap"]

# ---------------------------------------------------------------------------
# 线性图标：name -> 内部 SVG 元素（描边色以 {c} 占位，渲染时替换）
# ---------------------------------------------------------------------------
_PATHS: dict[str, str] = {
    "open": '<path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5zM8 3v5h7M8 21v-7h8v7"/>',
    "print": '<path d="M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z"/>',
    "undo": '<path d="M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5M20 9H9a5 5 0 0 0 0 10h3"/>',
    "select": '<path d="M5 3l14 7-6 2-2 6z"/>',
    "hand": '<path d="M8 13V5a1.5 1.5 0 0 1 3 0v6m0-7a1.5 1.5 0 0 1 3 0v7m0-5a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-3l-2-4a1.5 1.5 0 0 1 2.5-1.5L8 15"/>',
    "search": '<path d="M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm5-2 5 5"/>',
    "copy": '<path d="M9 9h11v11H9zM5 15H4V4h11v1"/>',
    "highlight": '<path d="m9 11-6 6v3h9l3-3M22 12l-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
    "underline": '<path d="M6 4v6a6 6 0 0 0 12 0V4M4 21h16"/>',
    "ink": '<path d="M12 19l7-7 3 3-7 7zM18 13l-1.5-7.5L2 2l3.5 14.5L13 18zM2 2l7.6 7.6"/>',
    "note": '<path d="M4 4h16v12l-4 4H4zM16 20v-4h4"/>',
    "eraser": '<path d="m7 21-4-4L14 6l7 7-8 8zM10 10l7 7"/>',
    "signature": '<path d="M3 17c3-6 5-6 6-3s3 3 5-2 4 2 7 1M3 21h18"/>',
    "stamp": '<path d="M12 7v5l3 2M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z"/>',
    "delete_page": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14"/>',
    "rotate": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "merge": '<path d="M8 3v6a4 4 0 0 0 4 4h0a4 4 0 0 1 4 4v4M16 3v6M8 21v-4"/>',
    "watermark": '<path d="M12 3s6 6 6 11a6 6 0 0 1-12 0c0-5 6-11 6-11z"/>',
    "sidebar": '<path d="M3 4h18v16H3zM9 4v16"/>',
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "forward": '<path d="m9 18 6-6-6-6"/>',
    "prev": '<path d="M15 18l-6-6 6-6"/>',
    "next": '<path d="m9 18 6-6-6-6"/>',
    "zoom_in": '<path d="M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm5-2 5 5M8 11h6M11 8v6"/>',
    "zoom_out": '<path d="M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm5-2 5 5M8 11h6"/>',
    "fit_width": '<path d="M3 12h18M7 8l-4 4 4 4M17 8l4 4-4 4"/>',
    "fit_page": '<path d="M5 3h14v18H5zM9 8h6M9 12h6"/>',
    "actual_size": '<path d="M5 3h14v18H5zM10.5 9.5 12 8.5V16"/>',
    "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/>',
    "night": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/>',
    "ocr": '<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4M8 12h8"/>',
    "export": '<path d="M12 3v12M7 8l5-5 5 5M5 15v4h14v-4"/>',
    "notes": '<path d="M4 5h16v14H4zM7 15V9l3 3 3-3v6M17 9v6M15 13l2 2 2-2"/>',
    "info": '<path d="M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 11v6M12 7.5v.5"/>',
    "docinfo": '<path d="M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 11v6M12 7.5v.5"/>',
    "translate": '<path d="M4 5h8M8 3v2M6 5c0 4 3 7 6 8M10 5c0 4-3 7-6 8M13 21l4-10 4 10M14.5 17h5"/>',
    "sun": '<path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/>',
    "monitor": '<path d="M3 4.5h18v12H3zM9 20h6M12 16.5V20"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "close_doc": '<path d="M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM9.5 9.5l5 5M14.5 9.5l-5 5"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m6 15 6-6 6 6"/>',
    "doc": '<path d="M6 2h9l5 5v15H6zM14 2v6h6"/>',
    "file": '<path d="M6 2h9l5 5v15H6zM14 2v6h6"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "grid": '<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
    "pen": '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
}

ICON_NAMES = tuple(_PATHS.keys())

# 渲染缓存：(name, color, size, dpr, stroke) -> QPixmap
_cache: dict[tuple[str, str, int, float, float], QPixmap] = {}


def _device_pixel_ratio() -> float:
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _svg(name: str, color: str, stroke: float) -> bytes:
    inner = _PATHS[name].replace("{c}", color)
    doc = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{inner}</svg>'
    )
    return doc.encode("utf-8")


def render_pixmap(name: str, color: str, size: int = 22, *, stroke: float = 1.7) -> QPixmap:
    """把某个图标按指定颜色渲染为 :class:`QPixmap`（考虑高 DPI）。"""
    dpr = _device_pixel_ratio()
    key = (name, color, size, dpr, stroke)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    px = max(1, round(size * dpr))
    pixmap = QPixmap(px, px)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    _cache[key] = pixmap
    return pixmap


def render_icon(
    name: str,
    color: str,
    *,
    on_color: str | None = None,
    size: int = 22,
    stroke: float = 1.7,
) -> QIcon:
    """渲染图标为 :class:`QIcon`。

    若给出 ``on_color``，则为"勾选态（On）"另存一份该色像素——
    可勾选按钮被选中时 Qt 会自动切换到 On 态图标。
    """
    icon = QIcon()
    icon.addPixmap(
        render_pixmap(name, color, size, stroke=stroke), QIcon.Mode.Normal, QIcon.State.Off
    )
    if on_color is not None:
        on_pm = render_pixmap(name, on_color, size, stroke=stroke)
        icon.addPixmap(on_pm, QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(on_pm, QIcon.Mode.Active, QIcon.State.On)
    return icon


# ---------------------------------------------------------------------------
# 应用图标（窗口 / 任务栏 / 欢迎页 logo）
# ---------------------------------------------------------------------------
def _app_logo_svg(accent: str, accent_dark: str) -> bytes:
    """Lumen 标志：左上 → 右下渐变的圆角方块，中间一个白色「L」（与设计稿一致）。"""
    doc = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{accent}"/>
      <stop offset="1" stop-color="{accent_dark}"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="44" height="44" rx="11" fill="url(#g)"/>
  <path d="M19 14v20h12" fill="none" stroke="#ffffff" stroke-width="4.2"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""
    return doc.encode("utf-8")


def app_icon(accent: str = "#6366F1", accent_dark: str = "#7C3AED") -> QIcon:
    """应用 logo（用于窗口图标、欢迎页）。多尺寸渲染保证清晰。"""
    data = QByteArray(_app_logo_svg(accent, accent_dark))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        renderer = QSvgRenderer(data)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def app_logo_pixmap(size: int, accent: str, accent_dark: str) -> QPixmap:
    """欢迎页用的大 logo 像素图。"""
    dpr = _device_pixel_ratio()
    renderer = QSvgRenderer(QByteArray(_app_logo_svg(accent, accent_dark)))
    px = round(size * dpr)
    pixmap = QPixmap(px, px)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pixmap
