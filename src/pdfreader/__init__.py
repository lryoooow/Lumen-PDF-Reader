"""Lumen —— 开源的 PDF 文献阅读与批注工具（Python 包名 ``pdfreader``）。

采用分层架构：GUI 表现层、控制器层、业务核心层、服务层、引擎适配层、后台线程。
核心引擎 PyMuPDF 的所有访问被隔离在 :mod:`pdfreader.engines` 中。架构说明见 ``docs/02_架构设计.md``。
"""

from __future__ import annotations

__version__ = "0.0.2"
__license__ = "AGPL-3.0-or-later"

__all__ = ["__license__", "__version__"]
