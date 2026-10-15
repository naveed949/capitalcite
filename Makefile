.PHONY: help sync lint format typecheck test ci up down health

UV ?= uv
PYTEST ?= $(UV) run pytest
RUFF ?= $(UV) run ruff
MYPY ?= $(UV) run mypy

help:
	@echo "sync       Install the package and dev tools with uv"
	@echo "lint       Ruff check"
	@echo "format     Ruff format"
	@echo "typecheck  mypy (strict)"
	@echo "test       pytest"
	@echo "ci         lint + typecheck + test"
	@echo "up         docker compose up -d"
	@echo "down       docker compose down"
	@echo "health     GET /health against a running local API"

sync:
	$(UV) sync --all-groups

lint:
	$(RUFF) check .

format:
	$(RUFF) format .

typecheck:
	$(MYPY) src tests

test:
	$(PYTEST)

ci: lint typecheck test

up:
	docker compose up -d

down:
	docker compose down

health:
	curl -sf http://127.0.0.1:8000/health && echo
