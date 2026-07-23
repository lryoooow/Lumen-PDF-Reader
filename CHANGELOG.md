# Changelog

本项目所有重要变更均会记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增（开发中）
- **阶段 1：PDF 引擎适配层**（隔离 PyMuPDF 的核心层）
  - `PdfEngine` 协议（`base.py`）：定义引擎层对外稳定契约
  - `PyMuPdfEngine` 实现（`pdf_engine.py`）：open / metadata / page_count / render_page / extract_text / search / iter_pages_text
  - 数据结构（`types.py`）：DocumentMeta / PixmapData / SearchResult / PageText 等不可变值对象
  - 异常体系（`exceptions.py`）：PdfEngineError 基类及 6 个子类（FileOpen / PasswordRequired / CorruptPdf / PageIndex / Render / Search）
  - 7 个测试样例 PDF（纯文本/扫描件/表格/批注/加密/空白/损坏）
  - 48 个引擎层单元测试，覆盖率 92%
  - 全项目仅 `engines/pdf_engine.py` 含 `import pymupdf`
- 工程基础设施：`pyproject.toml`（uv + hatchling）、src 布局、质量工具链（ruff/mypy/pytest/pre-commit）
- 最小应用骨架：`pdfreader` 包可启动空主窗口
- 完整项目文档：技术调研、选型、项目结构、开发计划
- AGPL-3.0 许可证

## [0.0.1] - 开发中

初始项目骨架（阶段 0）。详细开发路线见 [docs/03_开发计划.md](docs/03_开发计划.md)。

[Unreleased]: https://github.com/your-org/pdfreader/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/your-org/pdfreader/releases/tag/v0.0.1
