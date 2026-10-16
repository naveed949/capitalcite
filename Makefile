.PHONY: help sync lint format typecheck test ci up down health eval-report infra-validate image deploy

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
	@echo "eval-report  Rewrite the README retrieval table from evals/results"
	@echo "infra-validate  bicep build (+ docker build if Docker exists)"
	@echo "image      docker build of the API image (UNVERIFIED without Docker)"
	@echo "deploy     opt-in Azure apply; UNVERIFIED without az login"

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

eval-report:
	$(UV) run capitalcite eval --suite golden --from-results evals/results --readme README.md

infra-validate:
	./scripts/infra-validate.sh

image:
	@if command -v docker >/dev/null 2>&1; then docker build -t capitalcite:local .; else echo "UNVERIFIED: docker is not installed"; fi

deploy:
	./scripts/deploy.sh
