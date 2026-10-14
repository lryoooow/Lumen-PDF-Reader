"""分层依赖约束测试。

项目约定（见 docs/ 与各层 __init__ 的"铁律"）：

* ``core``   —— 文档模型层，纯 Python，**不得**依赖 PySide6 / PyMuPDF
* ``services`` —— 服务层，**不得**依赖 PySide6 / PyMuPDF（PDF 读写一律经引擎）
* ``engines`` —— **整个项目唯一可以 import pymupdf 的地方**，且不得依赖 PySide6
* ``workers``/``controllers``/``gui`` —— 允许依赖 Qt
* ``main.py`` —— 仅"依赖自检"命令允许探测 pymupdf 是否可导入

这些约束是"设计可测试性"的地基：一旦某层偷偷直接 import PyMuPDF，
单元测试就会被原生库拖下水，上层替换实现（如换引擎）也会失效。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "pdfreader"

# 层目录 → 该层禁止导入的第三方顶层包
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "core": ("PySide6", "pymupdf"),
    "services": ("PySide6", "pymupdf"),
    "engines": ("PySide6",),
}

# 例外：文件相对 SRC 的路径 → 允许的额外包
EXEMPT: dict[str, tuple[str, ...]] = {
    "engines/pdf_engine.py": ("pymupdf",),  # 引擎实现本体
    "main.py": ("pymupdf",),  # --self-check 探测依赖
}


def _imported_modules(path: Path) -> set[str]:
    """返回文件导入的所有顶层模块名（含函数内导入）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return {name.split(".")[0] for name in modules}


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_sources_found() -> None:
    """扫描目标存在（防止路径写错导致测试空跑）。"""
    files = _python_files()
    assert len(files) > 20
    assert (SRC / "engines" / "pdf_engine.py").exists()


def test_layer_import_boundaries() -> None:
    violations: list[str] = []
    for path in _python_files():
        rel = path.relative_to(SRC).as_posix()
        layer = rel.split("/")[0] if "/" in rel else ""
        banned = set(FORBIDDEN.get(layer, ())) - set(EXEMPT.get(rel, ()))
        if not banned:
            continue
        hit = _imported_modules(path) & banned
        if hit:
            violations.append(f"{rel} 违规导入了 {sorted(hit)}")
    assert not violations, "分层约束被破坏：\n" + "\n".join(violations)


def test_only_engine_imports_pymupdf() -> None:
    """除引擎实现与自查命令外，任何模块都不得直接 import pymupdf。"""
    allowed = {rel for rel, pkgs in EXEMPT.items() if "pymupdf" in pkgs}
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _python_files()
        if path.relative_to(SRC).as_posix() not in allowed and "pymupdf" in _imported_modules(path)
    ]
    assert not offenders, f"只有 {sorted(allowed)} 可以 import pymupdf，违规：{offenders}"


def test_exempt_files_still_exist() -> None:
    """例外清单不残留失效路径。"""
    for rel in EXEMPT:
        assert (SRC / rel).exists(), f"例外清单中的 {rel} 已不存在，请更新测试"
