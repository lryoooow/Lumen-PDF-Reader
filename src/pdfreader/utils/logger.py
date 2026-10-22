"""日志配置。

使用 Python 标准库 logging,输出到文件(便于排查问题)。
日志位置:~/.pdfreader/pdfreader.log
"""

from __future__ import annotations

import faulthandler
import logging
from pathlib import Path
from typing import IO

__all__ = ["CRASH_LOG_FILE", "LOG_FILE", "enable_faulthandler", "setup_logging"]

LOG_FILE = Path.home() / ".pdfreader" / "pdfreader.log"
CRASH_LOG_FILE = Path.home() / ".pdfreader" / "crash.log"

_crash_handle: IO[bytes] | None = None


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


def enable_faulthandler() -> None:
    """把段错误 / 原生崩溃的调用栈写入 ``~/.pdfreader/crash.log``。

    Python 异常由 ``sys.excepthook`` 记录；这个钩子覆盖 Qt / PyMuPDF 里
    无法被 Python 捕获的硬崩溃。文件超过 1 MB 时在启动时轮转一份。
    """
    global _crash_handle
    if _crash_handle is not None:
        return
    try:
        CRASH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if CRASH_LOG_FILE.exists() and CRASH_LOG_FILE.stat().st_size > 1_000_000:
            backup = CRASH_LOG_FILE.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            CRASH_LOG_FILE.replace(backup)
        handle = CRASH_LOG_FILE.open("ab", buffering=0)
        faulthandler.enable(file=handle, all_threads=True)
        _crash_handle = handle
    except OSError:
        # 无法写崩溃日志不影响正常运行；标准错误仍可用
        logging.getLogger("pdfreader").debug("启用崩溃日志失败", exc_info=True)
