"""用独立资料目录验收实际冻结程序。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def frozen_selftest(executable: Path, profile: Path, version: str) -> dict[str, Any]:
    """运行冻结自检并验证这次进程写出的报告。"""
    profile.mkdir()
    subprocess.run(
        [str(executable), "--selftest"],
        env={**os.environ, "LUMEN_PROFILE_DIR": str(profile)},
        check=True,
        timeout=180,
    )
    report: dict[str, Any] = json.loads((profile / "selftest.json").read_text("utf-8"))
    if (
        report.get("ok") is not True
        or report.get("frozen") is not True
        or report.get("version") != version
    ):
        raise RuntimeError("冻结自检报告的版本或结果不正确")
    return report


def check_gui_startup(executable: Path, directory: Path, profile: Path) -> None:
    """用同一个隔离资料目录打开样例，验证应用可启动。"""
    sample = directory / "native-smoke.pdf"
    shutil.copyfile(ROOT / "tests/fixtures/samples/simple_text.pdf", sample)
    log_path = directory / "gui-startup.log"
    env = {**os.environ, "LUMEN_PROFILE_DIR": str(profile)}
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    with log_path.open("wb") as log:
        process = subprocess.Popen([str(executable), str(sample)], stdout=log, stderr=log, env=env)
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            application_log = profile / "pdfreader.log"
            if not application_log.is_file() or "进入事件循环" not in application_log.read_text(
                encoding="utf-8", errors="replace"
            ):
                raise RuntimeError("GUI 未在独立资料目录进入事件循环")
        else:
            raise RuntimeError("GUI 提前退出：" + log_path.read_text(errors="replace"))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
