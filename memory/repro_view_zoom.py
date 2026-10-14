"""验证：PdfView 缩放后布局/缓存一致性（原 P1-4 与 P2 潜伏缺陷的回归脚本）。

修复前实测（本脚本原版本）：
- A 缩放后 _page_items 仍是旧 zoom 的位图项（top=0/1212/2424、页高 1200，期望 0/612/1224、600）；
- B _update_page_item 兜底缩放会把 offset 一起缩放（第 11 页 top 12120 → 12093）；
- C _words_cache 只在 setup_document 清空，插入文字后选择/复制仍返回旧文本。

运行：uv run python memory/repro_view_zoom.py   （全部 PASS 时退出码 0）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsRectItem  # noqa: E402

from pdfreader.engines.types import PixmapData, Rect, WordBox  # noqa: E402
from pdfreader.gui.widgets import PdfView  # noqa: E402

app = QApplication.instance() or QApplication([])
failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


print("=== A：缩放后所有页回到新布局的骨架占位 ===")
view = PdfView()
view.resize(800, 600)
view.show()
view.setup_document(3, [(600, 800)] * 3)
z = view.zoom
print(f"初始 zoom={z} page_y={view._page_y_positions}")
for p in range(3):
    w, h = int(600 * z), int(800 * z)
    view.on_page_rendered(
        p, z, PixmapData(samples=bytes(w * h * 3), width=w, height=h, n=3, stride=w * 3)
    )
check(
    all(type(view._page_items[p]).__name__ == "QGraphicsPixmapItem" for p in range(3)),
    "渲染后三页均为位图项",
)

view.set_zoom(0.75)  # 相当于 Ctrl+滚轮 / 适宽 / 窗口缩放
expected_h = 800 * view.zoom
print(f"切到 zoom={view.zoom} 新 page_y={view._page_y_positions} 期望页高={expected_h}")
for p in range(3):
    item = view._page_items[p]
    rect = item.sceneBoundingRect()
    check(isinstance(item, QGraphicsRectItem), f"第{p}页已换回骨架占位（{type(item).__name__}）")
    check(
        abs(rect.top() - view._page_y_positions[p]) <= 0.5
        and abs(rect.height() - expected_h) <= 1.0,
        f"第{p}页 sceneTop={rect.top():.1f} sceneH={rect.height():.1f}"
        f"（期望 top={view._page_y_positions[p]} h={expected_h}）",
    )
check(
    not view.is_page_rendered(0) and view._rendered_cache.size == 0,
    "旧渲染缓存已清空，页面待重渲染",
)

print()
print("=== B：_update_page_item 尺寸不一致时不影响页面顶部位置 ===")
view2 = PdfView()
view2.resize(800, 600)
view2.setup_document(12, [(600, 800)] * 12)
z2 = view2.zoom
view2._update_page_item(10, QPixmap(int(600 * z2) + 2, int(800 * z2) + 2))  # 与占位差 >1px
item = view2._page_items[10]
top = item.sceneBoundingRect().top()
check(
    abs(top - view2._page_y_positions[10]) <= 0.5,
    f"第 11 页 期望 sceneTop={view2._page_y_positions[10]} 实际 sceneTop={top:.1f}"
    f"（scale={item.scale():.6f}）",
)

print()
print("=== C：invalidate_page 后词表与渲染缓存一起失效 ===")
state = {"text": "旧文本"}
view3 = PdfView()
view3.resize(400, 400)
view3.setup_document(1, [(600, 800)])
view3.set_text_provider(
    lambda _p: (WordBox(rect=Rect(0, 0, 10, 10), text=state["text"], block=0, line=0, word_no=0),)
)
check(view3._get_words(0)[0].text == "旧文本", "首次取词得到旧文本")
state["text"] = "新插入的文本"  # 相当于 main_window._on_placement 插入时间戳/签名后的文档内容
view3.invalidate_page(0)  # main_window._refresh_page 走这里
check(view3._get_words(0)[0].text == "新插入的文本", "invalidate_page 后取词得到新文本")

print()
if failures:
    print(f"结果：{len(failures)} 项未通过")
    sys.exit(1)
print("结果：全部通过")
