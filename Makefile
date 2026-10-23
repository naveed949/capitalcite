.PHONY: help sync lint format typecheck test ci check up down health eval-report infra-validate image bootstrap deploy

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
	@echo "check      capitalcite check against evals/baselines/ci.json"
	@echo "up         docker compose up -d"
	@echo "down       docker compose down"
	@echo "health     GET /health against a running local API"
	@echo "eval-report  Rewrite the README retrieval table from RESULTS"
	@echo "infra-validate  bicep format + build of bootstrap and main (+ docker build if Docker exists)"
	@echo "image      docker build of the API image (tag IMAGE, GIT_SHA build-arg; UNVERIFIED without Docker)"
	@echo "bootstrap  what-if of infra/bootstrap.bicep; UNVERIFIED without az login"
	@echo "deploy     what-if of infra/main.bicep; UNVERIFIED without az login or bootstrap"

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

ci: lint typecheck test check

check:
	$(UV) run capitalcite check

up:
	docker compose up -d

down:
	docker compose down

health:
	curl -sf http://127.0.0.1:8000/health && echo

HEAD_SHA := $(shell git rev-parse --short=12 HEAD)
HEAD_RESULTS := evals/results/$(HEAD_SHA).json
ifeq ($(origin RESULTS), undefined)
  ifneq ($(wildcard $(HEAD_RESULTS)),)
    RESULTS := $(HEAD_RESULTS)
  else
    RESULTS := evals/results
  endif
endif

eval-report:
	@if [ ! -e "$(RESULTS)" ]; then \
		echo "error: missing $(RESULTS). Run a full eval or set RESULTS=evals/results/<sha12>.json"; \
		exit 1; \
	fi
	$(UV) run capitalcite eval --suite golden --from-results "$(RESULTS)" --readme README.md

infra-validate:
	./scripts/infra-validate.sh

IMAGE ?= capitalcite:local
GIT_SHA ?= $(shell git rev-parse HEAD 2>/dev/null || echo unknown)

image:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		docker build --build-arg GIT_SHA="$(GIT_SHA)" -t "$(IMAGE)" .; \
	elif command -v docker >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then \
		sudo -n docker build --build-arg GIT_SHA="$(GIT_SHA)" -t "$(IMAGE)" .; \
	elif command -v docker >/dev/null 2>&1; then \
		echo "UNVERIFIED: docker is installed but this user cannot talk to the daemon"; \
	else \
		echo "UNVERIFIED: docker is not installed"; \
	fi

bootstrap:
	./scripts/bootstrap.sh

deploy:
	./scripts/deploy.sh
