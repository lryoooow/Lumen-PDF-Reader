"""复现：撤销「删除页 / 合并」后 DocumentMeta.page_count 不回滚 → 界面与文件页数不一致。

运行：uv run python memory/repro_undo_pagecount.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfreader.controllers import DocumentController  # noqa: E402

TMP = ROOT / "memory" / "_tmp"
TMP.mkdir(exist_ok=True)


def make_pdf(path: Path, pages: int, tag: str) -> None:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"{tag} page {i + 1}")
    d.save(path)
    d.close()


def engine_pages(ctl: DocumentController) -> int:
    doc = ctl.document
    assert doc is not None and doc._handle is not None
    return doc._engine.page_count(doc._handle)


print("=== 场景 A：删除页 → 撤销（界面页数不回来）===")
src = TMP / "three.pdf"
make_pdf(src, 3, "orig")
ctl = DocumentController()
ctl.open_file(src)
print("打开：界面", ctl.page_count, "页 / 引擎", engine_pages(ctl), "页")
ctl.delete_page(1)
print("删第 2 页：界面", ctl.page_count, "页 / 引擎", engine_pages(ctl), "页")
print("撤销：", ctl.undo())
print("撤销后：界面", ctl.page_count, "页 / 引擎", engine_pages(ctl), "页  ← 不一致")
print("视图据此布局的页数 get_page_sizes() =", len(ctl.get_page_sizes()))
print("撤销后 is_modified =", ctl.is_modified, "（内容已回到打开时的状态，仍算脏）")

print()
print("=== 场景 A2：接着保存 → 文件页数多于界面页数 ===")
print("save_in_place:", ctl.save_in_place())
chk = pymupdf.open(src)
print("磁盘文件", chk.page_count, "页；界面", ctl.page_count, "页 → 第 3 页在界面上看不到")
print("第 3 页内容仍在文件里：", repr(chk[2].get_text().strip()))
chk.close()

print()
print("=== 场景 B：合并 → 撤销（引擎不支持，静默失败）===")
other = TMP / "two.pdf"
make_pdf(other, 2, "merged")
base = TMP / "base.pdf"
make_pdf(base, 2, "base")
ctl2 = DocumentController()
ctl2.open_file(base)
print("合并 added =", ctl2.merge(other), "→ 界面", ctl2.page_count, "页")
print("撤销：", ctl2.undo(), "（引擎 merge 时已重开文档、日志失效）")
print(
    "撤销后 界面",
    ctl2.page_count,
    "页 / 引擎",
    engine_pages(ctl2),
    "页；is_modified =",
    ctl2.is_modified,
)
