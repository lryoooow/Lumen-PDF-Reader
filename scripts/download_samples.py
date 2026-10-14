"""生成测试用样例 PDF。

运行：``uv run python scripts/download_samples.py``

生成多种类型的 PDF 到 ``tests/fixtures/samples/``，覆盖引擎层测试所需场景：
- simple_text.pdf：纯文本、多页、含可搜索关键词
- with_tables.pdf：含表格
- with_annotations.pdf：预置批注
- scanned.pdf：扫描件（纯图片页，无文本层）
- encrypted.pdf：加密 PDF（密码 "secret"）
- empty.pdf：空白页文档（1 页无内容）
- corrupt.pdf：损坏文件（非 PDF 内容）

这些样例随仓库提交，保证测试可复现、不依赖外部下载。
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "samples"


def make_simple_text(out: Path) -> None:
    """多页纯文本 PDF，含固定关键词便于搜索测试。"""
    doc = pymupdf.open()
    for i in range(1, 4):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text(
            (72, 100),
            f"Page {i} of Sample Document",
            fontsize=18,
        )
        page.insert_text(
            (72, 140),
            "This document contains the keyword PyMuPDF for search testing.",
            fontsize=12,
        )
        page.insert_text(
            (72, 170),
            f"这里是中文测试内容，第 {i} 页。关键词：测试成功。",
            fontsize=12,
            fontname="china-s",  # 内置简体中文字体
        )
    doc.save(out)
    doc.close()


def make_with_tables(out: Path) -> None:
    """含简单表格的 PDF。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    # 用 insert_text 配合线条画一个简易表格
    headers = ["Name", "Age", "City"]
    rows = [["Alice", "30", "Beijing"], ["Bob", "25", "Shanghai"], ["Carol", "40", "Guangzhou"]]
    x0, y0 = 72, 100
    col_w = 120
    row_h = 24
    # 表头
    for c, h in enumerate(headers):
        page.insert_text((x0 + c * col_w + 4, y0 + 16), h, fontsize=11)
    # 数据行 + 网格线
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            page.insert_text((x0 + c * col_w + 4, y0 + r * row_h + 16), val, fontsize=11)
    # 横线
    for r in range(len(rows) + 1):
        y = y0 + r * row_h
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x0 + col_w * len(headers), y))
    # 竖线
    for c in range(len(headers) + 1):
        x = x0 + c * col_w
        page.draw_line(pymupdf.Point(x, y0), pymupdf.Point(x, y0 + row_h * len(rows)))
    doc.save(out)
    doc.close()


def make_with_annotations(out: Path) -> None:
    """预置高亮和文本批注的 PDF。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Annotated page content here.", fontsize=12)
    # 高亮一个矩形区域
    page.add_highlight_annot(pymupdf.Rect(70, 90, 300, 110))
    # 文本便签
    page.add_text_annot(pymupdf.Point(200, 200), "A sticky note annotation")
    doc.save(out)
    doc.close()


def make_scanned(out: Path) -> None:
    """扫描件：纯图片页，无文本层。用较小尺寸图片控制体积。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    # 生成一张带文字的小图片再嵌入（图片里的文字不可被文本提取）
    img_doc = pymupdf.open()
    img_page = img_doc.new_page(width=300, height=150)
    img_page.insert_text((20, 40), "Image-only scanned text.", fontsize=10)
    # 低 DPI + JPEG 压缩，控制体积在 1MB 以下
    pix = img_page.get_pixmap(dpi=72)
    img_bytes = pix.tobytes("jpeg")
    page.insert_image(pymupdf.Rect(72, 100, 372, 250), stream=img_bytes)
    img_doc.close()
    doc.save(out, deflate=True, garbage=4)
    doc.close()


def make_encrypted(out: Path) -> None:
    """加密 PDF，用户密码 'secret'。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Encrypted content.", fontsize=12)
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    doc.close()


def make_rotated(out: Path) -> None:
    """含 /Rotate=90 页的 PDF：坐标帧与撤销日志的回归样例。

    第 0 页旋转 90°（原 400x600 → 可见 600x400），第 1 页不旋转。
    两页都有可搜索关键词与可选中文字，用于覆盖「旋转页上取词/搜索/批注/插入」。
    """
    doc = pymupdf.open()
    rotated = doc.new_page(width=400, height=600)
    rotated.insert_text((50, 100), "Rotated Page Keyword", fontsize=14)
    rotated.set_rotation(90)
    plain = doc.new_page(width=400, height=600)
    plain.insert_text((50, 100), "Plain Page Keyword", fontsize=14)
    doc.save(out)
    doc.close()


def make_empty(out: Path) -> None:
    """空白页文档（1 页，无任何内容）。用于测试空内容页的渲染/提取。"""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(out)
    doc.close()


def make_corrupt(out: Path) -> None:
    """损坏文件：内容不是合法 PDF。"""
    out.write_bytes(b"This is not a valid PDF file.%EOF")


def main() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    generators = {
        "simple_text.pdf": make_simple_text,
        "with_tables.pdf": make_with_tables,
        "with_annotations.pdf": make_with_annotations,
        "scanned.pdf": make_scanned,
        "encrypted.pdf": make_encrypted,
        "rotated.pdf": make_rotated,
        "empty.pdf": make_empty,
        "corrupt.pdf": make_corrupt,
    }
    for name, gen in generators.items():
        target = SAMPLES_DIR / name
        gen(target)
        size = target.stat().st_size
        print(f"  ✓ {name:<24} {size:>8} bytes")
    print(f"\n生成完成，共 {len(generators)} 个样例 → {SAMPLES_DIR}")


if __name__ == "__main__":
    main()
