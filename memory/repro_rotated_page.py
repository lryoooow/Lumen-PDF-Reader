"""复现/回归：旋转页（/Rotate ≠ 0）上的取词、搜索与坐标落点。

运行：uv run python memory/repro_rotated_page.py

本轮发现并修复的问题（详见《复查报告与修复计划》R2-P0-3）：

* 读：引擎 ``open()`` 必开 journal（为了撤销），而 PyMuPDF 的 ``Page.get_text*`` /
  ``search_for`` 会临时把 /Rotate 置 0（写对象），于是**旋转页上取词/搜索/取文本全部报错**，
  表现为「选不中字」「搜索失败」「导出 TXT 失败」；本程序的「旋转页」功能会把普通页
  变成这种页，等于自己触发。
* 写：批注与内容流按**未旋转帧**存坐标，与渲染位图 / GUI 点击换算（可见帧）不一致，
  于是旋转页上插入的时间戳/签名/手写会跑位、橡皮擦点不中。

脚本先用 monkeypatch 还原「旧行为」把故障打出来，再跑「当前实现」确认已修好。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfreader.controllers import DocumentController  # noqa: E402
from pdfreader.engines import pdf_engine  # noqa: E402
from pdfreader.engines.pdf_engine import PyMuPdfEngine  # noqa: E402
from pdfreader.engines.types import Point, Rect  # noqa: E402

TMP = ROOT / "memory" / "_tmp"
TMP.mkdir(exist_ok=True)

ROTATED = ROOT / "tests" / "fixtures" / "samples" / "rotated.pdf"


def make_rotated(path: Path) -> None:
    """400x600 页 + /Rotate=90（可见帧变成 600x400）。"""
    d = pymupdf.open()
    page = d.new_page(width=400, height=600)
    page.insert_text((50, 100), "ROTATED-TARGET", fontsize=14)
    page.set_rotation(90)
    d.save(str(path))
    d.close()


def make_png(path: Path, width: int = 200, height: int = 100) -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pix.set_rect(pix.irect, (200, 30, 30))
    pix.save(str(path))


def diff_bbox(before, after) -> tuple[int, int, int, int] | None:
    x0 = y0 = 10**9
    x1 = y1 = -1
    for y in range(before.height):
        start = y * before.stride
        end = start + before.width * before.n
        row_a, row_b = before.samples[start:end], after.samples[start:end]
        if row_a == row_b:
            continue
        y0, y1 = min(y0, y), max(y1, y)
        for i in range(len(row_a)):
            if row_a[i] != row_b[i]:
                x0, x1 = min(x0, i // before.n), max(x1, i // before.n)
    return None if x1 < 0 else (x0, y0, x1, y1)


def attempt(label: str, fn):  # noqa: ANN201
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:28s} → [异常] {type(exc).__name__}: {str(exc)[:70]}")
        return None


src = TMP / "rotated.pdf"
make_rotated(src)
engine = PyMuPdfEngine()

print("=== 场景 A：旧行为（把读/写换回修复前的做法）===")
_orig_textpage = pdf_engine._textpage
_orig_point = pdf_engine._unrotated_point
_orig_rect = pdf_engine._unrotated_rect

pdf_engine._textpage = lambda page, pno, flags: page.get_textpage(flags=flags)  # noqa: ARG005
pdf_engine._unrotated_point = lambda page, pt: pymupdf.Point(pt.x, pt.y)  # noqa: ARG005
pdf_engine._unrotated_rect = lambda page, r: pymupdf.Rect(r.x0, r.y0, r.x1, r.y1)  # noqa: ARG005
try:
    h = engine.open(src)
    try:
        attempt("get_words（选择/复制）", lambda: engine.get_words(h, 0))
        attempt("search（搜索）", lambda: engine.search(h, "TARGET"))
        attempt("extract_text（导出 TXT）", lambda: engine.extract_text(h, 0))

        click = Point(300.0, 200.0)
        before = engine.render_page(h, 0, 1.0)
        attempt("insert_text（时间戳）", lambda: engine.insert_text(h, 0, click, "STAMP"))
        box = diff_bbox(before, engine.render_page(h, 0, 1.0))
        print(f"  用户点在 ({click.x:.0f}, {click.y:.0f})，"
              f"时间戳实际渲染在 {box} ← 跑位且被转成竖排")
    finally:
        engine.close(h)
finally:
    pdf_engine._textpage = _orig_textpage
    pdf_engine._unrotated_point = _orig_point
    pdf_engine._unrotated_rect = _orig_rect

print()
print("=== 场景 B：当前实现（同一份文件、同样操作）===")
h = engine.open(src)
try:
    words = attempt("get_words（选择/复制）", lambda: engine.get_words(h, 0))
    if words:
        w = words[0]
        print(f"  首个词 rect = ({w.rect.x0:.0f}, {w.rect.y0:.0f}, {w.rect.x1:.0f}, {w.rect.y1:.0f})"
              f" ← 可见帧（x>400，未旋转帧只有 400 宽）")
    hits = attempt("search（搜索）", lambda: engine.search(h, "TARGET"))
    if hits:
        print(f"  命中 {len(hits.hits)} 处，首处 page={hits.hits[0].page_num} "
              f"rect.x0={hits.hits[0].rect.x0:.0f}")
    text = attempt("extract_text（导出 TXT）", lambda: engine.extract_text(h, 0))
    if text:
        print(f"  文本 = {text.full_text.strip()!r}")

    click = Point(300.0, 200.0)
    attempt("insert_text（时间戳）", lambda: engine.insert_text(h, 0, click, "STAMP"))
    words = engine.get_words(h, 0)
    stamp = next((w for w in words if "STAMP" in w.text), None)
    if stamp:
        print(f"  点击可见点 ({click.x:.0f}, {click.y:.0f}) → 文字 rect.x0={stamp.rect.x0:.0f}"
              f" rect.y1={stamp.rect.y1:.0f}，横排={stamp.rect.width > stamp.rect.height}")

    note_point = Point(300.0, 300.0)
    attempt("add_text_annot（便签）", lambda: engine.add_text_annot(h, 0, note_point, "note"))
    idx = engine.annotation_index_at(h, 0, note_point)
    print(f"  橡皮擦用可见点查批注 → index={idx}（修复前为 None，点不中）")

    target = Rect(120.0, 160.0, 220.0, 210.0)
    png = TMP / "sig.png"
    make_png(png)
    before = engine.render_page(h, 0, 1.0)
    attempt("insert_image（签名）", lambda: engine.insert_image(h, 0, target, png))
    box = diff_bbox(before, engine.render_page(h, 0, 1.0))
    print(f"  签名目标可见矩形=({target.x0:.0f}, {target.y0:.0f}, {target.x1:.0f}, {target.y1:.0f})"
          f" → 渲染差异框={box}（修复前是竖条并被搬到页面另一侧）")
finally:
    engine.close(h)

print()
print("=== 场景 C：本程序「旋转页」功能之后，第 0 页仍可选中/搜索 ===")
plain = TMP / "plain.pdf"
d = pymupdf.open()
d.new_page(width=400, height=600).insert_text((50, 100), "PLAIN-PAGE", fontsize=14)
d.save(str(plain))
d.close()

ctl = DocumentController()
ctl.open_file(plain)
print("  旋转前：", [w.text for w in ctl.get_words(0)])
print("  rotate_page(0, 90) →", ctl.rotate_page(0, 90))
words = ctl.get_words(0)
print("  旋转后：get_words =", [(w.text, round(w.rect.x0), round(w.rect.y0)) for w in words])
doc = ctl.document
assert doc is not None
print("  旋转后：search('PLAIN') =", [hit.page_num for hit in doc.search("PLAIN").hits])
print("  ↑ 修复前这里 get_words 为空（异常被吞）、search 直接报错")
print()
print("对照样例（随仓库提交）：", ROTATED.relative_to(ROOT),
      "→ tests/unit/engines/test_rotated_pages.py 覆盖以上各点")
