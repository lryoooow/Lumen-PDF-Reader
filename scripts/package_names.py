"""分发文件名使用用户可识别的系统与芯片名称。"""

from __future__ import annotations


def windows_installer_name(version: str) -> str:
    """返回 Windows x64 安装程序的公开文件名。"""
    return f"Lumen-{version}-Windows-x64-Setup.exe"


def macos_archive_name(version: str, architecture: str) -> str:
    """返回对应 Mac 芯片的归档名，拒绝将未知架构标成可用包。"""
    chips = {"arm64": "Apple-Silicon", "x86_64": "Intel"}
    try:
        chip = chips[architecture]
    except KeyError as exc:
        raise ValueError(f"不支持的 macOS 架构：{architecture}") from exc
    return f"Lumen-{version}-macOS-{chip}.zip"


def desktop_archive_name(version: str, target: str, architecture: str) -> str:
    """返回原生桌面目录包的公开归档名。"""
    if target == "Darwin":
        return macos_archive_name(version, architecture)
    if target == "Windows":
        architecture = {"AMD64": "x64", "x86_64": "x64"}.get(architecture, architecture)
        return f"Lumen-{version}-Windows-{architecture}-Portable.zip"
    return f"Lumen-{version}-{target}-{architecture}.zip"
