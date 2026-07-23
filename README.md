# PDF Reader

> 开源多功能 PDF 阅读器与编辑器 · AGPL-3.0
> 当前版本：**0.0.1**（开发中，阶段 0：工程基础设施）

一个用 Python + PySide6 + PyMuPDF 打造的跨平台 PDF 阅读与编辑软件。

## ✨ 设计目标

- **阅读**：流畅渲染、连续滚动、缩放、全文搜索、文本选择复制
- **编辑**：增删页、合并/拆分、旋转、水印
- **批注**：高亮、手写、便签、框选
- **OCR**：中文识别（基于 RapidOCR）
- **转换**：PDF ↔ Word / Excel / 图片 / TXT

## 🛡️ 许可证

本项目采用 **GNU Affero General Public License v3.0 (AGPL-3.0-or-later)**。

AGPL 的"网络条款"意味着：任何通过网络向终端用户提供本软件功能的服务，也必须向所有用户提供完整源代码。这使得本项目对商业闭源使用形成天然限制，符合"开源共享、避免被直接拿去商用"的初衷。

## 🧱 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| GUI 框架 | [PySide6](https://www.qt.io/) (Qt6) | 跨平台桌面 GUI |
| PDF 引擎 | [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | 渲染/编辑/提取/批注（AGPL，天然兼容） |
| OCR | [RapidOCR](https://github.com/RapidAI/RapidOCR) | 中文识别（Apache-2.0） |
| 包管理 | [uv](https://github.com/astral-sh/uv) | 极速依赖管理 |
| 代码质量 | ruff / mypy / pytest / pre-commit | 格式化 + 类型 + 测试 |

## 📐 架构

采用分层架构，核心是**引擎适配层隔离第三方依赖**：

```
GUI 表现层 (gui/)         PySide6 控件
控制器层 (controllers/)    协调 GUI ↔ 业务
业务核心层 (core/)         纯 Python 模型（不依赖 Qt/pymupdf）
服务层 (services/)         OCR、格式转换
引擎适配层 (engines/)      唯一 import pymupdf 的地方 ★
后台线程 (workers/)        渲染/OCR 线程（隔离非线程安全操作）
```

**三条铁律**：
1. `core/` 和 `services/` 绝不 import PySide6 或 pymupdf
2. 只有 `engines/` 知道 PyMuPDF 的存在
3. 只有 `workers/` 在子线程跑 PyMuPDF（非线程安全）

详见 [docs/02_项目结构.md](docs/02_项目结构.md)。

## 🚀 开发路线

| 阶段 | 状态 | 交付 |
|---|---|---|
| 0 工程基础设施 | 🔄 进行中 | 空窗口 + 工具链就绪 |
| 1 引擎适配层 | ⏳ | PyMuPDF 封装 + 测试 |
| 2 核心模型层 | ⏳ | Document/Page 模型 |
| 3 最小 GUI | ⏳ | 打开 + 单页渲染 🎯 |
| 4 连续滚动+缩放 | ⏳ | 流畅浏览（难点） |
| 5 搜索+复制 | ⏳ | 全文搜索/选择 |
| 6 编辑功能 | ⏳ | 增删页/合并/水印 |
| 7 注释批注 | ⏳ | 高亮/手写/便签 🎯 |
| 8 OCR | ⏳ | 中文识别 |
| 9 格式转换 | ⏳ | PDF↔Word/Excel |
| 10 发布 v0.1.0 | ⏳ | 打包+文档 🎯 |

完整计划见 [docs/03_开发计划.md](docs/03_开发计划.md)。

## 🔧 开发

### 环境要求

- Python ≥ 3.10（推荐 3.12/3.13）
- [uv](https://docs.astral.sh/uv/) 包管理器

### 快速开始

```bash
# 安装依赖
uv sync

# 安装 git pre-commit hooks
make install

# 启动应用
make run

# 运行测试
make test

# 全套检查（lint + 类型 + 测试）
make check
```

### 常用命令

| 命令 | 作用 |
|---|---|
| `make run` | 启动应用 |
| `make test` | 运行测试（含覆盖率） |
| `make lint` | 代码检查 |
| `make format` | 格式化代码 |
| `make typecheck` | 类型检查 |
| `make check` | 全套检查 |
| `make clean` | 清理产物 |

## 📁 文档

- [技术调研报告](docs/00_技术调研报告.md)
- [最终技术选型方案](docs/01_最终技术选型方案.md)
- [项目结构](docs/02_项目结构.md)
- [开发计划](docs/03_开发计划.md)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。贡献者请遵守 AGPL-3.0 许可证。

## 📜 许可证

Copyright © 2026 PDF Reader Contributors.
Licensed under the [GNU AGPL-3.0-or-later](LICENSE).
