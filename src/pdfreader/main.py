"""应用入口。

负责 QApplication 的创建与配置、主窗口的构建与启动事件循环。
本模块是整个 GUI 应用的唯一起点，通过 ``python -m pdfreader`` 或
``pdfreader``（GUI 脚本入口）调用。
"""

from __future__ import annotations

import sys
from types import TracebackType

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow

from pdfreader import __version__
from pdfreader.gui.main_window import MainWindow

__all__ = ["create_application", "global_exception_handler", "main"]


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

    避免崩溃时静默退出：将异常打印到 stderr，保证可排查。
    后续阶段会替换为友好的错误对话框 + 日志落盘。
    """
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main() -> int:
    """启动 PDF Reader 应用，返回退出码。"""
    sys.excepthook = global_exception_handler
    app = create_application(sys.argv)

    window: QMainWindow = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
