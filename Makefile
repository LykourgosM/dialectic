.PHONY: install test test-all e2e lint format typecheck check cov clean build

install:
	uv sync --extra dev
	uv run pre-commit install

test:
	uv run pytest --ignore=tests/test_e2e_real_cli.py

test-all:
	uv run pytest

e2e:
	DIALECTIC_E2E=1 uv run pytest tests/test_e2e_real_cli.py -v -s

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy dialectic

cov:
	uv run pytest --ignore=tests/test_e2e_real_cli.py --cov=dialectic --cov-report=term-missing --cov-report=html

check: lint typecheck test

build:
	uv build

clean:
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
