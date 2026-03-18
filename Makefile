.PHONY: install dev run paper live test lint fmt clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"
	python -m spacy download en_core_web_sm

run:
	python -m src.main

paper:
	TRADING_MODE=paper python -m src.main

live:
	TRADING_MODE=live python -m src.main

test:
	pytest tests/ -v --tb=short

lint:
	ruff check src/ tests/
	mypy src/

fmt:
	ruff format src/ tests/
	ruff check --fix src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
