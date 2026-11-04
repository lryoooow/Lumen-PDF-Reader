"""Lumen 用户数据路径，也用于隔离安装包验收。"""

import os
from pathlib import Path


def profile_override() -> Path | None:
    """返回显式指定的独立数据目录。"""
    value = os.environ.get("LUMEN_PROFILE_DIR")
    return Path(value).resolve() if value else None


def profile_directory() -> Path:
    """返回应用数据目录。"""
    return profile_override() or Path.home() / ".pdfreader"
