FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

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

# The builder downloads FastEmbed and writes the fixture index. It does not
# call EDGAR. HF_HUB_OFFLINE is set only on the runtime stage, below.
ENV SEC_USER_AGENT="capitalcite-image build@example.com" \
    ASK_MODEL=fake \
    EMBEDDER=bge-small \
    CHUNKER=section \
    FASTEMBED_CACHE_PATH=/app/fastembed \
    QDRANT_PATH=/app/qdrant

RUN mkdir -p /app/fastembed /app/qdrant \
    && capitalcite chunk --input-dir /tmp/corpus --chunker section --output-dir /tmp/demo \
    && capitalcite index --input-dir /tmp/demo --embedder bge-small \
    && python -c 'from fastembed import TextEmbedding; from fastembed.sparse import SparseTextEmbedding; TextEmbedding("BAAI/bge-small-en-v1.5"); SparseTextEmbedding("Qdrant/bm25")'

# Offline load checks mock.file and tamil.txt. The Qdrant/bm25 snapshot does not
# ship those names. Empty files keep a later load on this cache.
RUN python -c 'import os; from pathlib import Path; cache=Path(os.environ["FASTEMBED_CACHE_PATH"]); snaps=list(cache.glob("models--Qdrant--bm25/snapshots/*")); assert snaps, "bm25 snapshot missing"; [(snap / "mock.file").touch() for snap in snaps]; [(snap / "tamil.txt").touch() for snap in snaps]; os.environ["HF_HUB_OFFLINE"]="1"; from fastembed import TextEmbedding; from fastembed.sparse import SparseTextEmbedding; TextEmbedding("BAAI/bge-small-en-v1.5"); SparseTextEmbedding("Qdrant/bm25")'

FROM python:3.12-slim-bookworm AS runtime

RUN useradd --uid 65532 --create-home --home-dir /home/app app \
    && mkdir -p /home/app/data \
    && chown -R app:app /home/app

ARG GIT_SHA=unknown

COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /app/qdrant /app/qdrant
COPY --from=builder --chown=65532:65532 /app/fastembed /app/fastembed

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
    HF_HUB_OFFLINE=1

USER 65532:65532
WORKDIR /home/app
EXPOSE 8000

CMD ["uvicorn", "capitalcite.api:app", "--host", "0.0.0.0", "--port", "8000"]
