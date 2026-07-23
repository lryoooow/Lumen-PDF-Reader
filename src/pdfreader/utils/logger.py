"""日志配置。

使用 Python 标准库 logging,输出到文件(便于排查问题)。
日志位置:~/.pdfreader/pdfreader.log
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = ["LOG_FILE", "setup_logging"]

LOG_FILE = Path.home() / ".pdfreader" / "pdfreader.log"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置并返回应用 logger。

    日志写入 ~/.pdfreader/pdfreader.log,轮转保留最近 1MB × 3 份。
    """
    logger = logging.getLogger("pdfreader")
    if logger.handlers:  # 已配置,避免重复添加
        return logger

    logger.setLevel(level)
    logger.propagate = False

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        handler: logging.Handler = RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError:
        # 无法写日志文件时退化为 stderr
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    return logger
