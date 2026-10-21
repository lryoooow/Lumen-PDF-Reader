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
def _no_write_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """测试期间把用户设置重定向到临时目录。

    旧实现只是空 ``yield``：主窗口测试打开样例文件时会把它们写进开发者真实的
    ``~/.pdfreader/settings.json``（最近文件 / 阅读位置），还会读入开发者的设置影响结果。
    """
    from pdfreader.config import settings as settings_module
    from pdfreader.services import materials as materials_module

    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    monkeypatch.setattr(settings_module, "default_settings_path", lambda: path)
    # 写作素材库同理：不碰开发者真实的 ~/.pdfreader/materials.db
    monkeypatch.setattr(materials_module, "default_materials_path", lambda: folder / "m.db")
    yield


@pytest.fixture(autouse=True)
def _no_motion() -> Iterator[None]:
    """测试默认关闭界面动效：断言针对最终状态，不依赖动画时序（动效专项测试会自行打开）。"""
    from pdfreader.gui.widgets import motion

    motion.set_enabled(False)
    yield
    motion.set_enabled(False)


class _MemoryKeyring:
    """内存版凭据后端：测试绝不读写开发者真实的系统凭据管理器。"""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.data.pop((service, username), None)


@pytest.fixture(autouse=True)
def memory_keyring() -> Iterator[_MemoryKeyring]:
    """把 API Key 存取换成内存后端（测试可直接检查 ``.data``）。"""
    from pdfreader.services.ai import keystore

    backend = _MemoryKeyring()
    keystore.set_default_backend(backend)
    yield backend
    keystore.set_default_backend(None)
