"""复现：加密 PDF 全链路（打开 / 搜索 / OCR / 另存为的加密状态）。

运行：uv run python memory/repro_encrypted_pipeline.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pdfreader.controllers import DocumentController  # noqa: E402
from pdfreader.engines.types import Rect  # noqa: E402
from pdfreader.workers import SearchWorker  # noqa: E402

app = QApplication.instance() or QApplication([])
TMP = ROOT / "memory" / "_tmp"
TMP.mkdir(exist_ok=True)
enc = TMP / "encrypted.pdf"

d = pymupdf.open()
d.new_page().insert_text((72, 72), "SECRET TOKEN")
d.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw1", owner_pw="pw1")
d.close()


def run_search(source: Path) -> list[tuple]:
    """按 MainWindow._on_search_requested 的方式调用 SearchWorker（不传密码）。"""
    got: list[tuple] = []
    w = SearchWorker()
    w.setup(source, "SECRET")
    w.searchCompleted.connect(lambda q, r: got.append(("ok", q, r.hit_count)))
    w.searchFailed.connect(lambda m: got.append(("fail", m)))
    w.run()
    return got


print("=== 1：GUI 打开加密 PDF（MainWindow._open_path 不传密码，也没有密码输入框）===")
ctl = DocumentController()
failures: list[tuple[str, str]] = []
ctl.openFailed.connect(lambda r, m: failures.append((r, m)))
print("open_file(无密码) =", ctl.open_file(enc), "| openFailed 信号 =", failures)

print()
print("=== 2：程序侧带密码打开后，搜索链路 ===")
ctl2 = DocumentController()
print(
    "open_file(password='pw1') =", ctl2.open_file(enc, password="pw1"), "| 页数 =", ctl2.page_count
)
doc = ctl2.document
assert doc is not None
print("is_modified =", doc.is_modified, "→ _worker_source 返回加密原文件 doc.path")
print("搜索（未改动 / 加密文件）→", run_search(doc.path), "  ← SearchWorker/OcrWorker 都收不到密码")

print()
print("=== 3：加了批注走快照 → 搜索能用，但快照没有密码保护 ===")
ctl2.add_highlight(0, Rect(72, 60, 300, 90))
snap = Path(tempfile.gettempdir()) / "pdfreader_snapshot_probe.pdf"
print("save_copy(snapshot) =", ctl2.save_copy(snap))
chk = pymupdf.open(snap)
print(f"快照 needs_pass={chk.needs_pass} is_encrypted={chk.is_encrypted}")
chk.close()
print("搜索（快照）→", run_search(snap))

print()
print("=== 4：另存为 vs Ctrl+S 的加密状态 ===")
out = TMP / "saved_as.pdf"
print("save_as =", ctl2.save_as(out))
chk2 = pymupdf.open(out)
print(f"另存为结果 needs_pass={chk2.needs_pass} is_encrypted={chk2.is_encrypted}（原文件是加密的）")
chk2.close()
