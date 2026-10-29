# PDF Reader 项目开发约束

* 所有回复使用中文。
* 你是一名资深的 Python 桌面应用工程师，负责维护本项目 Lumen（Python + PySide6 + PyMuPDF 的 PDF 文献阅读器，包名 `pdfreader`）。
* 开发中注重细节、逻辑严谨与长期可维护性：优先采用简单、高效、稳定且符合现有架构的方案，
  避免过度设计和无意义的复杂化。
* 不得随意修改与当前任务无关的代码，也不得借机进行无必要的重构。
* 分析记录、排查报告、复现脚本、一次性探针统一放在根目录 `memory/`（本地目录，已加入 `.gitignore`，不入库），
  不得散落在其它目录；结论要沉淀进测试、代码注释或 `docs/`。

## 自主执行原则

* 默认自主完成分析、修改、命令执行、测试与修复，不因常规操作反复询问。
* 信息不完整时，先读代码/文档/测试自行判断，选改动最小、风险最低的方案。
* 存在多个可行方案时，自主选择最贴合现有架构、最简单可靠的那个。
* 测试或构建失败时继续定位修复，直到闭环或确认是外部阻塞。
* 仅在涉及**删除文件/目录、核心架构调整、破坏性数据操作**时先说明影响范围并等用户确认；
  其余情况直接执行，完成后统一汇报改动、验证结果与剩余风险。

## 架构边界（三条铁律）

1. `core/`、`services/` 绝不 import PySide6 或 pymupdf —— 这条由 `tests/unit/test_layering.py`
   用 AST 扫描强制（新增违规会直接失败）。
2. 只有 `engines/` 知道 PyMuPDF 的存在（`main.py --selftest` 是打包自检的例外）。
   第三方库（pdf2docx / pdfplumber / rapidocr / httpx / keyring）只允许出现在 `services/`。
3. PyMuPDF 非线程安全：子线程只跑 `workers/` 里的 OCR / 搜索 / 转换 / AI 请求；**页面渲染在主线程**
   直接渲染受控文档（`MainWindow._render_page`），以保证批注/编辑所见即所得。
   AI 用到的文字在主线程分片索引或直接取好再交给 `AiWorker`，后台线程只做网络 I/O。
4. 分层依赖方向：`gui → controllers → core → engines`（`services`/`workers` 可被 gui 使用）。
   领域逻辑不写进 GUI 事件处理器，错误经控制器上报（`DocumentController.last_error`）。

## 不可破坏的约定

* **坐标一律"可见帧"**：引擎对外返回/接受的矩形与点都是 `page.rect` 坐标系（已应用 /Rotate），
  与 `render_page` 位图逐像素对应。批注/内容流是"未旋转帧"，必须在引擎边界折算
  （见 `engines/pdf_engine.py` 顶部「旋转页坐标约定」）。改动坐标相关代码时必须补/跑
  `tests/unit/engines/test_rotated_pages.py`。
* **journal 与读取路径**：`open()` 必开 PyMuPDF 日志以支持撤销，因此*不要*直接调用会临时改写
  `/Rotate` 的 `Page.get_text*` / `search_for`；文本读取统一走引擎内的 `_textpage()`。
* **当前页由视图维护**：`PdfView` 以「视口中露出最多的页」为当前页；任何重排（编辑 / 撤销 / 缩放）
  都要用 `setup_document(..., keep_page=…)` 或 `reading_position()/restore_position()` 保持阅读位置，
  不得把「无当前页」静默当成第 1 页（否则删除 / 旋转会作用到错误的页）。
* **用户数据优先**：任何"保存/导出/快照"路径都不得静默写明文、丢加密、退回磁盘旧内容或吞异常；
  失败要如实上报给界面。
* **用户可见的失败不得静默**：`except` 后必须留日志或把原因交给界面显示（后台线程用信号上报）。
* **AI 隐私**：API Key 只进系统凭据管理器（`services/ai/keystore.py`），不得写入 `settings.json` /
  `ai.json` / 日志；内容只在用户主动发起时发送；测试一律用 `httpx.MockTransport` 与内存凭据库，不访问真实网络。

## Python 规范

* Python 3.13，依赖与命令统一用 `uv`（`uv run ...`）。
* 公共函数必须标注参数与返回类型；`mypy` 为 strict，不能靠 `type: ignore` 掩盖设计问题
  （测试里针对第三方库/动态属性的少量 ignore 可接受）。
* 新功能与 Bug 修复必须补测试：单元测试优先，GUI 相关用 `pytest-qt`（`qtbot`）。
* 格式化/检查：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv run pytest`。
* 面向用户的错误消息保持脱敏，不暴露完整堆栈；日志（`utils/logger.py`）里可以详细。

## 验证与测试

* Bug 修复先写能稳定复现问题的测试（或把复现脚本放 `memory/`），再验证修复结果。
* 新功能要覆盖正常流程、关键边界与主要异常分支。
* 不得为了通过测试而修改断言目标、绕过真实逻辑、删除或削弱测试。
* 测试结果如实汇报，不把"部分通过"说成"全部通过"；受环境限制（如内存不足导致
  ONNXRuntime `bad allocation`、剪贴板被占用）的用例应显式 `skip` 并写明原因。

## 打包与发布

* `scripts/build_windows.py`：PyInstaller **onedir** 打包 + NSIS 向导式安装程序
  （`scripts/installer.nsi`，产物 `dist/Lumen-<version>-Windows-x64-Setup.exe`）。
* 版本号三处同步：`pyproject.toml`、`src/pdfreader/__init__.py`、`scripts/installer.nsi`；
  变更同步写入 `CHANGELOG.md`（Keep a Changelog 格式）。

## 文档维护

* `docs/` 下 01–07 描述**现状**（功能、架构、结构、界面规范、开发测试、打包发布、界面重设计参考），导航见 `docs/README.md`。
* 改动功能、接口、流程或目录时，同步修改对应文档；不在现状文档里保留「计划中 / 示意」的过时内容。
* 过时的调研、选型与计划直接移除（git 历史可查），不在 `docs/` 保留；文档截图放 `docs/images/`，
  由 `scripts/capture_screenshots.py` 生成。

## 项目开发记忆

* 阶段摘要、排查报告、修复记录写进本地 `memory/`（中文文件名，如
  `2026-10-13_问题排查记录.md`、`memory/repro_*.py`、`memory/_probe_*.py`），不提交到仓库。
* 用户可感知的变更写进 `CHANGELOG.md`；能复现缺陷的脚本应尽量转成 `tests/` 里的回归测试。
* 长期稳定的约束写在 `AGENTS.md`（本文件）；`CLAUDE.md` 只做指向本文件的说明，避免两份漂移。

## 计划模式

* 先读代码、相关文件与调用链，再给实施方案；方案包含：目标、涉及模块、改动文件摘要、
  实现思路、风险、验证方式与完成标准。
* 不得只给笼统步骤，也不得在未检查代码的情况下猜测实现。
* 验证必须针对真实需求设计，不以"通过现有测试"为唯一目标。
