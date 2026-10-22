"""崩溃恢复快照：写入、扫描、坏清单容错、清理、备份与两种恢复路径。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pdfreader.services import recovery
from pdfreader.services.recovery import RecoveryError, RecoveryStore


def _store(tmp_path: Path) -> RecoveryStore:
    return RecoveryStore(tmp_path / "recovery")


def _write(
    store: RecoveryStore, original: Path, payload: bytes = b"snapshot"
) -> recovery.RecoveryEntry:
    def writer(target: Path) -> bool:
        target.write_bytes(payload)
        return True

    return store.write_snapshot(
        original,
        page_count=3,
        title="论文",
        encrypted=False,
        writer=writer,
    )


def test_write_and_scan(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)

    entry = _write(store, original)

    assert entry.snapshot.read_bytes() == b"snapshot"
    assert entry.manifest.is_file()
    assert store.entries() == [entry]
    assert entry.original_unchanged
    assert entry.title == "论文"
    assert entry.page_count == 3


def test_writer_failure_leaves_nothing(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)

    def writer(target: Path) -> bool:
        target.write_bytes(b"partial")
        return False

    with pytest.raises(RecoveryError):
        store.write_snapshot(original, page_count=1, title="", encrypted=False, writer=writer)

    assert store.entries() == []
    assert list(store.directory.glob("*.pdf*")) == []


def test_writer_exception_cleans_temp_file(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)

    def writer(target: Path) -> bool:
        target.write_bytes(b"partial")
        raise OSError("磁盘满了")

    with pytest.raises(OSError, match="磁盘满了"):
        store.write_snapshot(original, page_count=1, title="", encrypted=False, writer=writer)

    assert list(store.directory.glob("*.pdf*")) == []


def test_manifest_failure_keeps_snapshot(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)
    _write(store, original, b"old")

    def boom(entry: recovery.RecoveryEntry) -> None:
        raise OSError("清单写不进去")

    monkeypatch.setattr(store, "_write_manifest", boom)
    with pytest.raises(OSError, match="清单写不进去"):
        _write(store, original, b"new")

    entries = store.entries()
    assert len(entries) == 1
    assert entries[0].snapshot.read_bytes() == b"new"


def test_broken_manifests_are_skipped(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)
    entry = _write(store, original)

    bad_json = store.directory / "bad.json"
    bad_json.write_text("not json", encoding="utf-8")
    wrong_key = store.directory / "wrong.json"
    wrong_key.write_text(json.dumps({"version": 1, "key": "other", "original": str(original)}))
    missing_snapshot = store.directory / "missing.json"
    missing_snapshot.write_text(
        json.dumps({"version": 1, "key": "missing", "original": str(original)})
    )
    # 路径与 key 不匹配（防止清单被改成别的文件）
    mismatch = store.directory / "mismatch.json"
    mismatch.write_text(json.dumps({"version": 1, "key": "mismatch", "original": str(original)}))

    assert store.entries() == [entry]


def test_remove_for_and_replacement(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)
    _write(store, original)

    assert store.remove_for(original) is True
    assert store.remove_for(original) is False
    assert store.entries() == []


def test_original_unchanged_detects_edit(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)
    entry = _write(store, original)
    assert entry.original_unchanged

    original.write_bytes(b"edited after crash")

    assert not entry.original_unchanged
    assert entry.original_exists


def test_replace_original_backs_up_and_removes_entry(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)
    entry = _write(store, original, b"restored")

    backup = store.replace_original(entry)

    assert original.read_bytes() == b"restored"
    assert backup.read_bytes() == b"original"
    assert store.entries() == []


def test_copy_aside_keeps_original(tmp_path: Path) -> None:
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    store = _store(tmp_path)
    entry = _write(store, original, b"restored")
    original.write_bytes(b"edited after crash")

    copy = store.copy_aside(entry)

    assert copy != original
    assert copy.read_bytes() == b"restored"
    assert original.read_bytes() == b"edited after crash"
    assert store.entries() == []


def test_prune_by_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(recovery, "MAX_SNAPSHOTS", 1)
    for index in range(3):
        original = tmp_path / f"paper{index}.pdf"
        original.write_bytes(b"x")
        _write(store, original)

    assert len(store.entries()) == 1


def test_prune_by_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(recovery, "MAX_AGE_SECONDS", 0)
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"x")
    _write(store, original)

    assert store.entries() == []


def test_session_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    first = _store(tmp_path)
    assert first.acquire_session() is True
    assert first.session_owned
    first.release_session()
    assert not first.session_owned

    second = _store(tmp_path)
    assert second.acquire_session() is True
    second.release_session()


def test_session_lock_excludes_another_instance(tmp_path: Path) -> None:
    first = _store(tmp_path)
    assert first.acquire_session() is True
    second = _store(tmp_path)
    try:
        assert second.acquire_session() is False
    finally:
        first.release_session()
    assert second.acquire_session() is True
    second.release_session()


@pytest.mark.skipif(os.name != "nt", reason="Windows 下才验证锁文件字节")
def test_session_lock_keeps_lock_file_nonempty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.acquire_session()
    try:
        assert (store.directory / ".session.lock").stat().st_size >= 1
    finally:
        store.release_session()
