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
    monkeypatch.setattr(builder.tempfile, "tempdir", str(tmp_path))
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
    monkeypatch.setattr(builder, "frozen_selftest", lambda *_args: {"ok": True})
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

    monkeypatch.setattr(
        builder,
        "subprocess",
        SimpleNamespace(run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0)),
    )
    assert builder.make_installer() != 0  # 上一次的安装包不能算作本次产物
    monkeypatch.setattr(builder, "_find_makensis", lambda: None)
    assert builder.make_installer() != 0


def test_nsis_default_matches_windows_download_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdfreader import __version__

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    names = importlib.import_module("package_names")
    installer = (root / "scripts" / "installer.nsi").read_text(encoding="utf-8-sig")
    assert f'!define VERSION "{__version__}"' in installer
    assert f'!define OUTFILE "dist\\{names.windows_installer_name("${VERSION}")}"' in installer


def test_unknown_mac_chip_is_not_mislabeled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    names = importlib.import_module("package_names")
    with pytest.raises(ValueError, match="不支持的 macOS 架构"):
        names.macos_archive_name("0.0.6", "unknown")


def test_package_selftest_requires_fresh_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    smoke = importlib.import_module("package_smoke")
    profile = tmp_path / "isolated"

    def run(args: list[str], *, env: dict[str, str], **_kwargs: object) -> None:
        assert args[-1] == "--selftest"
        assert env["LUMEN_PROFILE_DIR"] == str(profile)
        (profile / "selftest.json").write_text(
            json.dumps({"ok": True, "frozen": True, "version": "0.0.6"}), encoding="utf-8"
        )

    monkeypatch.setattr(smoke, "subprocess", SimpleNamespace(run=run))
    assert smoke.frozen_selftest(tmp_path / "PDFReader", profile, "0.0.6")["ok"]
    with pytest.raises(FileExistsError):
        smoke.frozen_selftest(tmp_path / "PDFReader", profile, "0.0.6")


def test_macos_verifier_rejects_wrong_arch_and_isolates_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import plistlib

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    verifier = importlib.import_module("verify_macos_package")
    monkeypatch.setattr(verifier.tempfile, "tempdir", str(tmp_path))
    (tmp_path / "dist").mkdir()
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "__version__", "0.0.6")
    monkeypatch.setattr(
        verifier,
        "platform",
        SimpleNamespace(system=lambda: "Darwin", machine=lambda: "arm64", mac_ver=lambda: ("15",)),
    )
    profiles: list[Path] = []
    architectures = ["x86_64"]

    def run(args: list[str], **_kwargs: object) -> None:
        if args[0] == "ditto":
            contents = Path(args[-1]) / "PDFReader.app/Contents"
            executable = contents / "MacOS/PDFReader"
            executable.parent.mkdir(parents=True)
            executable.touch(mode=0o755)
            (contents / "framework-link").touch()
            (contents / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleVersion": "0.0.6",
                        "CFBundleShortVersionString": "0.0.6",
                        "LSMinimumSystemVersion": "15.0",
                        "CFBundleExecutable": "PDFReader",
                    }
                )
            )

    def selftest(_executable: Path, profile: Path, _version: str) -> dict[str, bool]:
        profiles.append(profile)
        return {"ok": True}

    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path.name == "framework-link" or original(path)
    )
    monkeypatch.setattr(
        verifier,
        "subprocess",
        SimpleNamespace(run=run, check_output=lambda *a, **kw: architectures[0]),
    )
    monkeypatch.setattr(verifier, "frozen_selftest", selftest)
    monkeypatch.setattr(
        verifier, "check_gui_startup", lambda _exe, _root, profile: profiles.append(profile)
    )
    with pytest.raises(RuntimeError, match="架构"):
        verifier.main()
    assert profiles == []
    architectures[0] = "arm64"
    assert verifier.main() == 0
    assert profiles[0].parent == profiles[1].parent
    assert profiles[0] != profiles[1]


@pytest.mark.parametrize("architecture", ["AMD64", "x86_64"])
def test_windows_portable_is_distinct_from_installer(
    monkeypatch: pytest.MonkeyPatch, architecture: str
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    names = importlib.import_module("package_names")
    assert names.desktop_archive_name("0.0.6", "Windows", architecture) == (
        "Lumen-0.0.6-Windows-x64-Portable.zip"
    )
