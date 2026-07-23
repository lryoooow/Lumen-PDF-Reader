"""设置持久化测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.config.settings import Settings, SettingsStore


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.default_zoom == 1.5
        assert s.recent_files == []
        assert s.window_maximized is False

    def test_add_recent_file_new(self) -> None:
        s = Settings()
        s.add_recent_file("/a.pdf")
        s.add_recent_file("/b.pdf")
        assert s.recent_files == ["/b.pdf", "/a.pdf"]

    def test_add_recent_file_dedup(self) -> None:
        s = Settings()
        s.add_recent_file("/a.pdf")
        s.add_recent_file("/b.pdf")
        s.add_recent_file("/a.pdf")  # 重复,应置顶
        assert s.recent_files == ["/a.pdf", "/b.pdf"]

    def test_add_recent_file_max_count(self) -> None:
        s = Settings()
        for i in range(15):
            s.add_recent_file(f"/{i}.pdf")
        assert len(s.recent_files) == 10
        assert s.recent_files[0] == "/14.pdf"


class TestSettingsStore:
    def test_load_missing_returns_default(self, store: SettingsStore) -> None:
        s = store.load()
        assert isinstance(s, Settings)
        assert s.default_zoom == 1.5

    def test_save_and_load(self, store: SettingsStore) -> None:
        s = Settings(default_zoom=2.0, recent_files=["/x.pdf"])
        store.save(s)
        assert store.path.exists()

        loaded = store.load()
        assert loaded.default_zoom == 2.0
        assert loaded.recent_files == ["/x.pdf"]

    def test_load_corrupt_returns_default(self, store: SettingsStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{invalid json", encoding="utf-8")
        s = store.load()
        assert s.default_zoom == 1.5  # 回退默认

    def test_load_partial_keeps_known_keys(self, store: SettingsStore) -> None:
        """未知键被忽略,已知键保留。"""
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text('{"default_zoom": 3.0, "unknown_key": "x"}', encoding="utf-8")
        s = store.load()
        assert s.default_zoom == 3.0

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "subdir" / "nested" / "s.json")
        store.save(Settings())
        assert store.path.exists()
