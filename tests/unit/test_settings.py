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


def test_bookmarks_round_trip_and_tolerate_bad_data(tmp_path: Path) -> None:
    import json

    from pdfreader.config.settings import Bookmark

    store = SettingsStore(tmp_path / "s.json")
    settings = Settings()
    settings.set_bookmarks("a.pdf", [Bookmark(3, 0.5, "结论"), Bookmark(0, 0.1, "引言")])
    store.save(settings)
    loaded = store.load()
    assert loaded.bookmarks_for("a.pdf") == [Bookmark(0, 0.1, "引言"), Bookmark(3, 0.5, "结论")]
    settings.set_bookmarks("a.pdf", [])  # 删光后不留空条目
    assert "a.pdf" not in settings.bookmarks
    (tmp_path / "s.json").write_text(
        json.dumps({"bookmarks": {"x.pdf": [[1, 0.2, "ok"], ["bad", 0, "x"], [1, 2]], "y": 5}}),
        encoding="utf-8",
    )
    assert store.load().bookmarks_for("x.pdf") == [Bookmark(1, 0.2, "ok")]
    assert store.load().bookmarks_for("y") == []


def test_favorites_toggle_and_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    settings = Settings()
    assert settings.toggle_favorite("a.pdf") is True
    assert settings.is_favorite("a.pdf")
    assert settings.toggle_favorite(Path("b.pdf")) is True
    assert settings.toggle_favorite("a.pdf") is False
    assert not settings.is_favorite("a.pdf")
    settings.library_view = "list"
    store.save(settings)
    loaded = store.load()
    assert loaded.favorites == ["b.pdf"]
    assert loaded.library_view == "list"


def test_doc_meta_remember_evict_and_tolerate_bad_data(tmp_path: Path) -> None:
    import json

    from pdfreader.config.settings import MAX_DOC_META, DocMeta

    settings = Settings()
    assert settings.meta_for("a.pdf") is None
    meta = DocMeta(pages=12, annotations=3, title="综述", author="Lin", opened_at=100.0)
    settings.remember_meta("a.pdf", meta)
    assert settings.meta_for("a.pdf") == meta
    for i in range(MAX_DOC_META + 5):
        settings.remember_meta(f"{i}.pdf", DocMeta(pages=i))
    assert len(settings.doc_meta) == MAX_DOC_META
    assert settings.meta_for("a.pdf") is None  # 最旧的被淘汰
    settings.remember_meta("0.pdf", DocMeta(pages=1))  # 已淘汰的重新记录后排在最新
    assert list(settings.doc_meta)[-1] == "0.pdf"

    store = SettingsStore(tmp_path / "s.json")
    (tmp_path / "s.json").write_text(
        json.dumps(
            {
                "library_view": "cards",
                "doc_meta": {
                    "x.pdf": {"pages": 5, "annotations": -2, "title": 7, "opened_at": 3},
                    "y.pdf": "bad",
                    "z.pdf": {"pages": True, "author": "A" * 400},
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = store.load()
    assert loaded.library_view == "grid"
    assert loaded.meta_for("x.pdf") == DocMeta(pages=5, annotations=0, opened_at=3.0)
    assert loaded.meta_for("y.pdf") is None
    z = loaded.meta_for("z.pdf")
    assert z is not None and z.pages == 0 and len(z.author) == 300
    (tmp_path / "s.json").write_text(json.dumps({"doc_meta": [1, 2]}), encoding="utf-8")
    assert store.load().doc_meta == {}
