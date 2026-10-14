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
        assert s.window_width == 1024
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
        assert s.window_width == 1024

    def test_save_and_load(self, store: SettingsStore) -> None:
        s = Settings(window_width=1500, recent_files=["/x.pdf"])
        store.save(s)
        assert store.path.exists()

        loaded = store.load()
        assert loaded.window_width == 1500
        assert loaded.recent_files == ["/x.pdf"]

    def test_load_corrupt_returns_default(self, store: SettingsStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{invalid json", encoding="utf-8")
        s = store.load()
        assert s.window_width == 1024  # 回退默认

    def test_load_partial_keeps_known_keys(self, store: SettingsStore) -> None:
        """未知键被忽略,已知键保留。"""
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text('{"window_width": 1300, "unknown_key": "x"}', encoding="utf-8")
        s = store.load()
        assert s.window_width == 1300

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "subdir" / "nested" / "s.json")
        store.save(Settings())
        assert store.path.exists()


class TestSettingsRobustness:
    def test_wrong_field_types_fall_back_to_defaults(self, store: SettingsStore) -> None:
        """手工改坏 / 旧版本留下的错误类型不能带进程序（如 recent_files: null 会让启动崩溃）。"""
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(
            '{"recent_files": null, "window_width": "wide", "window_maximized": 1,'
            ' "theme": "dark", "reading_positions": {"a.pdf": "x", "b.pdf": [3, 0.5, 2.0]}}',
            encoding="utf-8",
        )
        s = store.load()
        assert s.recent_files == []
        assert s.window_width == 1024
        assert s.window_maximized is False
        assert s.theme == "dark"
        assert s.position_for("a.pdf") is None
        pos = s.position_for("b.pdf")
        assert pos is not None and (pos.page, pos.offset, pos.zoom) == (3, 0.5, 2.0)

    def test_non_object_json_returns_defaults(self, store: SettingsStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("[1, 2, 3]", encoding="utf-8")
        assert store.load() == Settings()

    def test_save_is_atomic_and_leaves_no_temp(self, store: SettingsStore) -> None:
        store.save(Settings(window_width=1400))
        assert store.load().window_width == 1400
        assert not store.path.with_suffix(".json.tmp").exists()


class TestReadingPositions:
    def test_remember_and_recall(self) -> None:
        from pdfreader.config.settings import ReadingPosition

        s = Settings()
        s.remember_position("/a.pdf", ReadingPosition(7, 0.25, 1.8))
        pos = s.position_for("/a.pdf")
        assert pos == ReadingPosition(7, 0.25, 1.8)
        assert s.position_for("/missing.pdf") is None

    def test_capacity_evicts_least_recent(self) -> None:
        from pdfreader.config.settings import MAX_READING_POSITIONS, ReadingPosition

        s = Settings()
        for i in range(MAX_READING_POSITIONS + 5):
            s.remember_position(f"/{i}.pdf", ReadingPosition(i))
        assert len(s.reading_positions) == MAX_READING_POSITIONS
        assert s.position_for("/0.pdf") is None
        assert s.position_for(f"/{MAX_READING_POSITIONS + 4}.pdf") is not None

    def test_remove_recent_file(self) -> None:
        s = Settings()
        s.add_recent_file("/a.pdf")
        s.remove_recent_file("/a.pdf")
        s.remove_recent_file("/not-there.pdf")
        assert s.recent_files == []
