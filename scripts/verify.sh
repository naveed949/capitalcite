#!/usr/bin/env bash
# Drive capitalcite the way a user does: live uvicorn + CLI.
# Usage (repo root): ./scripts/verify.sh
# Evidence: artifacts/verify/<UTC-timestamp>/  (gitignored)
# Cleanup kills only the uvicorn this script started; evidence is kept.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "verify: uv is required (https://docs.astral.sh/uv/)" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "verify: curl is required" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
ART="${ROOT}/artifacts/verify/${TS}"
mkdir -p "$ART"

UVICORN_PID=""

log() {
  printf '%s\n' "$*"
}

write() {
  local file="$1"
  shift
  printf '%s\n' "$*" >"${ART}/${file}"
}

cleanup() {
  local rc=$?
  if [[ -n "${UVICORN_PID}" ]]; then
    local children=""
    children="$(pgrep -P "${UVICORN_PID}" 2>/dev/null || true)"
    if kill -0 "${UVICORN_PID}" 2>/dev/null; then
      # shellcheck disable=SC2086
      kill ${UVICORN_PID} ${children} 2>/dev/null || true
      local i
      for i in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "${UVICORN_PID}" 2>/dev/null || break
        sleep 0.1
      done
      if kill -0 "${UVICORN_PID}" 2>/dev/null; then
        # shellcheck disable=SC2086
        kill -9 ${UVICORN_PID} ${children} 2>/dev/null || true
      fi
      wait "${UVICORN_PID}" 2>/dev/null || true
    fi
  fi
  if [[ "${rc}" -ne 0 ]]; then
    log "verify: FAILED (exit ${rc}). evidence kept at ${ART}"
  fi
  return "${rc}"
}

trap cleanup EXIT INT TERM

log "verify: artifacts -> ${ART}"
write summary.txt "timestamp ${TS}"

log "verify: uv sync --frozen"
uv sync --frozen

PORT="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
write port "${PORT}"
log "verify: launching uvicorn on 127.0.0.1:${PORT}"

export SEC_USER_AGENT="${SEC_USER_AGENT:-capitalcite-verify you@example.com}"
export APP_ENV="${APP_ENV:-verify}"
export LOG_LEVEL="${LOG_LEVEL:-WARNING}"

uv run uvicorn capitalcite.api:app --host 127.0.0.1 --port "${PORT}" \
  >"${ART}/uvicorn.log" 2>&1 &
UVICORN_PID=$!
write uvicorn.pid "${UVICORN_PID}"

ready=0
i=0
while [[ "${i}" -lt 60 ]]; do
  if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
    log "verify: uvicorn pid ${UVICORN_PID} exited before ready"
    cat "${ART}/uvicorn.log" >&2 || true
    exit 1
  fi
  if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  i=$((i + 1))
  sleep 0.25
done

if [[ "${ready}" -ne 1 ]]; then
  log "verify: timed out waiting for GET /health on port ${PORT}"
  cat "${ART}/uvicorn.log" >&2 || true
  exit 1
fi

log "verify: doctor GET /health"
set +e
health_http="$(
  curl -sS -D "${ART}/health.headers" -o "${ART}/health.body.json" \
    -w "%{http_code}" \
    "http://127.0.0.1:${PORT}/health"
)"
health_curl_rc=$?
set -e
write health.exit "${health_curl_rc}"
write health.http "${health_http}"
if [[ "${health_curl_rc}" -ne 0 ]]; then
  log "verify: curl /health failed with exit ${health_curl_rc}"
  exit 1
fi
if [[ "${health_http}" != "200" ]]; then
  log "verify: GET /health HTTP ${health_http}"
  exit 1
fi

uv run python - "${ART}/health.body.json" <<'PY'
import json
import sys
from pathlib import Path

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
errors = []
if body.get("status") != "ok":
    errors.append(f"status={body.get('status')!r}")
if body.get("service") != "capitalcite":
    errors.append(f"service={body.get('service')!r}")
version = body.get("version")
if not isinstance(version, str) or not version.strip():
    errors.append(f"version={version!r}")
if errors:
    sys.stderr.write("verify: /health JSON failed: " + ", ".join(errors) + "\n")
    sys.exit(1)
print(json.dumps(body, sort_keys=True))
PY

log "verify: doctor passed (/health JSON ok)"

