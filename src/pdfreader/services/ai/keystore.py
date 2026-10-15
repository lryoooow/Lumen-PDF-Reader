"""API Key 存取：交给系统凭据管理器（Windows 凭据管理器 / macOS 钥匙串 / Secret Service）。

Key 从不写入 ``ai.json``、不进日志。条目为 ``(SERVICE, provider.id)``。
凭据后端不可用（如精简的 Linux 环境）时抛 :class:`KeyStoreError`，由界面提示用户。
"""

from __future__ import annotations

import contextlib
from typing import Any, Protocol

__all__ = ["SERVICE", "KeyStore", "KeyStoreError", "default_keystore", "set_default_backend"]

SERVICE = "Lumen"


class KeyStoreError(Exception):
    """凭据管理器不可用或读写失败。"""


class _Backend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class _KeyringBackend:
    """默认后端：``keyring`` 模块（首次使用时才导入，加速启动）。"""

    def _module(self) -> Any:
        try:
            import keyring
        except Exception as exc:  # pragma: no cover - 打包缺失时才会发生
            raise KeyStoreError(f"系统凭据管理器不可用：{exc}") from exc
        return keyring

    def get_password(self, service: str, username: str) -> str | None:
        value = self._module().get_password(service, username)
        return value if isinstance(value, str) else None

    def set_password(self, service: str, username: str, password: str) -> None:
        self._module().set_password(service, username, password)

    def delete_password(self, service: str, username: str) -> None:
        self._module().delete_password(service, username)


class KeyStore:
    """按 provider id 读写 API Key。"""

    def __init__(self, backend: _Backend | None = None) -> None:
        self._backend: _Backend = backend or _KeyringBackend()

    def get(self, provider_id: str) -> str:
        """读取 Key；没有时返回空串。"""
        try:
            return self._backend.get_password(SERVICE, provider_id) or ""
        except KeyStoreError:
            raise
        except Exception as exc:
            raise KeyStoreError(f"读取 API Key 失败：{type(exc).__name__}") from exc

    def set(self, provider_id: str, key: str) -> None:
        """保存 Key；空串表示删除。"""
        if not key:
            self.delete(provider_id)
            return
        try:
            self._backend.set_password(SERVICE, provider_id, key)
        except KeyStoreError:
            raise
        except Exception as exc:
            raise KeyStoreError(f"保存 API Key 失败：{type(exc).__name__}") from exc

    def delete(self, provider_id: str) -> None:
        """删除 Key（不存在时忽略）。"""
        with contextlib.suppress(Exception):
            self._backend.delete_password(SERVICE, provider_id)


_default: KeyStore | None = None


def default_keystore() -> KeyStore:
    """全局凭据存取对象（测试可用 :func:`set_default_backend` 换成内存后端）。"""
    global _default
    if _default is None:
        _default = KeyStore()
    return _default


def set_default_backend(backend: _Backend | None) -> None:
    """替换全局后端（``None`` 恢复系统凭据管理器）。"""
    global _default
    _default = KeyStore(backend)
