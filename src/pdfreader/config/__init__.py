"""用户设置的持久化（~/.pdfreader/settings.json）。"""

from __future__ import annotations

from pdfreader.config.settings import Settings, SettingsStore, default_settings_path

__all__ = ["Settings", "SettingsStore", "default_settings_path"]
