"""诊断包：把版本、运行环境、日志与脱敏设置打成一个 zip。

用户反馈问题时，「关于 → 导出诊断信息」生成这个压缩包。它**不包含文档内容**：
设置里的最近文件、阅读位置、书签、会话路径、Zotero 目录等路径字段会被移除，
API Key（即使误写进 ai.json）与 ``sk-`` 形式的串会被替换。日志只保留尾部。
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from pdfreader import __version__

__all__ = ["collect_diagnostics", "redact_text", "sanitize_settings"]

_logger = logging.getLogger("pdfreader")

_LOG_TAIL_BYTES = 200_000
# 设置里与个人文档路径相关的字段：诊断包不需要，直接剔除
_PATH_FIELDS = frozenset(
    {
        "recent_files",
        "reading_positions",
        "bookmarks",
        "favorites",
        "doc_meta",
        "session_files",
        "session_active",
        "zotero_data_dir",
    }
)
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")
_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)")


def redact_text(text: str, home: Path | None = None) -> str:
    """去掉用户主目录前缀与疑似密钥。"""
    if home is not None:
        text = re.sub(re.escape(str(home)), "~", text, flags=re.IGNORECASE)
    return _SECRET_RE.sub("***", text)


def sanitize_settings(data: object) -> dict[str, object]:
    """设置字典去掉路径字段；非字典输入返回空字典。"""
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if k not in _PATH_FIELDS}


def _strip_secrets(value: object) -> object:
    """递归删除键名像密钥的项（ai.json 正常不含 Key，这里防御历史版本 / 手改文件）。"""
    if isinstance(value, dict):
        return {
            str(k): _strip_secrets(v) for k, v in value.items() if not _SECRET_KEY_RE.search(str(k))
        }
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "未安装"


def _environment() -> dict[str, object]:
    return {
        "app_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "pyside6": _version("PySide6"),
        "pymupdf": _version("PyMuPDF"),
        "rapidocr": _version("rapidocr-onnxruntime"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _json_text(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _add_text(archive: zipfile.ZipFile, name: str, text: str) -> None:
    archive.writestr(name, text.encode("utf-8"))


def _add_file(archive: zipfile.ZipFile, name: str, path: Path, home: Path | None) -> None:
    try:
        raw = path.read_bytes()
    except OSError:
        return
    text = raw.decode("utf-8", errors="replace")
    _add_text(archive, name, redact_text(text, home))


def _add_log_tail(archive: zipfile.ZipFile, name: str, path: Path, home: Path | None) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _LOG_TAIL_BYTES:
                handle.seek(size - _LOG_TAIL_BYTES)
            raw = handle.read()
    except OSError:
        return
    text = raw.decode("utf-8", errors="replace")
    if size > _LOG_TAIL_BYTES:
        text = "……（仅保留日志末尾）\n" + text
    _add_text(archive, name, redact_text(text, home))


def collect_diagnostics(
    destination: Path,
    *,
    home: Path | None = None,
    settings_path: Path | None = None,
    ai_settings_path: Path | None = None,
    log_file: Path | None = None,
    crash_log: Path | None = None,
    recovery_dir: Path | None = None,
    selftest_path: Path | None = None,
) -> Path:
    """生成诊断 zip，返回实际写入的路径（父目录不存在会创建）。"""
    from pdfreader.config.settings import default_settings_path
    from pdfreader.services.ai.provider import default_ai_settings_path
    from pdfreader.utils.logger import CRASH_LOG_FILE, LOG_FILE

    home = home or Path.home()
    settings_path = settings_path or default_settings_path()
    ai_settings_path = ai_settings_path or default_ai_settings_path()
    log_file = log_file or LOG_FILE
    crash_log = crash_log or CRASH_LOG_FILE
    recovery_dir = recovery_dir or home / ".pdfreader" / "recovery"
    selftest_path = selftest_path or home / ".pdfreader" / "selftest.json"

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        _add_text(archive, "environment.json", _json_text(_environment()))
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except OSError as exc:
            _logger.warning("读取设置失败，诊断包省略 settings.json：%s", exc)
            settings = None
        except ValueError as exc:
            _logger.warning("设置不是合法 JSON，诊断包省略 settings.json：%s", exc)
            settings = None
        if settings is not None:
            _add_text(
                archive,
                "settings.json",
                redact_text(_json_text(sanitize_settings(settings)), home),
            )
        try:
            ai_settings = json.loads(ai_settings_path.read_text(encoding="utf-8"))
        except OSError as exc:
            _logger.warning("读取 AI 设置失败，诊断包省略 ai.json：%s", exc)
            ai_settings = None
        except ValueError as exc:
            _logger.warning("AI 设置不是合法 JSON，诊断包省略 ai.json：%s", exc)
            ai_settings = None
        if ai_settings is not None:
            _add_text(
                archive,
                "ai.json",
                redact_text(_json_text(_strip_secrets(ai_settings)), home),
            )
        _add_log_tail(archive, "logs/pdfreader.log", log_file, home)
        _add_log_tail(archive, "logs/crash.log", crash_log, home)
        _add_file(archive, "selftest.json", selftest_path, home)
        count = 0
        try:
            count = len(list(recovery_dir.glob("*.json")))
        except OSError:
            count = 0
        _add_text(archive, "recovery.json", _json_text({"snapshots": count}))
        _add_text(
            archive,
            "README.txt",
            "Lumen 诊断信息：版本、运行环境、日志尾部与脱敏设置。\n"
            "不包含 PDF 内容；最近文件、阅读位置、书签、会话路径等字段已移除。\n",
        )

    _logger.info("诊断信息已导出到 %s", destination)
    return destination
