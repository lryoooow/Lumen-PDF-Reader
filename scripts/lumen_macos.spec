# -*- mode: python ; coding: utf-8 -*-
"""macOS 原生应用包；在 macOS 15 构建，以兼容仍受支持的旧系统。"""
from pathlib import Path
import platform
import sys

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "scripts"))
from pdfreader import __version__
from build_desktop import COLLECT_ALL

datas = [(str(root / "src/pdfreader/resources/dict"), "pdfreader/resources/dict")]
binaries = []
hiddenimports = ["PySide6.QtPrintSupport", "PySide6.QtTextToSpeech", "keyring.backends.macOS"]
for package in COLLECT_ALL:
    package_data, package_binaries, package_imports = collect_all(package)
    datas.extend(package_data)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)

analysis = Analysis(
    [str(root / "src/pdfreader/__main__.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name="PDFReader",
    console=False,
    target_arch=platform.machine(),
)
collection = COLLECT(executable, analysis.binaries, analysis.datas, name="PDFReader")
app = BUNDLE(
    collection,
    name="PDFReader.app",
    icon=str(root / "src/pdfreader/resources/icons/app.ico"),
    bundle_identifier="io.github.lryoooow.lumen",
    version=__version__,
    info_plist={
        "CFBundleDisplayName": "Lumen",
        "CFBundleName": "Lumen",
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
    },
)
