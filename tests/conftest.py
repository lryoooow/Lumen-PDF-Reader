"""pytest 全局 fixture 与配置。"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest

# 让 GUI 测试在无显示器的 CI 环境也能跑（使用离屏平台）
# 在有真实显示的本地开发环境不影响（只有 DISPLAY 不可用时才启用）
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qtbot(qtbot: pytest.fixture) -> pytest.fixture:  # type: ignore[type-arg]
    """提供 pytest-qt 的 qtbot，用于 GUI 测试。"""
    return qtbot


@pytest.fixture(autouse=True)
def _no_write_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """防止测试期间向磁盘写入用户配置。"""
    yield
