"""在 macOS 验证实际分发 ZIP：元数据、签名完整性、权限、冻结自检与 GUI 启动。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile

from package_names import macos_archive_name

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
        assert info["CFBundleShortVersionString"] == __version__
        assert info["CFBundleVersion"] == __version__
        assert info["LSMinimumSystemVersion"] == "15.0"
        executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
        assert executable.is_file() and os.access(executable, os.X_OK)
        links = [item for item in app.rglob("*") if item.is_symlink()]
        assert links and all(item.exists() for item in links), "框架符号链接丢失或损坏"
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        subprocess.run([str(executable), "--selftest"], check=True)
        selftest = json.loads((Path.home() / ".pdfreader/selftest.json").read_text("utf-8"))
        assert selftest["ok"] and selftest["frozen"] and selftest["version"] == __version__

        import pymupdf

        sample = extracted / "native-smoke.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "Lumen native macOS launch check")
            document.save(sample)
        # 单独启动本次创建的进程。这里只确认未在启动阶段退出，不替代手工阅读/保存验收。
        with (extracted / "gui-startup.log").open("wb") as log:
            process = subprocess.Popen([str(executable), str(sample)], stdout=log, stderr=log)
            try:
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    raise RuntimeError("GUI 提前退出：" + (extracted / "gui-startup.log").read_text(errors="replace"))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
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
