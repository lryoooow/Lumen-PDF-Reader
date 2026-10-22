"""诊断包：内容齐全、隐私字段被剔除、密钥被替换。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from pdfreader.services.diagnostics import collect_diagnostics, redact_text, sanitize_settings


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_redact_text_hides_home_and_tokens() -> None:
    home = Path("C:/Users/Alice")
    text = f"打开 {home / 'paper.pdf'}，key=sk-1234567890abcdef，Authorization: Bearer abcdefghijkl"
    result = redact_text(text, home)
    assert str(home) not in result
    assert "~" in result
    assert "paper.pdf" in result
    assert "sk-1234567890abcdef" not in result
    assert "Bearer abcdefghijkl" not in result


def test_sanitize_settings_drops_path_fields() -> None:
    data = {
        "theme": "dark",
        "recent_files": ["C:/Users/Alice/a.pdf"],
        "reading_positions": {"x": [1, 0.0, 1.0]},
        "bookmarks": {},
        "favorites": [],
        "doc_meta": {},
        "session_files": ["C:/Users/Alice/a.pdf"],
        "session_active": "C:/Users/Alice/a.pdf",
        "zotero_data_dir": "C:/Users/Alice/Zotero",
        "sidebar_width": 280,
    }
    clean = sanitize_settings(data)
    assert clean == {"theme": "dark", "sidebar_width": 280}


def test_diagnostics_zip_contains_redacted_environment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = _write(
        home / ".pdfreader" / "settings.json",
        json.dumps(
            {
                "theme": "sepia",
                "recent_files": [str(home / "secret" / "paper.pdf")],
                "session_files": [str(home / "secret" / "paper.pdf")],
                "sidebar_width": 300,
            },
            ensure_ascii=False,
        ),
    )
    ai = _write(
        home / ".pdfreader" / "ai.json",
        json.dumps(
            {
                "base_url": "https://api.example.com/v1",
                "model": "demo",
                "api_key": "sk-should-not-leak",
                "extra": {"token": "nope"},
            }
        ),
    )
    log = _write(
        home / ".pdfreader" / "pdfreader.log",
        f"2026-01-01 打开 {home / 'secret' / 'paper.pdf'} key=sk-1234567890abcdef\n",
    )
    crash = _write(home / ".pdfreader" / "crash.log", "Fatal Python error: Segmentation fault\n")
    recovery = home / ".pdfreader" / "recovery"
    _write(recovery / "a.json", "{}")
    _write(recovery / "b.json", "{}")
    selftest = _write(home / ".pdfreader" / "selftest.json", '{"ok": true}')

    destination = tmp_path / "out" / "diag.zip"
    written = collect_diagnostics(
        destination,
        home=home,
        settings_path=settings,
        ai_settings_path=ai,
        log_file=log,
        crash_log=crash,
        recovery_dir=recovery,
        selftest_path=selftest,
    )
    assert written == destination
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        assert {
            "environment.json",
            "settings.json",
            "ai.json",
            "logs/pdfreader.log",
            "logs/crash.log",
            "recovery.json",
            "selftest.json",
            "README.txt",
        } <= names
        clean_settings = json.loads(archive.read("settings.json"))
        assert clean_settings == {"theme": "sepia", "sidebar_width": 300}
        ai_text = archive.read("ai.json").decode()
        assert "api_key" not in ai_text
        assert "token" not in ai_text
        assert "https://api.example.com/v1" in ai_text
        log_text = archive.read("logs/pdfreader.log").decode()
        assert "sk-1234567890abcdef" not in log_text
        assert str(home) not in log_text
        assert json.loads(archive.read("recovery.json")) == {"snapshots": 2}
        assert "Segmentation fault" in archive.read("logs/crash.log").decode()


def test_missing_files_are_skipped(tmp_path: Path) -> None:
    destination = tmp_path / "diag.zip"
    collect_diagnostics(
        destination,
        home=tmp_path,
        settings_path=tmp_path / "nope.json",
        ai_settings_path=tmp_path / "nope-ai.json",
        log_file=tmp_path / "nope.log",
        crash_log=tmp_path / "nope-crash.log",
        recovery_dir=tmp_path / "nope-recovery",
        selftest_path=tmp_path / "nope-selftest.json",
    )
    with zipfile.ZipFile(destination) as archive:
        assert "environment.json" in archive.namelist()
        assert "settings.json" not in archive.namelist()
        assert "logs/pdfreader.log" not in archive.namelist()
