FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder

WORKDIR /src
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

COPY tests/fixtures/chunking/corpus /tmp/corpus

# The builder writes a pinned FastEmbed cache and the fixture index. It does
# not call EDGAR. Index and the model-load check run with HF_HUB_OFFLINE=1.
ENV SEC_USER_AGENT="capitalcite-image build@example.com" \
    ASK_MODEL=fake \
    EMBEDDER=bge-small \
    CHUNKER=section \
    EMBED_THREADS=1 \
    FASTEMBED_CACHE_PATH=/app/fastembed \
    QDRANT_PATH=/app/qdrant

RUN mkdir -p /app/fastembed /app/qdrant \
    && python -c 'from capitalcite.retrieval.fastembed_pins import prefetch_fastembed_cache; prefetch_fastembed_cache()' \
    && export HF_HUB_OFFLINE=1 \
    && capitalcite chunk --input-dir /tmp/corpus --chunker section --output-dir /tmp/demo \
    && capitalcite index --input-dir /tmp/demo --embedder bge-small \
    && python -c 'from pathlib import Path; from capitalcite.retrieval.fastembed_pins import verify_pinned_files; verify_pinned_files(Path("/app/fastembed"))' \
    && python -c 'from fastembed import TextEmbedding; from fastembed.sparse import SparseTextEmbedding; TextEmbedding("BAAI/bge-small-en-v1.5"); SparseTextEmbedding("Qdrant/bm25")'

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS metrics

WORKDIR /src
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    DBT_PROJECT_DIR=/src/dbt \
    DBT_PROFILES_DIR=/src/dbt \
    DBT_TARGET=ci

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY dbt ./dbt
COPY tests/fixtures/metrics ./tests/fixtures/metrics

RUN uv sync --frozen --group metrics --no-dev --no-editable \
    && uv run --frozen --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci \
    && uv run --frozen --group metrics python -c "from pathlib import Path; import os; from capitalcite.metric_query.backends import compile_catalog; out = Path('/tmp/catalog.json'); compile_catalog(out, cwd=Path('/src'), env=os.environ); committed = Path('src/capitalcite/metric_query/catalog.json').read_bytes(); assert out.read_bytes() == committed, 'stale metric catalog'"

FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime

RUN useradd --uid 65532 --create-home --home-dir /home/app app \
    && mkdir -p /home/app/data \
    && chown -R app:app /home/app

ARG GIT_SHA=unknown

COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /app/qdrant /app/qdrant
COPY --from=builder --chown=65532:65532 /app/fastembed /app/fastembed
COPY --from=metrics --chown=65532:65532 /src/dbt/target/ci.duckdb /app/warehouse/ci.duckdb

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASK_MODEL=fake \
    AUDIT_LOG_PATH=/home/app/data/audit.jsonl \
    DATA_DIR=/home/app/data \
    QDRANT_PATH=/app/qdrant \
    FASTEMBED_CACHE_PATH=/app/fastembed \
    GIT_SHA=${GIT_SHA} \
    ASK_RATE_LIMIT=60 \
    EMBED_THREADS=1 \
    HF_HUB_OFFLINE=1 \
    METRIC_QUERY_BACKEND=catalog \
    WAREHOUSE_PATH=/app/warehouse/ci.duckdb

USER 65532:65532
WORKDIR /home/app
EXPOSE 8000

CMD ["uvicorn", "capitalcite.api:app", "--host", "0.0.0.0", "--port", "8000"]
