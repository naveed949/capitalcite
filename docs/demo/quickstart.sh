#!/usr/bin/env bash
# Fake-model local path used by the README recording. No OpenAI key. No Docker.
# Run from the repo root after `uv sync --frozen`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "demo: uv is required (https://docs.astral.sh/uv/)" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "demo: curl is required" >&2
  exit 1
fi

export SEC_USER_AGENT="${SEC_USER_AGENT:-capitalcite-demo you@example.com}"
export ASK_MODEL=fake
export QDRANT_PATH="${QDRANT_PATH:-${ROOT}/data/qdrant-demo}"
export LANGFUSE_TRACING_ENABLED=false
export LOG_LEVEL="${LOG_LEVEL:-WARNING}"
export AUDIT_LOG_PATH="${ROOT}/data/audit-demo.jsonl"
mkdir -p "${QDRANT_PATH}" "${ROOT}/data"

summarize() {
  uv run python "${ROOT}/docs/demo/summarize.py" "$1"
}

UVICORN_PID=""
cleanup() {
  if [[ -n "${UVICORN_PID}" ]] && kill -0 "${UVICORN_PID}" 2>/dev/null; then
    kill "${UVICORN_PID}" 2>/dev/null || true
    wait "${UVICORN_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "== chunk fixture corpus (small local ingest) =="
uv run capitalcite chunk \
  --input-dir tests/fixtures/chunking/corpus \
  --output-dir data \
  --chunker section

echo "== index with local bge-small into embedded Qdrant =="
uv run capitalcite index --input-dir data --chunker section --embedder bge-small

PORT="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
echo "== serve API on 127.0.0.1:${PORT} =="
uv run uvicorn capitalcite.api:app --host 127.0.0.1 --port "${PORT}" >/tmp/capitalcite-demo-uvicorn.log 2>&1 &
UVICORN_PID=$!
ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
    ready=1
    break
  fi
  sleep 0.2
done
if [[ "${ready}" -ne 1 ]]; then
  echo "demo: uvicorn did not become ready on 127.0.0.1:${PORT}" >&2
  cat /tmp/capitalcite-demo-uvicorn.log >&2 || true
  exit 1
fi

echo "== GET /health =="
curl -sS "http://127.0.0.1:${PORT}/health" | summarize health
echo

echo "== POST /search =="
curl -sS "http://127.0.0.1:${PORT}/search" -H 'content-type: application/json' \
  -d '{"query":"What CET1 ratio did JPMorgan disclose?","mode":"hybrid","k":3,"ticker":"JPM"}' \
  | summarize search
echo

echo "== POST /ask (fake model) =="
curl -sS "http://127.0.0.1:${PORT}/ask" -H 'content-type: application/json' \
  -d '{"question":"What CET1 ratio did JPMorgan disclose?","ticker":"JPM","model":"fake","k":5}' \
  | summarize ask
echo

echo "== POST /ask path=agent (fake model) =="
curl -sS "http://127.0.0.1:${PORT}/ask" -H 'content-type: application/json' \
  -d '{"question":"What CET1 ratio did JPMorgan disclose for 2024?","path":"agent","model":"fake","k":5}' \
  | summarize ask
echo

echo "== gate deny (unknown tool) =="
uv run capitalcite gate decide --tool shell --args '{"cmd":"ls"}' | summarize gate
echo
