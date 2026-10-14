.PHONY: help install sync run test test-fast lint format format-check typecheck check samples icon build installer clean clean-dist

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

install: ## 安装 pre-commit hooks
	uv run pre-commit install

sync: ## 安装依赖（含开发依赖）
	uv sync

run: ## 启动应用
	uv run pdfreader

test: ## 运行全部测试（含覆盖率，门槛见 pyproject.toml）
	uv run pytest

test-fast: ## 快速测试（无覆盖率）
	uv run pytest --no-cov -q

lint: ## 代码检查（ruff）
	uv run ruff check .

format: ## 格式化代码
	uv run ruff format .

format-check: ## 检查格式（不改文件）
	uv run ruff format --check .

typecheck: ## 类型检查（mypy strict）
	uv run mypy

check: lint format-check typecheck test ## 提交前全套检查：lint + 格式 + 类型 + 测试

samples: ## 重新生成测试样例 PDF（tests/fixtures/samples/）
	uv run python scripts/download_samples.py

icon: ## 由内置 logo 重新生成 app.ico
	uv run python scripts/make_icon.py

build: ## PyInstaller 打包程序目录 dist/PDFReader/
	uv run python scripts/build_windows.py

installer: ## 打包并生成安装程序 dist/PDFReader-Setup-<版本>.exe（需要 NSIS）
	uv run python scripts/build_windows.py --installer

clean: ## 清理缓存与打包中间产物（保留 dist/）
	rm -rf build *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .tox .coverage
	find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true

clean-dist: ## 删除打包产物 dist/（含安装程序）
	rm -rf dist
