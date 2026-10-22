"""应用入口。

负责 QApplication 的创建与配置、日志初始化、全局异常处理、
主窗口的构建与启动事件循环。通过 ``python -m pdfreader`` 或
``pdfreader``（GUI 脚本入口）调用。
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfreader import __version__
from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.single_instance import SingleInstance
from pdfreader.utils.logger import enable_faulthandler, setup_logging

__all__ = [
    "create_application",
    "file_from_argv",
    "global_exception_handler",
    "main",
]

_logger = setup_logging()


def _set_windows_app_id() -> None:
    """设置 Windows AppUserModelID，让任务栏使用应用自身图标而非 python 图标。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFReader.App")
    except Exception:  # 仅影响任务栏图标，不影响功能
        _logger.debug("设置 AppUserModelID 失败", exc_info=True)


def create_application(argv: list[str]) -> QApplication:
    """创建并配置 :class:`QApplication`。

    设置高 DPI 支持、应用元数据、主题与窗口图标。
    """
    _set_windows_app_id()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName("PDF Reader")
    app.setApplicationDisplayName("Lumen")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PDF Reader")

    # 应用主题（Fusion 基底 + 全局样式表）与应用图标
    from pdfreader.gui.theme import get_theme

    theme = get_theme()
    theme.apply(app)
    app.setWindowIcon(theme.app_icon())
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
        msg = f"{exc_type.__name__}: {exc_value}\n\n详情已记录到日志文件。"
        with contextlib.suppress(Exception):
            QMessageBox.critical(None, "程序出错", msg)


def run_selftest() -> int:
    """打包自检：验证关键运行时子系统在（尤其是冻结后的）环境中可用。

    用 ``PDFReader.exe --selftest`` 触发。校验 OCR 模型加载、格式转换依赖、
    PDF 引擎；结果写入 ``~/.pdfreader/selftest.json`` 并打印，返回 0/1。
    """
    import json

    result: dict[str, object] = {
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "ok": True,
        "checks": {},
    }
    checks: dict[str, str] = result["checks"]  # type: ignore[assignment]

    def check(name: str, fn) -> None:  # type: ignore[no-untyped-def]
        try:
            fn()
            checks[name] = "ok"
        except Exception as exc:
            checks[name] = f"FAIL: {exc}"
            result["ok"] = False

    def _ocr() -> None:
        from pdfreader.services.ocr import OCR_AVAILABLE, OcrService

        if not OCR_AVAILABLE:
            raise RuntimeError("rapidocr-onnxruntime 不可用")
        OcrService()._ensure_engine()  # 加载 onnx 模型（缺模型/DLL 会抛错）

    def _convert() -> None:
        import openpyxl  # noqa: F401
        import pdf2docx  # noqa: F401
        import pdfplumber  # noqa: F401

        from pdfreader.services.converter import FORMATS  # noqa: F401

    def _pdf() -> None:
        import pymupdf  # noqa: F401

    def _ai() -> None:
        import ssl

        import httpx
        import keyring

        # 真正加载 _ssl 并建 HTTPS 客户端（只建不发）：DLL 版本不对、缺根证书都会在这里暴露
        ssl.create_default_context()
        httpx.Client(base_url="https://localhost").close()
        backend = keyring.get_keyring()
        if "fail" in type(backend).__module__:
            raise RuntimeError(f"没有可用的凭据后端：{type(backend).__name__}")

    def _research() -> None:
        import io

        import bibtexparser
        import regex
        from docx import Document as WordDocument
        from PySide6.QtTextToSpeech import QTextToSpeech

        if not bibtexparser.loads("@article{test,title={Lumen}}").entries:
            raise RuntimeError("BibTeX 解析失败")
        if not list(regex.finditer("Lumen", "Lumen", timeout=0.1)):
            raise RuntimeError("正则搜索不可用")
        buffer = io.BytesIO()
        document = WordDocument()
        document.add_paragraph("中文笔记自检")
        document.save(buffer)
        if not buffer.getvalue().startswith(b"PK") or not hasattr(QTextToSpeech, "say"):
            raise RuntimeError("笔记或朗读依赖不可用")

    check("pdf_engine", _pdf)
    check("convert_deps", _convert)
    check("ocr_engine", _ocr)
    check("ai_deps", _ai)
    check("research_deps", _research)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    with contextlib.suppress(Exception):
        out = Path.home() / ".pdfreader" / "selftest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    with contextlib.suppress(Exception):
        print(text)
    return 0 if result["ok"] else 1


def file_from_argv(argv: list[str]) -> Path | None:
    """命令行里第一个存在的 PDF/EPUB 路径（含拖到 exe 图标上的文件）。"""
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        candidate = Path(arg)
        if candidate.suffix.lower() in (".pdf", ".epub") and candidate.is_file():
            return candidate
    return None


def main() -> int:
    """启动 PDF Reader 应用，返回退出码。"""
    if "--selftest" in sys.argv:
        return run_selftest()

    _logger.info("PDF Reader %s 启动", __version__)
    enable_faulthandler()
    sys.excepthook = global_exception_handler
    initial = file_from_argv(sys.argv)
    app = create_application(sys.argv)

    # 单实例：第二次启动只把文件交给已运行的窗口，不再开第二个进程
    instance = SingleInstance()
    if not instance.try_start():
        sent = instance.send_open([str(initial)] if initial is not None else [], str(Path.cwd()))
        if not sent:
            _logger.warning("已有实例在运行，但转交打开请求失败")
        return 0
    app.aboutToQuit.connect(instance.close)

    # 命令行给了文件时只打开它；否则恢复上次退出时的标签
    window = MainWindow(restore_session=initial is None)
    instance.messageReceived.connect(window.handle_second_instance)
    window.show()
    if initial is not None:
        # 等事件循环起来、窗口完成布局后再打开（适宽缩放依赖视口尺寸）
        QTimer.singleShot(0, lambda: window.open_path(initial))

    _logger.info("进入事件循环")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
