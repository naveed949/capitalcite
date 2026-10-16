FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /src
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime

RUN useradd --uid 65532 --create-home --home-dir /home/app app \
    && mkdir -p /home/app/data \
    && chown -R app:app /home/app

COPY --from=builder /src/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASK_MODEL=fake \
    AUDIT_LOG_PATH=/home/app/data/audit.jsonl \
    DATA_DIR=/home/app/data

USER 65532:65532
WORKDIR /home/app
EXPOSE 8000

CMD ["uvicorn", "capitalcite.api:app", "--host", "0.0.0.0", "--port", "8000"]
