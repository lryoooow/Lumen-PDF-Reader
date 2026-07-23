"""应用入口。

负责 QApplication 的创建与配置、日志初始化、全局异常处理、
主窗口的构建与启动事件循环。通过 ``python -m pdfreader`` 或
``pdfreader``（GUI 脚本入口）调用。
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from types import TracebackType

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from pdfreader import __version__
from pdfreader.gui.main_window import MainWindow
from pdfreader.utils.logger import setup_logging

__all__ = ["create_application", "global_exception_handler", "main"]

_logger = setup_logging()


def create_application(argv: list[str]) -> QApplication:
    """创建并配置 :class:`QApplication`。

    设置高 DPI 支持与应用元数据（名称、版本）。
    """
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName("PDF Reader")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PDF Reader")
    return app


def global_exception_handler(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
) -> None:
    """全局未捕获异常钩子。

    记录到日志文件,并弹出友好错误对话框(而非静默崩溃)。
    """
    # 记录完整堆栈到日志
    _logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
    # 同时输出到 stderr(便于命令行排查)
    traceback.print_exception(exc_type, exc_value, exc_tb)

    # 弹出友好对话框(QApplication 可能已存在)
    app = QApplication.instance()
    if app is not None:
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        msg = f"{exc_type.__name__}: {exc_value}\n\n详情已记录到日志文件。"
        with contextlib.suppress(Exception):
            QMessageBox.critical(None, "程序出错", msg)


def main() -> int:
    """启动 PDF Reader 应用，返回退出码。"""
    _logger.info("PDF Reader %s 启动", __version__)
    sys.excepthook = global_exception_handler
    app = create_application(sys.argv)

    window: QMainWindow = MainWindow()
    window.show()

    _logger.info("进入事件循环")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
