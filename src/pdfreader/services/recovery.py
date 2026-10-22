"""崩溃恢复快照：把含未保存改动的文档整份保存到用户目录。

为什么是整份 PDF 而不是「批注 JSON 日志」：

* 用户可能做签名、插入图片 / 文字、旋转 / 删除页面等任意编辑，只有整份写出才能
  忠实还原；也直接复用了引擎里已受测的保存与加密逻辑（``save_copy``）。
* 快照目录与临时目录分开：``~/.pdfreader/recovery`` 里的文件要跨进程、跨重启保留，
  不能被系统清理临时目录时删掉。

每个文档对应两个文件：``<key>.pdf``（快照）与 ``<key>.json``（清单）。``key`` 是
规范化绝对路径的哈希，清单里的 ``original`` 必须与 key 对得上，避免误写别的文件。
写入顺序是先快照后清单，扫描时只认「清单 + 快照都在」的条目，半成品自然被忽略。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from uuid import uuid4

_logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
MAX_SNAPSHOTS = 20
MAX_AGE_SECONDS = 14 * 24 * 60 * 60
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_BACKUPS = 5


class RecoveryError(RuntimeError):
    """恢复快照无法读取、写入或写回原文件。"""


def default_recovery_dir() -> Path:
    """默认的恢复快照目录（用户目录，不是临时目录）。"""
    return Path.home() / ".pdfreader" / "recovery"


def _key_for(path: Path) -> str:
    normalized = os.path.normcase(str(path.expanduser().resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    """一份恢复快照及其清单信息。"""

    key: str
    snapshot: Path
    manifest: Path
    original: Path
    title: str
    created_at: float
    page_count: int
    encrypted: bool
    original_size: int
    original_mtime_ns: int

    @property
    def original_exists(self) -> bool:
        return self.original.is_file()

    @property
    def original_unchanged(self) -> bool:
        """原文件是否仍是快照生成时的样子。

        取不到文件属性、或清单里没记下大小 / 时间（生成快照时文件已不可读）时
        一律按「已变化」处理——宁可另存恢复副本，也不覆盖用户后来改过的文件。
        """
        if self.original_size <= 0 or self.original_mtime_ns <= 0:
            return False
        try:
            stat = self.original.stat()
        except OSError:
            return False
        return stat.st_size == self.original_size and stat.st_mtime_ns == self.original_mtime_ns

    @property
    def created_label(self) -> str:
        return time.strftime("%m-%d %H:%M", time.localtime(self.created_at))


class RecoveryStore:
    """恢复快照目录的读写与清理（纯 Python，不依赖 Qt）。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = (directory or default_recovery_dir()).expanduser()
        self._session_file: IO[bytes] | None = None

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def session_owned(self) -> bool:
        """本进程是否持有会话锁（另一实例正在运行时为 False）。"""
        return self._session_file is not None

    # ------------------------------------------------------------------ 会话锁
    def acquire_session(self) -> bool:
        """尝试取得会话锁；已有另一个实例持锁时返回 False。

        锁只用于判断「有没有活着的实例正在使用这些快照」：崩溃后进程退出，
        操作系统会自动释放锁，下次启动就能看到上次留下的快照。
        """
        if self._session_file is not None:
            return True
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            handle = (self._directory / ".session.lock").open("a+b")
        except OSError:
            _logger.warning("无法创建恢复目录 %s", self._directory, exc_info=True)
            return False
        if not _lock(handle):
            with contextlib.suppress(OSError):
                handle.close()
            return False
        self._session_file = handle
        return True

    def release_session(self) -> None:
        handle = self._session_file
        if handle is None:
            return
        self._session_file = None
        with contextlib.suppress(OSError):
            _unlock(handle)
        with contextlib.suppress(OSError):
            handle.close()

    # ------------------------------------------------------------------ 写入
    def write_snapshot(
        self,
        original: Path,
        *,
        page_count: int,
        title: str,
        encrypted: bool,
        writer: Callable[[Path], bool],
    ) -> RecoveryEntry:
        """让 ``writer`` 把当前内存文档写到临时文件，再原子发布为快照并写清单。

        ``writer`` 返回 False 视为失败（调用方应从控制器取原因）；异常原样上抛，
        这样界面可以把真实原因显示出来。
        """
        original = original.expanduser().resolve()
        key = _key_for(original)
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RecoveryError(f"无法创建恢复目录：{exc}") from exc

        tmp = self._directory / f"{key}.pdf.tmp"
        target = self._directory / f"{key}.pdf"
        try:
            if not writer(tmp):
                raise RecoveryError("写入恢复快照失败")
            tmp.replace(target)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

        try:
            stat = original.stat()
            size, mtime = stat.st_size, stat.st_mtime_ns
        except OSError:
            size, mtime = 0, 0
        entry = RecoveryEntry(
            key=key,
            snapshot=target,
            manifest=self._directory / f"{key}.json",
            original=original,
            title=title.strip() or original.name,
            created_at=time.time(),
            page_count=max(page_count, 0),
            encrypted=encrypted,
            original_size=size,
            original_mtime_ns=mtime,
        )
        try:
            self._write_manifest(entry)
        except OSError:
            # 保留快照本身：清单写失败时宁可留下一个扫描不到的孤儿文件，
            # 也不能把上一份仍然有效的快照删掉（那才是真的丢数据）。
            _logger.warning("写入恢复清单失败：%s", entry.manifest, exc_info=True)
            raise
        self.prune()
        return entry

    def _write_manifest(self, entry: RecoveryEntry) -> None:
        payload = {
            "version": MANIFEST_VERSION,
            "key": entry.key,
            "original": str(entry.original),
            "title": entry.title,
            "created_at": entry.created_at,
            "page_count": entry.page_count,
            "encrypted": entry.encrypted,
            "original_size": entry.original_size,
            "original_mtime_ns": entry.original_mtime_ns,
        }
        tmp = entry.manifest.with_name(entry.manifest.name + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(entry.manifest)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    # ------------------------------------------------------------------ 读取
    def entries(self) -> list[RecoveryEntry]:
        """扫描全部有效快照（最新的在前）。坏清单跳过并记日志，不影响其它条目。"""
        if not self._directory.is_dir():
            return []
        found: list[RecoveryEntry] = []
        for manifest in sorted(self._directory.glob("*.json")):
            entry = self._load(manifest)
            if entry is not None:
                found.append(entry)
        found.sort(key=lambda item: item.created_at, reverse=True)
        return found

    def _load(self, manifest: Path) -> RecoveryEntry | None:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _logger.warning("跳过无法读取的恢复清单：%s", manifest, exc_info=True)
            return None
        if not isinstance(data, dict):
            _logger.warning("跳过格式错误的恢复清单：%s", manifest)
            return None
        raw: dict[str, object] = {str(key): value for key, value in data.items()}
        if raw.get("version") != MANIFEST_VERSION:
            return None
        key = manifest.stem
        if raw.get("key") != key:
            return None
        original_raw = raw.get("original")
        if not isinstance(original_raw, str):
            return None
        original = Path(original_raw)
        if not original.is_absolute() or original.suffix.lower() != ".pdf":
            return None
        if _key_for(original) != key:
            _logger.warning("恢复清单与原文件路径不匹配：%s", manifest)
            return None
        snapshot = self._directory / f"{key}.pdf"
        if not snapshot.is_file():
            return None
        title_raw = raw.get("title")
        title = title_raw if isinstance(title_raw, str) and title_raw else original.name
        created_raw = raw.get("created_at")
        created_at = float(created_raw) if isinstance(created_raw, (int, float)) else 0.0
        pages_raw = raw.get("page_count")
        page_count = int(pages_raw) if isinstance(pages_raw, int) else 0
        size_raw = raw.get("original_size")
        original_size = int(size_raw) if isinstance(size_raw, int) else 0
        mtime_raw = raw.get("original_mtime_ns")
        original_mtime_ns = int(mtime_raw) if isinstance(mtime_raw, int) else 0
        return RecoveryEntry(
            key=key,
            snapshot=snapshot,
            manifest=manifest,
            original=original,
            title=title,
            created_at=created_at,
            page_count=page_count,
            encrypted=bool(raw.get("encrypted")),
            original_size=original_size,
            original_mtime_ns=original_mtime_ns,
        )

    # ------------------------------------------------------------------ 删除 / 清理
    def remove(self, entry: RecoveryEntry) -> None:
        """删除一条快照；文件已不存在也视为成功。"""
        for path in (entry.snapshot, entry.manifest):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                _logger.warning("删除恢复快照失败：%s", path, exc_info=True)

    def remove_for(self, original: Path) -> bool:
        """删除某个原文件对应的快照（没有则返回 False）。"""
        key = _key_for(original)
        removed = False
        for path in (self._directory / f"{key}.pdf", self._directory / f"{key}.json"):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                _logger.warning("删除恢复快照失败：%s", path, exc_info=True)
                continue
            removed = True
        return removed

    def prune(self) -> None:
        """按数量、年龄、总大小清理旧快照，并只保留最近几份原文件备份。"""
        now = time.time()
        kept: list[RecoveryEntry] = []
        total = 0
        for entry in self.entries():  # 新的在前
            try:
                size = entry.snapshot.stat().st_size
            except OSError:
                continue
            too_old = now - entry.created_at > MAX_AGE_SECONDS
            too_many = len(kept) >= MAX_SNAPSHOTS
            too_big = bool(kept) and total + size > MAX_TOTAL_BYTES
            if too_old or too_many or too_big:
                self.remove(entry)
                continue
            kept.append(entry)
            total += size
        self._prune_backups()

    def _prune_backups(self) -> None:
        folder = self._directory / "backups"
        if not folder.is_dir():
            return
        files = sorted(
            (path for path in folder.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in files[MAX_BACKUPS:]:
            with contextlib.suppress(OSError):
                old.unlink()

    # ------------------------------------------------------------------ 恢复
    def backup_original(self, entry: RecoveryEntry) -> Path:
        """把原文件复制到 ``backups/``，返回备份路径。"""
        if not entry.original.is_file():
            raise RecoveryError("原文件已不存在，无法备份")
        folder = self._directory / "backups"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            target = folder / f"{entry.key}-{stamp}{entry.original.suffix}"
            counter = 2
            while target.exists():
                target = folder / f"{entry.key}-{stamp}-{counter}{entry.original.suffix}"
                counter += 1
            shutil.copy2(entry.original, target)
        except OSError as exc:
            raise RecoveryError(f"备份原文件失败：{exc}") from exc
        self._prune_backups()
        return target

    def replace_original(self, entry: RecoveryEntry) -> Path:
        """备份原文件后，用快照原子替换它；返回备份路径。"""
        backup = self.backup_original(entry)
        tmp = entry.original.with_name(f".{entry.original.name}.{uuid4().hex[:8]}.lumen-tmp")
        try:
            shutil.copy2(entry.snapshot, tmp)
            tmp.replace(entry.original)
        except OSError as exc:
            raise RecoveryError(f"写回原文件失败：{exc}") from exc
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        self.remove(entry)
        return backup

    def copy_aside(self, entry: RecoveryEntry) -> Path:
        """把快照另存为原文件旁边的「恢复副本」，不动原文件；返回新路径。"""
        folder = entry.original.parent if entry.original.parent.is_dir() else self._directory
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(entry.created_at))
        base = f"{entry.original.stem} (恢复 {stamp})"
        target = folder / f"{base}{entry.original.suffix}"
        counter = 2
        while target.exists():
            target = folder / f"{base} ({counter}){entry.original.suffix}"
            counter += 1
        try:
            shutil.copy2(entry.snapshot, target)
        except OSError as exc:
            raise RecoveryError(f"生成恢复副本失败：{exc}") from exc
        self.remove(entry)
        return target


def _lock(handle: IO[bytes]) -> bool:
    """对文件加非阻塞独占锁；已被别的实例锁住时返回 False。"""
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
