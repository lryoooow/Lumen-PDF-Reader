"""第三轮复查探针：引擎 / core / 控制器层（纯逻辑，无 GUI）。

用法：uv run python memory/_probe_round3_engine.py
每一节打印 [事实] 与 [结论]，用于把代码走查里的怀疑变成实测证据。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pymupdf

from pdfreader.core import Document
from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.types import Point, Rect

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "fixtures" / "samples"
TMP = Path(tempfile.mkdtemp(prefix="probe_r3_"))
eng = PyMuPdfEngine()


def make_pdf(path: Path, pages: int = 3, text: str = "hello world line") -> Path:
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 72), f"page {i + 1} {text}")
        p.insert_text((72, 100), "alpha beta gamma delta epsilon zeta")
    d.save(str(path))
    d.close()
    return path


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- A 另存为选中原文件
section("A 另存为目标 == 当前打开的原文件")
src = make_pdf(TMP / "a.pdf")
doc = Document.open(eng, src)
doc.add_highlight(0, Rect(72, 60, 200, 80))
try:
    doc.save(src)  # 控制器 save_as 走的就是 incremental=False
    print("[事实] 整份保存到原文件：成功")
except Exception as exc:
    print(f"[事实] 整份保存到原文件：失败 -> {type(exc).__name__}: {str(exc)[:120]}")
doc.close()

# ---------------------------------------------------------------- B 脏标记：分叉历史
section("B 保存后撤销再做新编辑 → 重做回到保存位置时是否被误判为干净")
src = make_pdf(TMP / "b.pdf")
doc = Document.open(eng, src)
doc.add_highlight(0, Rect(72, 60, 200, 80))  # pos 1
doc.add_highlight(0, Rect(72, 90, 200, 110))  # pos 2
doc.save(src, incremental=True)  # saved @2
print("保存后 is_modified =", doc.is_modified, "saved_pos =", doc._saved_position)
doc.undo()  # pos 1
doc.add_text_annot(1, Point(100, 100), "新分支")  # 新分支 pos 2（内容已与磁盘不同）
print("新分支编辑后 is_modified =", doc.is_modified)
doc.undo()
doc.redo()  # 回到 pos 2（新分支）
print("新分支 undo+redo 后 is_modified =", doc.is_modified, "(期望 True：内容≠磁盘)")
doc.close()

# ---------------------------------------------------------------- C 合并失败后文档是否还能用
section("C 合并一个加密 / 损坏文件失败后，当前文档是否仍可用、撤销历史是否被白白丢掉")
for other in ("encrypted.pdf", "corrupt.pdf"):
    src = make_pdf(TMP / f"c_{other}")
    doc = Document.open(eng, src)
    doc.add_highlight(0, Rect(72, 60, 200, 80))
    print(f"[{other}] 合并前 can_undo =", doc.can_undo())
    try:
        doc.merge(SAMPLES / other)
        print(f"[{other}] 合并：成功（意外）")
    except Exception as exc:
        print(f"[{other}] 合并失败：{type(exc).__name__}: {str(exc)[:90]}")
    try:
        print(f"[{other}] 失败后 page_count =", doc.page_count, "can_undo =", doc.can_undo(),
              "is_modified =", doc.is_modified)
        doc.render_page(0)
        print(f"[{other}] 失败后渲染第 0 页：OK")
    except Exception as exc:
        print(f"[{other}] 失败后文档不可用：{type(exc).__name__}: {str(exc)[:90]}")
    doc.close()

# ---------------------------------------------------------------- D 搜索片段
section("D 搜索结果片段是否截掉命中之后的文字")
src = make_pdf(TMP / "d.pdf")
doc = Document.open(eng, src)
res = doc.search("gamma")
for h in res.hits[:1]:
    print("[事实] 查询 gamma 的片段 =", repr(h.text), "（原行：alpha beta gamma delta epsilon zeta）")
doc.close()

# ---------------------------------------------------------------- E 批注类型常量
section("E AnnotationType 常量 vs PyMuPDF")
from pdfreader.engines.types import AnnotationType as AT  # noqa: E402

for name, ours, real in (
    ("SQUIGGLY", AT.SQUIGGLY, pymupdf.PDF_ANNOT_SQUIGGLY),
    ("SQUARE", AT.SQUARE, pymupdf.PDF_ANNOT_SQUARE),
    ("CIRCLE", AT.CIRCLE, pymupdf.PDF_ANNOT_CIRCLE),
    ("STRIKEOUT", AT.STRIKEOUT, pymupdf.PDF_ANNOT_STRIKE_OUT),
):
    print(f"{name}: ours={ours} pymupdf={real} {'OK' if ours == real else '不一致'}")

# ---------------------------------------------------------------- F 水印是否遮挡正文
section("F 水印：是否不透明遮挡正文、中文是否居中")
src = make_pdf(TMP / "f.pdf")
doc = Document.open(eng, src)
doc.add_watermark(0, "内部机密文件")
h = doc._handle
page = h.native[0]
spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
         for s in line["spans"] if "机密" in s["text"]]
if spans:
    x0, _, x1, _ = spans[0]["bbox"]
    print(f"[事实] 中文水印 bbox x0={x0:.0f} x1={x1:.0f}，页宽={page.rect.width:.0f}，"
          f"左右留白 {x0:.0f} / {page.rect.width - x1:.0f}")
gs = [x for x in h.native.xref_get_keys(page.get_contents()[0])] if page.get_contents() else []
cont = b"".join(h.native.xref_stream(x) for x in page.get_contents())
print("[事实] 内容流含透明度设置 (/gs 或 ca)：", b" gs" in cont or b"/ca" in cont)
doc.close()

# ---------------------------------------------------------------- G 删除最后一页
section("G 删到 0 页后能否保存")
src = make_pdf(TMP / "g.pdf", pages=1)
doc = Document.open(eng, src)
try:
    doc.delete_page(0)
    print("[事实] 删除唯一一页：成功，page_count =", doc.page_count)
    doc.save(src, incremental=True)
    print("[事实] 0 页文档保存：成功")
except Exception as exc:
    print(f"[事实] {type(exc).__name__}: {str(exc)[:120]}")
doc.close()

# ---------------------------------------------------------------- H 中文取词粒度
section("H 中文文本的取词粒度（选择/高亮的最小单位）")
src = TMP / "h.pdf"
d = pymupdf.open()
p = d.new_page()
p.insert_text((72, 72), "深度学习在自然语言处理中的应用研究综述", fontname="china-s", fontsize=12)
d.save(str(src))
d.close()
doc = Document.open(eng, src)
words = doc.get_words(0)
print("[事实] 词数 =", len(words), "；各词 =", [w.text for w in words])
doc.close()

# ---------------------------------------------------------------- I 修复过的文件能否增量保存
section("I 损坏后被 MuPDF 修复的文件：Ctrl+S（增量保存）是否必然失败")
src = make_pdf(TMP / "i.pdf")
raw = src.read_bytes()
idx = raw.rfind(b"startxref")
src.write_bytes(raw[:idx] + b"startxref\n999999\n%%EOF\n")  # 破坏 xref 偏移
doc = Document.open(eng, src)
print("[事实] can_save_incrementally =", doc._handle.native.can_save_incrementally())
doc.add_highlight(0, Rect(72, 60, 200, 80))
try:
    doc.save(src, incremental=True)
    print("[事实] 增量保存：成功")
except Exception as exc:
    print(f"[事实] 增量保存失败：{str(exc)[:120]}")
doc.close()

shutil.rmtree(TMP, ignore_errors=True)
