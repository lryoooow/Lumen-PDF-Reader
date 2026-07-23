"""PyInstaller 打包脚本(Windows)。

生成单文件可执行程序 PDFReader.exe。

用法:
    uv run python scripts/build_windows.py

产物: dist/PDFReader.exe
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

__all__ = ["main"]

# 应用名(生成的 exe 文件名)
APP_NAME = "PDFReader"

# 入口(项目根的 src/pdfreader/__main__.py)
ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "src" / "pdfreader" / "__main__.py"

# 需要收集数据的依赖(含模型/数据文件)
HIDDEN_IMPORTS = [
    "rapidocr_onnxruntime",
    "pdf2docx",
    "pdfplumber",
    "openpyxl",
]

COLLECT_ALL = [
    "rapidocr_onnxruntime",
    "pdf2docx",
    "pdfplumber",
    "openpyxl",
    "shapely",
]


def build() -> int:
    """执行 PyInstaller 打包,返回退出码。"""
    dist = ROOT / "dist"
    build_dir = ROOT / "build"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",  # GUI 程序,不弹控制台
        "--onefile",  # 单文件
        f"--distpath={dist}",
        f"--workpath={build_dir}",
        f"--specpath={build_dir}",
        f"--paths={ROOT / 'src'}",
    ]

    # 隐藏导入
    for imp in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", imp])

    # 收集全部数据(模型/资源)
    for pkg in COLLECT_ALL:
        cmd.extend(["--collect-all", pkg])

    # 图标(如有)
    icon = ROOT / "src" / "pdfreader" / "resources" / "icons" / "app.ico"
    if icon.exists():
        cmd.extend(["--icon", str(icon)])

    cmd.append(str(ENTRY))

    print("执行打包命令:")
    print(" ".join(cmd))
    print()

    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        exe = dist / f"{APP_NAME}.exe"
        if exe.exists():
            size_mb = exe.stat().st_size / (1024 * 1024)
            print(f"\n打包成功: {exe}")
            print(f"大小: {size_mb:.1f} MB")
        else:
            print(f"\n警告: 打包返回 0 但 {exe} 不存在")
    else:
        print(f"\n打包失败,退出码 {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(build())
