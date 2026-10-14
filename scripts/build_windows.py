"""PyInstaller 打包脚本（Windows）。

生成**目录版**（onedir）程序 dist/PDFReader/PDFReader.exe——相比单文件版
启动更快（无需每次解压 ~180MB），并作为 NSIS 安装程序的输入。

用法:
    uv run python scripts/build_windows.py            # 仅打包
    uv run python scripts/build_windows.py --installer # 打包并生成安装程序

产物:
    dist/PDFReader/                         程序目录（onedir）
    dist/PDFReader-Setup-<version>.exe      安装程序（需 NSIS / makensis）
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

# 让 Windows 控制台也能输出中文（避免 cp936/cp950 报错）
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

__all__ = ["build", "main"]

APP_NAME = "PDFReader"
ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "src" / "pdfreader" / "__main__.py"
ICON = ROOT / "src" / "pdfreader" / "resources" / "icons" / "app.ico"

# 需显式声明的隐藏导入（动态导入 / 可选依赖）
HIDDEN_IMPORTS = [
    "rapidocr_onnxruntime",
    "onnxruntime",
    "cv2",
    "pdf2docx",
    "pdfplumber",
    "openpyxl",
    "PySide6.QtPrintSupport",  # 打印
]

# 需连同数据文件（模型 / 配置）一起收集的包
COLLECT_ALL = [
    "rapidocr_onnxruntime",  # OCR 模型 config.yaml + *.onnx
    "onnxruntime",  # onnx 运行时原生 DLL（否则 OCR 初始化失败）
    "pdf2docx",
    "pdfplumber",
    "openpyxl",
    "shapely",
]


def _version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from pdfreader import __version__

    return __version__


def build() -> int:
    """执行 PyInstaller 打包（onedir），返回退出码。"""
    dist = ROOT / "dist"
    build_dir = ROOT / "build"

    # 清掉旧的 onefile 产物，避免与 onedir 混淆
    old_exe = dist / f"{APP_NAME}.exe"
    if old_exe.exists():
        old_exe.unlink()
    if (dist / APP_NAME).exists():
        shutil.rmtree(dist / APP_NAME, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",  # GUI 程序，不弹控制台
        "--onedir",  # 目录版：启动快，供安装程序打包
        f"--distpath={dist}",
        f"--workpath={build_dir}",
        f"--specpath={build_dir}",
        f"--paths={ROOT / 'src'}",
    ]
    for imp in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", imp])
    for pkg in COLLECT_ALL:
        cmd.extend(["--collect-all", pkg])
    if ICON.exists():
        cmd.extend(["--icon", str(ICON)])
    cmd.append(str(ENTRY))

    print("执行打包命令:\n" + " ".join(cmd) + "\n")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\n❌ 打包失败，退出码 {result.returncode}")
        return result.returncode

    return _verify(dist)


def _verify(dist: Path) -> int:
    """校验 onedir 产物：exe 存在 + OCR 模型已打包。"""
    app_dir = dist / APP_NAME
    exe = app_dir / f"{APP_NAME}.exe"
    if not exe.exists():
        print(f"\n❌ 未找到可执行文件: {exe}")
        return 1

    # onedir 依赖放在 _internal 下
    internal = app_dir / "_internal"
    models = internal / "rapidocr_onnxruntime" / "models"
    onnx_files = list(models.glob("*.onnx")) if models.exists() else []
    ort_dlls = list(internal.glob("onnxruntime*/**/*.dll")) if internal.exists() else []

    total = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print("\n✅ 打包成功（onedir）")
    print(f"   程序目录: {app_dir}")
    print(f"   可执行:   {exe}")
    print(f"   总大小:   {total / (1024 * 1024):.1f} MB")
    print(f"   OCR 模型: {len(onnx_files)} 个 .onnx {'✓' if onnx_files else '✗（OCR 可能失效）'}")
    print(f"   onnx DLL: {'✓' if ort_dlls else '（依赖 PyInstaller hook）'}")
    if not onnx_files:
        print("   ⚠️  未检测到 OCR 模型文件，OCR 功能可能不可用。")
    return 0


def make_installer() -> int:
    """用 NSIS（makensis）编译正规向导式安装程序 PDFReader-Setup-<version>.exe。

    优先使用系统 makensis；否则复用 electron-builder 缓存的 NSIS（本机常见）。
    """
    nsi = ROOT / "scripts" / "installer.nsi"
    if not nsi.exists():
        print(f"⚠️  未找到 {nsi}，跳过安装程序生成。")
        return 0

    makensis = _find_makensis()
    if makensis is None:
        print(
            "\nℹ️  未找到 NSIS（makensis.exe）。安装程序未生成。\n"
            "   NSIS 下载：https://nsis.sourceforge.io/  （或安装 electron-builder 会缓存一份）\n"
            f"   找到后运行：\"<NSIS>\\makensis.exe\" /DVERSION={_version()} {nsi}"
        )
        return 0

    version = _version()
    sourcedir = ROOT / "dist" / "PDFReader"
    outfile = ROOT / "dist" / f"PDFReader-Setup-{version}.exe"
    print(f"\n使用 NSIS 编译安装程序: {makensis}")
    # 传绝对路径的 /D 定义（避免 cwd 依赖）；脚本带 UTF-8 BOM，makensis 自动识别编码。
    result = subprocess.run(
        [
            str(makensis),
            f"/DVERSION={version}",
            f"/DSOURCEDIR={sourcedir}",
            f"/DOUTFILE={outfile}",
            f"/DICON={ICON}",
            str(nsi),
        ],
        cwd=str(ROOT),
        check=False,
    )
    setup = ROOT / "dist" / f"PDFReader-Setup-{version}.exe"
    if result.returncode == 0 and setup.exists():
        size_mb = setup.stat().st_size / (1024 * 1024)
        print(f"\n✅ 安装程序已生成: {setup}  ({size_mb:.1f} MB)")
    else:
        print(f"\n❌ 安装程序编译失败，退出码 {result.returncode}")
    return result.returncode


def _find_makensis() -> Path | None:
    """定位 makensis.exe：系统 PATH → electron-builder 缓存 → 常见安装位置。"""
    found = shutil.which("makensis")
    if found:
        return Path(found)
    import os

    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cache = Path(local) / "electron-builder" / "Cache" / "nsis"
        if cache.exists():
            candidates.extend(sorted(cache.glob("nsis-*/makensis.exe"), reverse=True))
    candidates += [
        Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
        Path(r"C:\Program Files\NSIS\makensis.exe"),
    ]
    return next((c for c in candidates if c.exists()), None)


def main() -> int:
    code = build()
    if code != 0:
        return code
    if "--installer" in sys.argv:
        return make_installer()
    print("\n提示：加 --installer 参数可同时生成 Windows 安装程序。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
