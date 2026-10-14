# Lumen · PDF 文献阅读器

> 专注阅读，安静思考 —— 开源的 PDF 文献阅读与批注工具 · AGPL-3.0
> 当前版本：**Lumen 0.0.1**（版本号见 `pyproject.toml`，变更见 [CHANGELOG](CHANGELOG.md)）

用 Python + PySide6 + PyMuPDF 打造的桌面 PDF 阅读器，面向论文 / 文献阅读场景。

![Lumen 阅读界面](docs/images/reading-annotations.png)

## 功能

- **阅读**：连续滚动、缩放 10%–800%（高分屏清晰）、跳页对齐页首、记住每个文件的阅读位置、夜间 / 护眼模式
- **科研导航**：目录、缩略图、参考文献 / 图表链接跳转（先弹预览卡）、后退 / 前进
- **搜索与复制**：边输边搜（F3 前后跳转）、中文精确到字的选择、去换行复制（便于翻译）
- **批注与笔记**：五色高亮、下划线 / 删除线 / 波浪线、手写、便签、签名、时间戳；批注面板编辑笔记，一键导出 Markdown
- **编辑**：删页、旋转、合并、半透明水印、撤销 / 重做
- **加密文档**：输入密码打开；搜索 / OCR / 导出自动使用密码；另存为保留加密与权限
- **OCR 与转换**：中文 OCR（RapidOCR）；PDF → 图片 / TXT / Word / Excel

完整说明见 [docs/01_功能说明](docs/01_功能说明.md)。

## 安装（Windows）

运行 `PDFReader-Setup-<版本>.exe` 安装向导即可，无需 Python。安装后可在 PDF 文件右键「打开方式」中选择本程序。
设置与日志保存在 `%USERPROFILE%\.pdfreader\`，卸载时保留。

## 开发

```bash
uv sync          # 安装依赖（Python ≥ 3.10，开发用 3.13）
make run         # 启动
make check       # 提交前检查：ruff → 格式 → mypy → pytest
make installer   # 打包并生成安装程序（需要 NSIS）
```

分层架构（`gui → controllers → core → engines`，只有 `engines/` 接触 PyMuPDF）与开发约定见文档。

## 文档

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 全部文档索引 |
| [01 功能说明](docs/01_功能说明.md) | 功能入口、快捷键、设置、路线图 |
| [02 架构设计](docs/02_架构设计.md) | 分层、铁律、关键机制、不可破坏的约定 |
| [03 项目结构](docs/03_项目结构.md) | 目录与文件职责 |
| [04 界面与交互规范](docs/04_界面与交互规范.md) | Lumen 设计令牌、动效、控件接口 |
| [05 开发与测试](docs/05_开发与测试.md) | 环境、命令、质量门禁、测试约定 |
| [06 打包与发布](docs/06_打包与发布.md) | PyInstaller、NSIS、发布清单 |

协作约束见 [AGENTS.md](AGENTS.md)；分析记录与问题复现脚本见 [memory/](memory/README.md)。

## 许可证

GNU Affero General Public License v3.0（AGPL-3.0-or-later），见 [LICENSE](LICENSE)。
通过网络向用户提供本软件功能的服务，也须向用户提供完整源代码。

Copyright © 2026 Lumen Contributors.
