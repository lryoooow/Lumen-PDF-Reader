# CLAUDE.md

本项目的开发约束见 **[AGENTS.md](AGENTS.md)**（唯一事实来源，避免两份规则漂移）。

速览：

* 回复用中文；自主执行，只在删除文件/目录、核心架构调整、破坏性数据操作前确认。
* 三条铁律：`core/`+`services/` 不 import PySide6/pymupdf；只有 `engines/` 知道 PyMuPDF；
  子线程只跑 `workers/`（OCR/搜索/转换），页面渲染在主线程直接渲染受控文档。
* 坐标一律"可见帧"（`page.rect`，已应用 /Rotate）；文本读取走引擎 `_textpage()`
  （PyMuPDF 的 `Page.get_text*` 会改写 /Rotate，和撤销日志冲突）。
* 提交前：`uv run ruff check .` → `uv run ruff format --check .` → `uv run mypy` → `uv run pytest`。
* 分析记录、复现脚本、探针放本地 `memory/`（不入库）；现状文档见 `docs/README.md`。
* 版本号三处同步：`pyproject.toml`、`src/pdfreader/__init__.py`、`scripts/installer.nsi`，并写 `CHANGELOG.md`。
