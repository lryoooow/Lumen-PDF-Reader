"""命令扩展的最小公共契约；不自动发现或执行第三方代码。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Command:
    id: str
    title: str
    run: Callable[[], None]
    enabled: Callable[[], bool] = lambda: True


@dataclass(frozen=True)
class DocumentContext:
    path: str
    title: str
    page: int | None
    pages: int
    encrypted: bool


class CommandHost(Protocol):
    def register_command(self, command: Command) -> None: ...
    def document_context(self) -> DocumentContext | None: ...
