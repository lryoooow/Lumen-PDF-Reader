# Changelog

本项目所有重要变更均会记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.0.1] - 2026-07-23

### 首个完整版本 🎉

完成全部 11 个开发阶段（0-10），实现多功能 PDF 阅读与编辑软件。

#### 功能
- **阅读**：打开 PDF、连续滚动浏览、缩放(Ctrl+/-)、适应宽度
- **搜索**：Ctrl+F 全文搜索（后台线程，不卡 UI）、点击结果跳转
- **编辑**：删除页、旋转页、合并 PDF、添加水印、另存为
- **批注**：高亮（拖选）、手写笔迹（画线）、文本便签（点击）🎯
- **OCR**：扫描件中文识别（基于 RapidOCR PP-OCR 模型）
- **转换**：PDF → PNG/JPEG/TXT/Word(docx)/Excel(xlsx)，图片 → PDF

#### 技术
- 分层架构：引擎适配层 / 业务核心 / 服务层 / 后台线程 / GUI（严格隔离第三方依赖）
- 异步渲染线程（遵守 PyMuPDF 非线程安全约束）
- 设置持久化（~/.pdfreader/settings.json）、日志（~/.pdfreader/pdfreader.log）
- 全局异常友好处理

#### 质量
- 250 个单元/集成测试全部通过
- mypy strict 类型检查通过（41 文件）
- ruff lint + format 全通过

#### 打包
- PyInstaller 打包为单文件 `PDFReader.exe`（177 MB，无需 Python 环境）
- 构建脚本：`scripts/build_windows.py`

详细开发历程见 git log 与 [docs/](docs/)。

[Unreleased]: https://github.com/your-org/pdfreader/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/your-org/pdfreader/releases/tag/v0.0.1
