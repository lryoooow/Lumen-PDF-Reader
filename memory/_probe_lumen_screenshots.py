"""Lumen 设计稿视觉验收：离屏渲染各状态截图（用 Windows 字体目录，中文正常显示）。

用法：uv run python memory/_probe_lumen_screenshots.py <输出目录>
"""
import os, sys, tempfile, time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
import pymupdf
from PySide6.QtWidgets import QApplication
from pdfreader.config import settings as st

tmp = Path(tempfile.mkdtemp())
st.default_settings_path = lambda: tmp / "s.json"
from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.panels import SidePanel
from pdfreader.gui.widgets import motion
from pdfreader.engines.types import Rect
from pdfreader.main import create_application

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
paper = tmp / "Hierarchical Attention.pdf"
d = pymupdf.open()
titles = ["Hierarchical Attention for Long-Document Understanding", "2 Related Work", "3 Method", "4 Experiments", "5 Conclusion", "References"]
for i in range(6):
    p = d.new_page()
    p.insert_text((72, 90), titles[i], fontsize=17 if i == 0 else 14)
    p.insert_text((72, 130), "Recent advances in large-scale pretraining have shown that attention-based", fontsize=11)
    p.insert_text((72, 146), "architectures generalize remarkably well across modalities [1].", fontsize=11)
    p.insert_text((72, 176), "我们提出了一种层级注意力机制，在保持全局感受野的同时降低计算复杂度。", fontname="china-s", fontsize=11)
d[5].insert_text((72, 220), "[1] Vaswani A., et al. Attention is all you need. NeurIPS, 2017.", fontsize=11)
w = [x for x in d[0].get_text("words") if x[4] == "[1]."][0]
d[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(w[0], w[1], w[2] - 3, w[3]), "page": 5, "to": pymupdf.Point(72, 210)})
d.set_toc([[1, "摘要", 1], [1, "1 引言", 1], [1, "2 相关工作", 2], [2, "2.1 视觉 Transformer", 2], [1, "3 方法", 3], [2, "3.1 层级注意力", 3], [1, "4 实验", 4], [1, "5 结论", 5], [1, "参考文献", 6]])
d.save(str(paper)); d.close()

app = create_application([sys.argv[0]])
motion.set_enabled(True)

def pump(s=0.9):
    t = time.time()
    while time.time() - t < s:
        app.processEvents()

win = MainWindow(); win.resize(1360, 860); win.show()
win.welcome.set_recent([str(paper)])
win.welcome.showEvent(None) if False else None
pump(); win.grab().save(str(out / "01_welcome_light.png"))

win.open_path(paper); pump()
words = win._controller.get_words(0)
cn = [x for x in words if x.text in "层级注意力"]
win._on_annot_highlight(0, (Rect(cn[0].rect.x0, cn[0].rect.y0, cn[-1].rect.x1, cn[-1].rect.y1),))
win._controller.set_annotation_content(0, 0, "核心动机，可用于开题报告")
pump(); win.grab().save(str(out / "02_reading_toc.png"))

win.sidebar.tabs.setCurrentIndex(SidePanel.TAB_ANNOTATIONS); win._refresh_annotations_if_shown()
win.highlight_action.trigger(); pump(); win.grab().save(str(out / "03_annotations_toolhint.png"))
win.select_action.trigger()

win.search_panel.focus_input(); win._content.reposition_overlay()
win.search_panel._input.setText("attention"); pump(1.6)
win.search_panel.select_relative(1); pump()
win.grab().save(str(out / "04_search.png"))
win.search_panel._on_close()

links = win._controller.get_links(0)
win.pdf_view.jump_to_page(0)
from PySide6.QtCore import QPoint
from PySide6.QtGui import QCursor
QCursor.setPos(win.mapToGlobal(QPoint(700, 300)))
win._on_link_activated(0, links[0]); pump()
shot = win.grab(); pop = win.cite_popover.grab()
from PySide6.QtGui import QPainter
painter = QPainter(shot); painter.drawPixmap(560, 300, pop); painter.end()
shot.save(str(out / "05_cite_popover.png"))
win.cite_popover.hide()

win.sidebar.tabs.setCurrentIndex(SidePanel.TAB_THUMBNAILS); pump(1.2)
win.grab().save(str(out / "06_thumbnails.png"))

win._toggle_theme(); win._set_reading_mode("night")
win.ribbon._tab_group.button(3).click(); pump(1.2)
win.grab().save(str(out / "07_dark_night_view_tab.png"))
win._set_reading_mode("sepia"); win._toggle_theme(); pump(1.0)
win.grab().save(str(out / "08_light_sepia.png"))
win._set_reading_mode("normal")
win._controller.document.mark_saved(); win.close()
print("saved to", out)
