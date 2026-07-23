"""验证 ``pdfreader`` 包可被正确导入，元数据符合预期。

这是阶段 0 的"冒烟测试"——确保 src 布局、安装、打包配置都正确。
"""

from __future__ import annotations

import importlib

import pdfreader


def test_package_importable() -> None:
    """包能被 import。"""
    assert importlib.import_module("pdfreader") is pdfreader


def test_version_is_string() -> None:
    """版本号存在且为字符串。"""
    assert isinstance(pdfreader.__version__, str)
    assert pdfreader.__version__  # 非空


def test_version_matches_expected() -> None:
    """当前版本为 0.0.1。"""
    assert pdfreader.__version__ == "0.0.1"


def test_license_declared() -> None:
    """许可证声明为 AGPL。"""
    assert "AGPL" in pdfreader.__license__


def test_public_api() -> None:
    """公开 API 只导出预期符号。"""
    assert set(pdfreader.__all__) == {"__version__", "__license__"}
