"""文献目录发现与文件二进制去重；不删除、移动或修改源文件。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


def discover_documents(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in (".pdf", ".epub")
        ),
        key=lambda path: path.name.casefold(),
    )


@dataclass(frozen=True)
class DuplicateScan:
    groups: tuple[tuple[Path, ...], ...]
    failed: tuple[Path, ...]
    cancelled: bool = False


def duplicate_files(paths: Sequence[Path], cancelled: Callable[[], bool]) -> DuplicateScan:
    sized: dict[int, list[Path]] = {}
    failed: list[Path] = []
    for path in dict.fromkeys(path.resolve() for path in paths):
        try:
            sized.setdefault(path.stat().st_size, []).append(path)
        except OSError:
            logging.getLogger("pdfreader").warning("去重检查无法访问：%s", path, exc_info=True)
            failed.append(path)
    groups: list[tuple[Path, ...]] = []
    for candidates in sized.values():
        if len(candidates) < 2:
            continue
        hashes: dict[str, list[Path]] = {}
        for path in candidates:
            if cancelled():
                return DuplicateScan(tuple(groups), tuple(failed), True)
            try:
                before = path.stat()
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        if cancelled():
                            return DuplicateScan(tuple(groups), tuple(failed), True)
                        digest.update(block)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise OSError("扫描期间文件发生变化")
                hashes.setdefault(digest.hexdigest(), []).append(path)
            except OSError:
                logging.getLogger("pdfreader").warning("去重读取失败：%s", path, exc_info=True)
                failed.append(path)
        groups.extend(tuple(group) for group in hashes.values() if len(group) > 1)
    return DuplicateScan(tuple(groups), tuple(failed))
