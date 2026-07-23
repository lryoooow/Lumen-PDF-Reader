.PHONY: help install sync run test test-fast lint format typecheck check clean

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## 安装 pre-commit hooks
	uv run pre-commit install

sync: ## 安装依赖（含开发依赖）
	uv sync

run: ## 启动应用
	uv run pdfreader

test: ## 运行全部测试（含覆盖率）
	uv run pytest

test-fast: ## 快速测试（无覆盖率）
	uv run pytest --no-cov -q

lint: ## 代码检查（ruff）
	uv run ruff check .

format: ## 格式化代码
	uv run ruff format .

typecheck: ## 类型检查（mypy）
	uv run mypy

check: lint typecheck test ## 全套检查：lint + 类型 + 测试

clean: ## 清理构建产物与缓存
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .tox
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
