"""主题管理器：三模式（浅色 / 深色 / 跟随系统）。"""

from __future__ import annotations

from pdfreader.gui.theme.manager import ThemeManager


def test_light_and_dark_modes(qtbot) -> None:  # type: ignore[no-untyped-def]
    tm = ThemeManager("light")
    assert tm.mode == "light"
    assert tm.name == "light"
    assert tm.is_dark is False

    tm.set_mode("dark")
    assert tm.mode == "dark"
    assert tm.name == "dark"
    assert tm.is_dark is True


def test_system_mode_resolves_to_concrete_palette(qtbot) -> None:  # type: ignore[no-untyped-def]
    tm = ThemeManager("light")
    tm.set_mode("system")
    # 模式记为 system，但实际配色须解析为具体的 light/dark 之一
    assert tm.mode == "system"
    assert tm.name in ("light", "dark")
    assert tm.palette.name == tm.name


def test_invalid_mode_falls_back_to_light(qtbot) -> None:  # type: ignore[no-untyped-def]
    tm = ThemeManager("light")
    tm.set_mode("banana")
    assert tm.mode == "light"
    assert tm.name == "light"


def test_changed_emits_concrete_name(qtbot) -> None:  # type: ignore[no-untyped-def]
    tm = ThemeManager("light")
    seen: list[str] = []
    tm.changed.connect(seen.append)
    tm.set_mode("dark")
    assert seen and seen[-1] == "dark"


def test_toggle_switches_light_dark(qtbot) -> None:  # type: ignore[no-untyped-def]
    tm = ThemeManager("light")
    tm.toggle()
    assert tm.mode == "dark"
    tm.toggle()
    assert tm.mode == "light"
