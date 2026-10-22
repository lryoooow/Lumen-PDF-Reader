"""原生桌面目录包构建；必须在目标系统执行，不声称交叉编译。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def build_command(target: str) -> list[str]:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name=PDFReader", f"--paths={ROOT / 'src'}",
               f"--distpath={ROOT / 'dist' / 'native'}",
               f"--workpath={ROOT / 'build' / 'native'}", f"--specpath={ROOT / 'build' / 'native'}"]
    if target in ("Windows", "Darwin"):
        command.append("--windowed")
    for package in ("rapidocr_onnxruntime", "onnxruntime", "pdf2docx", "pdfplumber", "openpyxl",
                    "shapely", "keyring", "certifi", "bibtexparser", "regex", "docx"):
        command.extend(["--collect-all", package])
    command.extend(["--hidden-import=PySide6.QtPrintSupport", "--hidden-import=PySide6.QtTextToSpeech"])
    dictionary = ROOT / "src" / "pdfreader" / "resources" / "dict"
    if dictionary.is_dir():
        command.extend(["--add-data", f"{dictionary}{os.pathsep}pdfreader/resources/dict"])
    command.append(str(ROOT / "src" / "pdfreader" / "__main__.py"))
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target", choices=("Windows", "Linux", "Darwin"), default=platform.system())
    arguments = parser.parse_args()
    command = build_command(arguments.target)
    if arguments.dry_run:
        print(subprocess.list2cmdline(command))
        return 0
    if arguments.target != platform.system():
        parser.error("原生构建必须在目标操作系统执行")
    if arguments.target == "Windows":
        import build_windows

        if build_windows.build():
            return 1
        native = ROOT / "dist"
    else:
        if subprocess.run(command, cwd=ROOT, check=False).returncode:
            return 1
        native = ROOT / "dist" / "native"
    if arguments.target == "Darwin":
        executable = native / "PDFReader.app" / "Contents" / "MacOS" / "PDFReader"
    else:
        executable = native / "PDFReader" / ("PDFReader.exe" if arguments.target == "Windows" else "PDFReader")
    if subprocess.run([str(executable), "--selftest"], check=False).returncode:
        return 1
    sys.path.insert(0, str(ROOT / "src"))
    from pdfreader import __version__
    name = ROOT / "dist" / f"Lumen-{__version__}-{arguments.target}-{platform.machine()}"
    source = "PDFReader.app" if arguments.target == "Darwin" else "PDFReader"
    shutil.make_archive(str(name), "zip", root_dir=native, base_dir=source)
    print(str(name) + ".zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
