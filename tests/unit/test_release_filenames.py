"""发布文件名须让用户直接辨认系统和 Mac 芯片类型。"""

from __future__ import annotations

import importlib
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("architecture", "expected"),
    [
        ("arm64", "Lumen-0.0.6-macOS-Apple-Silicon.zip"),
        ("x86_64", "Lumen-0.0.6-macOS-Intel.zip"),
    ],
)
def test_macos_build_uses_download_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, architecture: str, expected: str
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    builder = importlib.import_module("build_desktop")
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder.sys, "path", list(builder.sys.path))
    monkeypatch.setattr("pdfreader.__version__", "0.0.6")
    monkeypatch.setattr(
        builder, "platform", SimpleNamespace(system=lambda: "Darwin", machine=lambda: architecture)
    )
    monkeypatch.setattr(builder, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setattr(builder.sys, "argv", ["build_desktop.py"])

    assert builder.main() == 0
    archive_call = next(call for call in calls if call[0] == "ditto")
    assert archive_call[-1] == str(tmp_path / "dist" / expected)
    assert "--keepParent" in archive_call


def test_windows_installer_name_shows_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    builder = importlib.import_module("build_windows")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "installer.nsi").touch()
    (tmp_path / "dist").mkdir()
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        output = next(
            argument.removeprefix("/DOUTFILE=")
            for argument in arguments
            if argument.startswith("/DOUTFILE=")
        )
        Path(output).write_bytes(b"installer test")
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "_version", lambda: "0.0.6")
    monkeypatch.setattr(builder, "_find_makensis", lambda: Path("makensis.exe"))
    monkeypatch.setattr(builder, "subprocess", SimpleNamespace(run=run))

    assert builder.make_installer() == 0
    expected = tmp_path / "dist" / "Lumen-0.0.6-Windows-x64-Setup.exe"
    assert f"/DOUTFILE={expected}" in calls[0]
    assert expected.is_file()


def test_nsis_default_matches_windows_download_name(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    names = importlib.import_module("package_names")
    installer = (root / "scripts" / "installer.nsi").read_text(encoding="utf-8-sig")
    assert f'!define OUTFILE "dist\\{names.windows_installer_name("${VERSION}")}"' in installer


def test_unknown_mac_chip_is_not_mislabeled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    names = importlib.import_module("package_names")
    with pytest.raises(ValueError, match="不支持的 macOS 架构"):
        names.macos_archive_name("0.0.6", "unknown")


@pytest.mark.parametrize("architecture", ["AMD64", "x86_64"])
def test_windows_portable_is_distinct_from_installer(
    monkeypatch: pytest.MonkeyPatch, architecture: str
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    names = importlib.import_module("package_names")
    assert names.desktop_archive_name("0.0.6", "Windows", architecture) == (
        "Lumen-0.0.6-Windows-x64-Portable.zip"
    )
