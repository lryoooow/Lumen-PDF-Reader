"""在 macOS 验证实际分发 ZIP：元数据、签名完整性、权限、冻结自检与 GUI 启动。"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

from package_names import macos_archive_name
from package_smoke import check_gui_startup, frozen_selftest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pdfreader import __version__


def main() -> int:
    if platform.system() != "Darwin":
        raise SystemExit("此验证必须在 macOS 执行")
    archive = ROOT / "dist" / macos_archive_name(__version__, platform.machine())
    with tempfile.TemporaryDirectory(prefix="lumen-package-test-") as directory:
        extracted = Path(directory)
        subprocess.run(["ditto", "-x", "-k", str(archive), str(extracted)], check=True)
        app = extracted / "PDFReader.app"
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        if (
            info["CFBundleShortVersionString"] != __version__
            or info["CFBundleVersion"] != __version__
            or info["LSMinimumSystemVersion"] != "15.0"
        ):
            raise RuntimeError("应用包版本或最低系统版本不正确")
        executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError("应用包缺少可执行文件或执行权限")
        architectures = subprocess.check_output(["lipo", "-archs", str(executable)], text=True)
        if architectures.split() != [platform.machine()]:
            raise RuntimeError("实际 Mach-O 架构与归档名不一致")
        links = [item for item in app.rglob("*") if item.is_symlink()]
        if not links or not all(item.exists() for item in links):
            raise RuntimeError("框架符号链接丢失或损坏")
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        profile = extracted / "profile"
        selftest = frozen_selftest(executable, profile, __version__)
        check_gui_startup(executable, extracted, extracted / "gui-profile")
        report = {
            "version": __version__,
            "architecture": platform.machine(),
            "macos": platform.mac_ver()[0],
            "minimum_macos": info["LSMinimumSystemVersion"],
            "archive": archive.name,
            "symlinks": len(links),
            "codesign_integrity": "verified (ad-hoc, not Developer ID or notarized)",
            "frozen_selftest": selftest,
            "gui_startup": "alive after 6 seconds (offscreen, not manual UI certification)",
        }
        output = ROOT / "dist" / f"macos-verification-{platform.machine()}.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output.read_text("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
