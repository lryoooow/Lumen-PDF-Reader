"""找出 src/ 中「只被自身模块或测试引用」的模块/符号（死代码体检）。"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path("src")
files = list(SRC.rglob("*.py"))
texts = {p: p.read_text(encoding="utf-8") for p in files}

# 1) 模块级：谁 import 了谁
imports: dict[str, set[str]] = {}
for p in files:
    mod = ".".join(p.relative_to(SRC).with_suffix("").parts)
    names: set[str] = set()
    try:
        tree = ast.parse(texts[p])
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    imports[mod] = names

print("== 没有任何 src 模块 import 的模块 ==")
for mod in sorted(imports):
    if mod.endswith(".__init__"):
        continue
    if any(
        mod == i or i.startswith(mod + ".") for m, ns in imports.items() if m != mod for i in ns
    ):
        continue
    print("  ", mod)

# 2) 模块内定义的公开名，是否在「其它文件」里出现过（粗粒度）
print("\n== 定义后从未在其它源文件中出现的公开名 ==")
for p in files:
    mod = ".".join(p.relative_to(SRC).with_suffix("").parts)
    tree = ast.parse(texts[p])
    defined: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                defined.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    defined.append(t.id)
    others = "\n".join(t for f, t in texts.items() if f != p)
    unused = [n for n in defined if n not in others]
    if unused:
        print(f"  {mod}: {', '.join(unused)}")