log "verify: capitalcite version"
set +e
uv run capitalcite version >"${ART}/version.out" 2>"${ART}/version.err"
version_rc=$?
set -e
write version.exit "${version_rc}"
if [[ "${version_rc}" -ne 0 ]]; then
  log "verify: capitalcite version failed"
  cat "${ART}/version.err" >&2 || true
  exit 1
fi

EXPECTED_VERSION="$(uv run python -c 'from importlib.metadata import version; print(version("capitalcite"))')"
ACTUAL_VERSION="$(tr -d '[:space:]' <"${ART}/version.out")"
write expected.version "${EXPECTED_VERSION}"
if [[ "${ACTUAL_VERSION}" != "${EXPECTED_VERSION}" ]]; then
  log "verify: version mismatch: cli=${ACTUAL_VERSION} metadata=${EXPECTED_VERSION}"
  exit 1
fi
log "verify: capitalcite version ${ACTUAL_VERSION}"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "verify: docker compose config"
  set +e
  docker compose config >"${ART}/compose.config.out" 2>"${ART}/compose.config.err"
  compose_rc=$?
  set -e
  write compose.config.exit "${compose_rc}"
  if [[ "${compose_rc}" -ne 0 ]]; then
    log "verify: docker compose config failed"
    cat "${ART}/compose.config.err" >&2 || true
    exit 1
  fi
  uv run python - "${ART}/compose.config.out" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
errors = []
for needle in (
    "docker.langfuse.com/langfuse/langfuse:4.59.0",
    "docker.langfuse.com/langfuse/langfuse-worker:4.59.0",
):
    if needle not in text:
        errors.append(f"missing {needle}")
if re.search(r"langfuse/langfuse:4\s*$", text, re.M) or re.search(
    r"langfuse/langfuse-worker:4\s*$", text, re.M
):
    errors.append("floating :4 tag still present")
# Long-form compose config uses host_ip + published, not "127.0.0.1:6333:6333".
for published in ("6333", "6334", "9090"):
    block = re.search(
        rf"host_ip:\s*127\.0\.0\.1\s*\n\s*published:\s*[\"']?{published}[\"']?",
        text,
    ) or re.search(
        rf"published:\s*[\"']?{published}[\"']?\s*\n\s*host_ip:\s*127\.0\.0\.1",
        text,
    )
    if not block:
        errors.append(f"127.0.0.1 bind missing for published port {published}")
if errors:
    sys.stderr.write("verify: compose config checks failed: " + "; ".join(errors) + "\n")
    sys.exit(1)
PY
  log "verify: docker compose config ok"

  set +e
  qdrant_http="$(
    curl -sS -o "${ART}/qdrant.health.out" -w "%{http_code}" \
      --connect-timeout 1 --max-time 2 \
      "http://127.0.0.1:6333/healthz" 2>"${ART}/qdrant.health.err"
  )"
  qdrant_rc=$?
  set -e
  write qdrant.health.exit "${qdrant_rc}"
  write qdrant.health.http "${qdrant_http:-}"
  if [[ "${qdrant_rc}" -eq 0 && "${qdrant_http}" == "200" ]]; then
    log "verify: Qdrant /healthz ok"
  else
    write qdrant.skipped "nothing healthy on 127.0.0.1:6333 (curl_exit=${qdrant_rc} http=${qdrant_http:-})"
    log "verify: Qdrant not listening; skipped"
  fi
else
  write compose.skipped "docker or docker compose not available"
  write qdrant.skipped "docker or docker compose not available"
  log "verify: Docker not available; skipped compose and Qdrant"
fi

{
  echo "timestamp ${TS}"
  echo "port ${PORT}"
  echo "uvicorn.pid ${UVICORN_PID}"
  echo "health.http $(cat "${ART}/health.http")"
  echo "health.exit $(cat "${ART}/health.exit")"
  echo "version ${ACTUAL_VERSION}"
  echo "version.exit $(cat "${ART}/version.exit")"
  if [[ -f "${ART}/compose.config.exit" ]]; then
    echo "compose.config.exit $(cat "${ART}/compose.config.exit")"
  else
    echo "compose skipped ($(cat "${ART}/compose.skipped"))"
  fi
  if [[ -f "${ART}/qdrant.skipped" ]]; then
    echo "qdrant skipped ($(cat "${ART}/qdrant.skipped"))"
  else
    echo "qdrant.health.exit $(cat "${ART}/qdrant.health.exit")"
  fi
  echo "artifacts ${ART}"
} | tee "${ART}/summary.txt"

log "verify: OK"
