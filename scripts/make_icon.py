"""生成应用图标 app.ico。

用 Qt 把内置的 logo SVG 渲染成 256px PNG，再用 Pillow 打包为多尺寸 .ico，
供 PyInstaller 与 Windows 安装程序 / 快捷方式使用。

用法:
    uv run python scripts/make_icon.py
产物: src/pdfreader/resources/icons/app.ico
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QByteArray  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pdfreader.gui.theme.icons import _app_logo_svg  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "src" / "pdfreader" / "resources" / "icons" / "app.ico"


def main() -> int:
    QApplication(sys.argv[:1])
    renderer = QSvgRenderer(QByteArray(_app_logo_svg("#6366F1", "#7C3AED")))
    img = QImage(256, 256, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()

    buf = QByteArray()
    from PySide6.QtCore import QBuffer

    qbuf = QBuffer(buf)
    qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(qbuf, "PNG")
    qbuf.close()

    pil = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pil.save(
        OUT,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成图标: {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
