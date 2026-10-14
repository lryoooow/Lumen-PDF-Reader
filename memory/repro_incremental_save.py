"""验证：另存为之后 / 合并之后，「保存」仍能成功（原 P0-1 的回归脚本）。

修复前实测：场景 1、2 均抛 `FileOpenError: 保存到 X.pdf 失败：incremental needs original file`，
于是 MainWindow._do_save 恒退化为另存为对话框。

修复后：目标已不是打开时的原文件（另存为 / 合并后为内存文档）时自动整份保存；
仍是原文件时保持增量保存（更快、保留加密）。

运行：uv run python memory/repro_incremental_save.py   （全部 PASS 时退出码 0）
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from pdfreader.core import Document
from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.types import Rect

SAMPLE = Path("tests/fixtures/samples/simple_text.pdf")
tmp = Path(tempfile.mkdtemp(prefix="pdfreader_repro_"))
engine = PyMuPdfEngine()
failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


# ---- 场景 1：另存为之后，再按「保存」 --------------------------------------
a = tmp / "A.pdf"
b = tmp / "B.pdf"
shutil.copy(SAMPLE, a)
doc = Document.open(engine, a)
print("1) 打开:", a.name, "engine doc.name =", engine.open(a).native.name)

doc.add_highlight(0, Rect(60, 60, 200, 80))
doc.save(b)  # 另存为
doc.path = b  # 控制器在 save_as 里做的事
print("   save_as 之后 doc.path =", doc.path.name)

try:
    doc.save(b, incremental=True)
    check(True, "增量存回 B.pdf 成功（自动整份保存）")
except Exception as exc:
    check(False, f"增量存回 B.pdf 失败 -> {type(exc).__name__}: {exc}")

# 保存后的 B.pdf 必须真的含批注
reopened = Document.open(PyMuPdfEngine(), b)
check(len(reopened.list_annotations(0)) == 1, "存回后的 B.pdf 中批注仍在")
reopened.close()

# ---- 场景 2：合并之后，再按「保存」 ----------------------------------------
c = tmp / "C.pdf"
shutil.copy(SAMPLE, c)
doc2 = Document.open(engine, c)
added = doc2.merge(SAMPLE)
print(f"2) 合并 {added} 页；native.stream =", bool(doc2._handle.native.stream))
try:
    doc2.save(c, incremental=True)
    check(True, "合并后存回 C.pdf 成功（自动整份保存）")
except Exception as exc:
    check(False, f"合并后存回 C.pdf 失败 -> {type(exc).__name__}: {exc}")
saved_pages = Document.open(PyMuPdfEngine(), c).page_count
check(
    saved_pages == doc2.page_count,
    f"存回后的 C.pdf 页数 = {saved_pages}（期望 {doc2.page_count}）",
)

# ---- 场景 3：未另存为、未合并（正常路径，仍走增量）-------------------------
d = tmp / "D.pdf"
shutil.copy(SAMPLE, d)
doc3 = Document.open(engine, d)
doc3.add_highlight(0, Rect(60, 60, 200, 80))
size_before = d.stat().st_size
doc3.save(d, incremental=True)
check(True, f"普通增量保存成功，文件大小 {size_before} → {d.stat().st_size}")

doc.close()
doc2.close()
doc3.close()
print("tmp =", tmp)
if failures:
    print(f"结果：{len(failures)} 项未通过")
    sys.exit(1)
print("结果：全部通过")
