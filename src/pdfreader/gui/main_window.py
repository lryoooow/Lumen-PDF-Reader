"""主窗口。

阶段 0 仅提供一个空骨架窗口，后续阶段逐步填充菜单、工具栏、PDF 视图等内容。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pdfreader import __version__

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """应用主窗口。

    阶段 0：展示占位内容、基础菜单（文件/退出）、状态栏。
    阶段 3 起将加入 PDF 视图、打开文件逻辑。
    """

    def __init__(self) -> None:
        super().__init__()
        self._setup_window()
        self._setup_central_widget()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()

    # ------------------------------------------------------------------
    # 初始化各部分
    # ------------------------------------------------------------------
    def _setup_window(self) -> None:
        self.setWindowTitle(self.tr("PDF Reader"))
        self.resize(1024, 768)
        self.setMinimumSize(640, 480)

    def _setup_central_widget(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        placeholder = QLabel(self.tr("PDF Reader 就绪 · 等待打开文件"), central)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("font-size: 16pt; color: gray;")
        layout.addWidget(placeholder)

        version_label = QLabel(f"v{__version__}", central)
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("font-size: 9pt; color: lightgray;")
        layout.addWidget(version_label)

        self.setCentralWidget(central)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu(self.tr("文件(&F)"))

        self.open_action = QAction(self.tr("打开(&O)..."), self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        # 阶段 3 实现真正的打开逻辑；阶段 0 先占位
        self.open_action.setEnabled(False)
        file_menu.addAction(self.open_action)

        file_menu.addSeparator()

        exit_action = QAction(self.tr("退出(&Q)"), self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu(self.tr("帮助(&H)"))
        about_action = QAction(self.tr("关于(&A)"), self)
        # 阶段 10 实现关于对话框
        about_action.setEnabled(False)
        help_menu.addAction(about_action)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar(self.tr("主工具栏"), self)
        toolbar.setMovable(False)
        toolbar.addAction(self.open_action)
        self.addToolBar(toolbar)

    def _setup_statusbar(self) -> None:
        status = QStatusBar(self)
        status.showMessage(self.tr("就绪"))
        self.setStatusBar(status)
