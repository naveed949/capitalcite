#!/usr/bin/env bash
# Drive capitalcite the way a user does: live uvicorn + CLI.
# Usage (repo root): ./scripts/verify.sh [--small]
# Evidence: artifacts/verify/<UTC-timestamp>/  (gitignored)
# Cleanup kills only the uvicorn and Next process this script started; evidence is kept.
# --small skips live EDGAR and indexes the fixture chunks for /ask/stream.

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

SMALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --small)
      SMALL=1
      shift
      ;;
    *)
      echo "verify: unknown argument: $1 (supported: --small)" >&2
      exit 1
      ;;
  esac
done

TS="$(date -u +%Y%m%dT%H%M%SZ)"
ART="${ROOT}/artifacts/verify/${TS}"
mkdir -p "$ART"

UVICORN_PID=""
LIMIT_PID=""
NEXT_PID=""

log() {
  printf '%s\n' "$*"
}

write() {
  local file="$1"
  shift
  printf '%s\n' "$*" >"${ART}/${file}"
}

# capitalcite check and eval --suite metrics need the fixture warehouse.
# Build once; later warehouse steps reuse the same dbt.out.
build_ci_warehouse() {
  export DBT_PROJECT_DIR="${ROOT}/dbt"
  if [[ -f "${ART}/dbt.exit" && -f "${ROOT}/dbt/target/ci.duckdb" ]]; then
    log "verify: dbt build --target ci already ran"
    return 0
  fi
  log "verify: dbt build --target ci"
  set +e
  uv run --frozen --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci \
    >"${ART}/dbt.out" 2>"${ART}/dbt.err"
  dbt_rc=$?
  set -e
  write dbt.exit "${dbt_rc}"
  if [[ "${dbt_rc}" -ne 0 ]] || ! grep -q "Completed successfully" "${ART}/dbt.out"; then
    log "verify: dbt build --target ci failed"
    cat "${ART}/dbt.out" >&2 || true
    cat "${ART}/dbt.err" >&2 || true
    exit 1
  fi
  log "verify: dbt build --target ci ok"
  cat "${ART}/dbt.out"
}

# Typer/Rich may insert SGR codes between the dashes in "--against-raw".
plain() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g' "$1"
}

stop_pid() {
  local pid="$1"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  local children=""
  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  if kill -0 "${pid}" 2>/dev/null; then
    # shellcheck disable=SC2086
    kill ${pid} ${children} 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "${pid}" 2>/dev/null; then
      # shellcheck disable=SC2086
      kill -9 ${pid} ${children} 2>/dev/null || true
    fi
    wait "${pid}" 2>/dev/null || true
  fi
}

stop_main_uvicorn() {
  stop_pid "${UVICORN_PID}"
  UVICORN_PID=""
}

start_main_uvicorn() {
  uv run uvicorn capitalcite.api:app --host 127.0.0.1 --port "${PORT}" \
    >>"${ART}/uvicorn.log" 2>&1 &
  UVICORN_PID=$!
  write uvicorn.pid "${UVICORN_PID}"
  local ready=0
  local i=0
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
}

kill_tree() {
  local pid="$1"
  local child
  local children=""
  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  for child in ${children}; do
    kill_tree "${child}"
  done
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
  fi
}

stop_next() {
  if [[ -z "${NEXT_PID}" ]]; then
    return 0
  fi
  kill_tree "${NEXT_PID}"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "${NEXT_PID}" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "${NEXT_PID}" 2>/dev/null; then
    kill -9 "${NEXT_PID}" 2>/dev/null || true
    local leftover=""
    leftover="$(pgrep -P "${NEXT_PID}" 2>/dev/null || true)"
    if [[ -n "${leftover}" ]]; then
      # shellcheck disable=SC2086
      kill -9 ${leftover} 2>/dev/null || true
    fi
  fi
  wait "${NEXT_PID}" 2>/dev/null || true
  NEXT_PID=""
}

cleanup() {
  local rc=$?
  stop_next
  stop_pid "${UVICORN_PID}"
  stop_pid "${LIMIT_PID}"
  if [[ "${rc}" -ne 0 ]]; then
    log "verify: FAILED (exit ${rc}). evidence kept at ${ART}"
  fi
  return "${rc}"
}

trap cleanup EXIT INT TERM

log "verify: artifacts -> ${ART}"
write summary.txt "timestamp ${TS}"
write small "${SMALL}"
log "verify: small=${SMALL}"

log "verify: uv sync --frozen"
uv sync --frozen

log "verify: write-up numbers"
set +e
uv run python "${ROOT}/scripts/check_writeup_numbers.py" \
  >"${ART}/writeup-numbers.out" 2>"${ART}/writeup-numbers.err"
writeup_numbers_rc=$?
set -e
write writeup-numbers.exit "${writeup_numbers_rc}"
if [[ "${writeup_numbers_rc}" -ne 0 ]]; then
  log "verify: write-up numbers failed"
  cat "${ART}/writeup-numbers.out" >&2 || true
  cat "${ART}/writeup-numbers.err" >&2 || true
  exit 1
fi
log "verify: write-up numbers ok"
cat "${ART}/writeup-numbers.out"

PORT="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
write port "${PORT}"
log "verify: launching uvicorn on 127.0.0.1:${PORT}"

export SEC_USER_AGENT="${SEC_USER_AGENT:-capitalcite-verify you@example.com}"
export APP_ENV="${APP_ENV:-verify}"
export LOG_LEVEL="${LOG_LEVEL:-WARNING}"
# Typer/Rich splits "--against-raw" with SGR codes; grep needs the literal flag.
export NO_COLOR="${NO_COLOR:-1}"
export EMBEDDER="${EMBEDDER:-bge-small}"
# The openai probe sends "model":"openai" in the body. This server stays on fake.
export ASK_MODEL=fake
QDRANT_STORE="${ART}/qdrant"
mkdir -p "${QDRANT_STORE}"
export QDRANT_PATH="${QDRANT_STORE}"
write qdrant.path "${QDRANT_PATH}"
export LANGFUSE_TRACING_ENABLED=false
export CAPITALCITE_TRACE_FILE="${ART}/spans.jsonl"
write capitalcite.trace.file "${CAPITALCITE_TRACE_FILE}"
export AUDIT_LOG_PATH="${ART}/audit.live.jsonl"
write audit.path "${AUDIT_LOG_PATH}"
export CORS_ORIGINS="https://verify.example"
write cors.origins "${CORS_ORIGINS}"

start_main_uvicorn

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
git_sha = body.get("git_sha")
if not isinstance(git_sha, str) or not git_sha.strip():
    errors.append(f"git_sha={git_sha!r}")
point_count = body.get("point_count")
if isinstance(point_count, bool) or not isinstance(point_count, int) or point_count < 0:
    errors.append(f"point_count={point_count!r}")
if errors:
    sys.stderr.write("verify: /health JSON failed: " + ", ".join(errors) + "\n")
    sys.exit(1)
print(json.dumps(body, sort_keys=True))
PY

log "verify: doctor passed (/health JSON ok)"

log "verify: CORS allowed and denied origins"
set +e
curl -sS -D "${ART}/cors.allowed.headers" -o "${ART}/cors.allowed.body" \
  -H "Origin: https://verify.example" \
  "http://127.0.0.1:${PORT}/health" \
  >"${ART}/cors.allowed.curl.err" 2>&1
cors_allowed_rc=$?
curl -sS -D "${ART}/cors.denied.headers" -o "${ART}/cors.denied.body" \
  -H "Origin: https://denied.example" \
  "http://127.0.0.1:${PORT}/health" \
  >"${ART}/cors.denied.curl.err" 2>&1
cors_denied_rc=$?
set -e
write cors.allowed.exit "${cors_allowed_rc}"
write cors.denied.exit "${cors_denied_rc}"
if [[ "${cors_allowed_rc}" -ne 0 || "${cors_denied_rc}" -ne 0 ]]; then
  log "verify: CORS curl failed"
  exit 1
fi
uv run python - "${ART}/cors.allowed.headers" "${ART}/cors.denied.headers" <<'PY'
import sys
from pathlib import Path


def origin(path: str) -> str | None:
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith("access-control-allow-origin:"):
            return line.split(":", 1)[1].strip()
    return None


allowed = origin(sys.argv[1])
denied = origin(sys.argv[2])
errors = []
if allowed != "https://verify.example":
    errors.append(f"allowed={allowed!r}")
if denied is not None:
    errors.append(f"denied={denied!r}")
if errors:
    sys.stderr.write("verify: CORS failed: " + ", ".join(errors) + "\n")
    sys.exit(1)
print(f"allowed={allowed}")
PY
log "verify: CORS ok"

log "verify: ask rate limit on a second uvicorn"
LIMIT_PORT="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
write limit.port "${LIMIT_PORT}"
LIMIT_QDRANT="${ART}/qdrant-limit"
mkdir -p "${LIMIT_QDRANT}"
ASK_RATE_LIMIT=2 \
  ASK_RATE_WINDOW_S=60 \
  QDRANT_PATH="${LIMIT_QDRANT}" \
  AUDIT_LOG_PATH="${ART}/audit.limit.jsonl" \
  uv run uvicorn capitalcite.api:app --host 127.0.0.1 --port "${LIMIT_PORT}" \
  >"${ART}/limit.uvicorn.log" 2>&1 &
LIMIT_PID=$!
write limit.pid "${LIMIT_PID}"
limit_ready=0
i=0
while [[ "${i}" -lt 40 ]]; do
  if ! kill -0 "${LIMIT_PID}" 2>/dev/null; then
    log "verify: rate-limit uvicorn exited before ready"
    cat "${ART}/limit.uvicorn.log" >&2 || true
    exit 1
  fi
  if curl -sf "http://127.0.0.1:${LIMIT_PORT}/health" >/dev/null 2>&1; then
    limit_ready=1
    break
  fi
  i=$((i + 1))
  sleep 0.25
done
if [[ "${limit_ready}" -ne 1 ]]; then
  log "verify: timed out waiting for rate-limit /health"
  cat "${ART}/limit.uvicorn.log" >&2 || true
  exit 1
fi
limit_payload='{"question":"What is the tier 1 capital buffer?","model":"fake"}'
for n in 1 2 3 4; do
  code="$(curl -sS -o "${ART}/limit.health.${n}.body" -w '%{http_code}' \
    "http://127.0.0.1:${LIMIT_PORT}/health")"
  write "limit.health.${n}.http" "${code}"
  if [[ "${code}" != "200" ]]; then
    log "verify: limited server /health ${n} returned ${code}"
    exit 1
  fi
done
for n in 1 2 3; do
  code="$(curl -sS -D "${ART}/limit.ask.${n}.headers" -o "${ART}/limit.ask.${n}.body" \
    -w '%{http_code}' \
    -X POST "http://127.0.0.1:${LIMIT_PORT}/ask" \
    -H 'content-type: application/json' \
    -d "${limit_payload}")"
  write "limit.ask.${n}.http" "${code}"
done
if [[ "$(cat "${ART}/limit.ask.1.http")" == "429" || "$(cat "${ART}/limit.ask.2.http")" == "429" ]]; then
  log "verify: ask rate limit tripped before the third request"
  exit 1
fi
if [[ "$(cat "${ART}/limit.ask.3.http")" != "429" ]]; then
  log "verify: third POST /ask was $(cat "${ART}/limit.ask.3.http"), expected 429"
  exit 1
fi
code="$(curl -sS -D "${ART}/limit.stream.headers" -o "${ART}/limit.stream.body" \
  -w '%{http_code}' \
  -X POST "http://127.0.0.1:${LIMIT_PORT}/ask/stream" \
  -H 'content-type: application/json' \
  -d "${limit_payload}")"
write limit.stream.http "${code}"
if [[ "${code}" != "429" ]]; then
  log "verify: POST /ask/stream was ${code}, expected 429"
  exit 1
fi
uv run python - "${ART}/limit.ask.3.headers" "${ART}/limit.ask.3.body" \
  "${ART}/limit.stream.headers" "${ART}/limit.stream.body" <<'PY'
import json
import sys
from pathlib import Path


def header(path: str, name: str) -> str | None:
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return None


errors = []
for label, headers, body in (
    ("ask", sys.argv[1], sys.argv[2]),
    ("stream", sys.argv[3], sys.argv[4]),
):
    retry = header(headers, "retry-after")
    content_type = header(headers, "content-type") or ""
    if retry is None or not retry.isdigit() or int(retry) < 1:
        errors.append(f"{label} retry-after={retry!r}")
    if "text/event-stream" in content_type.lower():
        errors.append(f"{label} content-type={content_type!r}")
    payload = json.loads(Path(body).read_text(encoding="utf-8"))
    detail = payload.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        errors.append(f"{label} detail={detail!r}")
if errors:
    sys.stderr.write("verify: rate limit failed: " + ", ".join(errors) + "\n")
    sys.exit(1)
print("rate limit 429 json ok")
PY
after="$(curl -sS -o "${ART}/limit.health.after.body" -w '%{http_code}' \
  "http://127.0.0.1:${LIMIT_PORT}/health")"
write limit.health.after.http "${after}"
if [[ "${after}" != "200" ]]; then
  log "verify: /health after the limit returned ${after}"
  exit 1
fi
stop_pid "${LIMIT_PID}"
LIMIT_PID=""
log "verify: ask rate limit ok"

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

log "verify: packaged capitalcite --help"
PKG_VENV="${ART}/packaged-venv"
uv venv "${PKG_VENV}"
uv pip install --python "${PKG_VENV}/bin/python" "${ROOT}"
set +e
(
  cd /tmp
  "${PKG_VENV}/bin/capitalcite" --help
) >"${ART}/packaged.help.out" 2>"${ART}/packaged.help.err"
packaged_help_rc=$?
set -e
write packaged.help.exit "${packaged_help_rc}"
if [[ "${packaged_help_rc}" -ne 0 ]]; then
  log "verify: packaged capitalcite --help failed"
  cat "${ART}/packaged.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/packaged.help.out" | grep -q "ingest" \
  || ! plain "${ART}/packaged.help.out" | grep -q "metrics"; then
  log "verify: packaged --help missing ingest or metrics"
  cat "${ART}/packaged.help.out" >&2 || true
  exit 1
fi
log "verify: packaged capitalcite --help ok"

log "verify: capitalcite golden validate --help"
set +e
uv run capitalcite golden validate --help >"${ART}/golden.help.out" 2>"${ART}/golden.help.err"
golden_help_rc=$?
set -e
write golden.help.exit "${golden_help_rc}"
if [[ "${golden_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite golden validate --help failed"
  cat "${ART}/golden.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/golden.help.out" | grep -q -- "--path" \
  || ! plain "${ART}/golden.help.out" | grep -q -- "--against-raw"; then
  log "verify: golden validate --help missing --path or --against-raw"
  cat "${ART}/golden.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite golden validate --help ok"

log "verify: uv run capitalcite golden validate --path evals/golden"
set +e
uv run capitalcite golden validate --path evals/golden \
  >"${ART}/golden.out" 2>"${ART}/golden.err"
golden_rc=$?
set -e
write golden.exit "${golden_rc}"
if [[ "${golden_rc}" -ne 0 ]]; then
  log "verify: capitalcite golden validate failed"
  cat "${ART}/golden.out" >&2 || true
  cat "${ART}/golden.err" >&2 || true
  exit 1
fi
if ! grep -q "items=" "${ART}/golden.out" \
  || ! grep -q "unanswerable_or_trick_pct=" "${ART}/golden.out" \
  || ! grep -q "numeric_pct=" "${ART}/golden.out"; then
  log "verify: golden validate stdout missing mix counts"
  cat "${ART}/golden.out" >&2 || true
  exit 1
fi
log "verify: capitalcite golden validate ok"
cat "${ART}/golden.out"

log "verify: capitalcite gate --help"
set +e
uv run capitalcite gate --help >"${ART}/gate.help.out" 2>"${ART}/gate.help.err"
gate_help_rc=$?
set -e
write gate.help.exit "${gate_help_rc}"
if [[ "${gate_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite gate --help failed"
  cat "${ART}/gate.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/gate.help.out" | grep -q "validate" \
  || ! plain "${ART}/gate.help.out" | grep -q "decide" \
  || ! plain "${ART}/gate.help.out" | grep -q "redteam" \
  || ! plain "${ART}/gate.help.out" | grep -q "bench"; then
  log "verify: gate --help missing validate, decide, redteam, or bench"
  cat "${ART}/gate.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite gate --help ok"

log "verify: capitalcite gate validate"
set +e
uv run capitalcite gate validate >"${ART}/gate.validate.out" 2>"${ART}/gate.validate.err"
gate_validate_rc=$?
set -e
write gate.validate.exit "${gate_validate_rc}"
if [[ "${gate_validate_rc}" -ne 0 ]]; then
  log "verify: capitalcite gate validate failed"
  cat "${ART}/gate.validate.out" >&2 || true
  cat "${ART}/gate.validate.err" >&2 || true
  exit 1
fi
if ! grep -q "ok tools=" "${ART}/gate.validate.out" \
  || ! grep -q "calculator" "${ART}/gate.validate.out"; then
  log "verify: gate validate stdout missing ok tools="
  cat "${ART}/gate.validate.out" >&2 || true
  exit 1
fi
log "verify: capitalcite gate validate ok"
cat "${ART}/gate.validate.out"

log "verify: capitalcite gate decide unknown tool"
set +e
uv run capitalcite gate decide --tool shell --args '{"cmd":"ls"}' \
  >"${ART}/gate.decide.unknown.body.json" 2>"${ART}/gate.decide.unknown.err"
gate_unknown_rc=$?
set -e
write gate.decide.unknown.exit "${gate_unknown_rc}"
if [[ "${gate_unknown_rc}" -ne 0 ]]; then
  log "verify: gate decide unknown failed"
  cat "${ART}/gate.decide.unknown.err" >&2 || true
  exit 1
fi

log "verify: capitalcite gate decide invalid args"
set +e
uv run capitalcite gate decide --tool calculator --args '{"expression":"1+1","cmd":"id"}' \
  >"${ART}/gate.decide.invalid.body.json" 2>"${ART}/gate.decide.invalid.err"
gate_invalid_rc=$?
set -e
write gate.decide.invalid.exit "${gate_invalid_rc}"
if [[ "${gate_invalid_rc}" -ne 0 ]]; then
  log "verify: gate decide invalid failed"
  cat "${ART}/gate.decide.invalid.err" >&2 || true
  exit 1
fi

GATE_BROKEN="${ART}/gate.broken.json"
printf '%s\n' '{' >"${GATE_BROKEN}"
log "verify: capitalcite gate decide broken policy"
set +e
uv run capitalcite gate decide --policy "${GATE_BROKEN}" --tool calculator --args '{"expression":"1+1"}' \
  >"${ART}/gate.decide.broken.body.json" 2>"${ART}/gate.decide.broken.err"
gate_broken_rc=$?
set -e
write gate.decide.broken.exit "${gate_broken_rc}"
if [[ "${gate_broken_rc}" -ne 0 ]]; then
  log "verify: gate decide broken policy failed"
  cat "${ART}/gate.decide.broken.err" >&2 || true
  exit 1
fi

uv run python - "${ART}" <<'PY'
import json
import sys
from pathlib import Path

art = Path(sys.argv[1])
errors = []

def load(name: str) -> dict:
    return json.loads((art / f"{name}.body.json").read_text(encoding="utf-8"))

unknown = load("gate.decide.unknown")
if unknown.get("kind") != "deny" or unknown.get("reason") != "unknown_tool":
    errors.append(f"unknown: {unknown!r}")
invalid = load("gate.decide.invalid")
if invalid.get("kind") != "deny" or invalid.get("reason") != "invalid_args":
    errors.append(f"invalid: {invalid!r}")
broken = load("gate.decide.broken")
if broken.get("kind") != "deny" or broken.get("reason") != "policy_load_failed":
    errors.append(f"broken: {broken!r}")
if errors:
    sys.stderr.write("verify: gate decide checks failed: " + "; ".join(errors) + "\n")
    sys.exit(1)
print("gate decide unknown/invalid/broken ok")
PY

log "verify: gate decide spans"
uv run python - "${ART}/spans.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.stat().st_size == 0:
    sys.stderr.write("verify: spans.jsonl missing after gate decide\n")
    sys.exit(1)
records = [
    json.loads(line)
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
gates = [row for row in records if row.get("name") == "gate"]
if len(gates) < 3:
    sys.stderr.write(f"verify: expected >=3 gate spans, got {len(gates)}\n")
    sys.exit(1)
reasons = {
    str((row.get("attributes") or {}).get("capitalcite.gate.reason")) for row in gates
}
tools = {str((row.get("attributes") or {}).get("capitalcite.tool")) for row in gates}
decisions = {
    str((row.get("attributes") or {}).get("capitalcite.gate.decision")) for row in gates
}
missing = []
if "unknown_tool" not in reasons:
    missing.append("unknown_tool")
if "invalid_args" not in reasons:
    missing.append("invalid_args")
if "policy_load_failed" not in reasons:
    missing.append("policy_load_failed")
if "shell" not in tools or "calculator" not in tools:
    missing.append(f"tools={sorted(tools)}")
if decisions != {"deny"}:
    missing.append(f"decisions={sorted(decisions)}")
if missing:
    sys.stderr.write("verify: gate spans missing " + ", ".join(missing) + "\n")
    sys.exit(1)
print(f"gate spans n={len(gates)} deny reasons={sorted(reasons)}")
PY
log "verify: gate decide spans tagged tool, deny, and reason"

log "verify: capitalcite gate redteam"
set +e
uv run capitalcite gate redteam \
  >"${ART}/gate.redteam.body.json" 2>"${ART}/gate.redteam.err"
gate_redteam_rc=$?
set -e
write gate.redteam.exit "${gate_redteam_rc}"
if [[ "${gate_redteam_rc}" -ne 0 ]]; then
  log "verify: capitalcite gate redteam failed"
  cat "${ART}/gate.redteam.body.json" >&2 || true
  cat "${ART}/gate.redteam.err" >&2 || true
  exit 1
fi
uv run python - "${ART}/gate.redteam.body.json" <<'PY'
import json
import sys
from pathlib import Path

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if body.get("failed") != 0:
    sys.stderr.write(f"verify: gate redteam failed={body.get('failed')!r}\n")
    sys.exit(1)
if body.get("passed", 0) < 5:
    sys.stderr.write(f"verify: gate redteam passed={body.get('passed')!r}\n")
    sys.exit(1)
print(json.dumps(body, sort_keys=True))
PY
log "verify: capitalcite gate redteam ok"

log "verify: capitalcite gate bench --n 2000"
set +e
uv run capitalcite gate bench --n 2000 \
  >"${ART}/gate.bench.body.json" 2>"${ART}/gate.bench.err"
gate_bench_rc=$?
set -e
write gate.bench.exit "${gate_bench_rc}"
if [[ "${gate_bench_rc}" -ne 0 ]]; then
  log "verify: capitalcite gate bench failed"
  cat "${ART}/gate.bench.err" >&2 || true
  exit 1
fi
uv run python - "${ART}/gate.bench.body.json" <<'PY'
import json
import sys
from pathlib import Path

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
errors = []
if not isinstance(body.get("p95_ms"), (int, float)):
    errors.append(f"p95_ms={body.get('p95_ms')!r}")
if not isinstance(body.get("p50_ms"), (int, float)):
    errors.append(f"p50_ms={body.get('p50_ms')!r}")
if body.get("p95_ms", 999) >= 50:
    errors.append(f"p95_ms {body.get('p95_ms')} >= 50ms ceiling")
if errors:
    sys.stderr.write("verify: gate bench checks failed: " + "; ".join(errors) + "\n")
    sys.exit(1)
print(json.dumps(body, sort_keys=True))
PY
log "verify: capitalcite gate bench ok"
cat "${ART}/gate.bench.body.json"
log "verify: capitalcite audit verify --help"
set +e
uv run capitalcite audit verify --help >"${ART}/audit.help.out" 2>"${ART}/audit.help.err"
audit_help_rc=$?
set -e
write audit.help.exit "${audit_help_rc}"
if [[ "${audit_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite audit verify --help failed"
  cat "${ART}/audit.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/audit.help.out" | grep -q -- "--path"; then
  log "verify: audit verify --help missing --path"
  cat "${ART}/audit.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite audit verify --help ok"

log "verify: write audit fixture and capitalcite audit verify"
uv run python - "${ART}/audit.fixture.jsonl" <<'PY'
from pathlib import Path
import sys

from capitalcite.agent.audited import AuditingTools
from capitalcite.agent.tools import ToolRegistry
from capitalcite.agent.types import ToolResult
from capitalcite.audit import AuditLog
from capitalcite.gate import Gate, load_policy

path = Path(sys.argv[1])


async def add(args: dict[str, object]) -> ToolResult:
    return ToolResult(ok=True, tool="calculator", args=dict(args), detail="2")


async def main() -> None:
    tools = AuditingTools(
        Gate(load_policy(None), ToolRegistry({"calculator": add})),
        AuditLog(path),
        "verify-trace",
    )
    await tools.execute("calculator", {"expression": "1+1"})
    await tools.execute("shell", {"cmd": "ls"})


import asyncio

asyncio.run(main())
print(f"wrote {path}")
PY

set +e
uv run capitalcite audit verify --path "${ART}/audit.fixture.jsonl" \
  >"${ART}/audit.ok.out" 2>"${ART}/audit.ok.err"
audit_ok_rc=$?
set -e
write audit.ok.exit "${audit_ok_rc}"
if [[ "${audit_ok_rc}" -ne 0 ]]; then
  log "verify: capitalcite audit verify failed on intact chain"
  cat "${ART}/audit.ok.out" >&2 || true
  cat "${ART}/audit.ok.err" >&2 || true
  exit 1
fi
if ! grep -q "audit ok records=2" "${ART}/audit.ok.out"; then
  log "verify: audit verify stdout missing audit ok records=2"
  cat "${ART}/audit.ok.out" >&2 || true
  exit 1
fi
log "verify: capitalcite audit verify intact chain ok"
cat "${ART}/audit.ok.out"

log "verify: tamper audit fixture and expect verify fail"
cp "${ART}/audit.fixture.jsonl" "${ART}/audit.tampered.jsonl"
cp "${ART}/audit.fixture.jsonl.tip" "${ART}/audit.tampered.jsonl.tip"
uv run python - "${ART}/audit.tampered.jsonl" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("calculator", "nope", 1), encoding="utf-8")
PY
set +e
uv run capitalcite audit verify --path "${ART}/audit.tampered.jsonl" \
  >"${ART}/audit.tamper.out" 2>"${ART}/audit.tamper.err"
audit_tamper_rc=$?
set -e
write audit.tamper.exit "${audit_tamper_rc}"
if [[ "${audit_tamper_rc}" -eq 0 ]]; then
  log "verify: audit verify exited 0 on a tampered log"
  cat "${ART}/audit.tamper.out" >&2 || true
  exit 1
fi
if ! grep -q "audit fail" "${ART}/audit.tamper.err" "${ART}/audit.tamper.out"; then
  log "verify: tampered audit verify did not print audit fail"
  cat "${ART}/audit.tamper.out" >&2 || true
  cat "${ART}/audit.tamper.err" >&2 || true
  exit 1
fi
log "verify: capitalcite audit verify detected tamper"
cat "${ART}/audit.tamper.err" || true

log "verify: drop last audit record and expect verify fail"
cp "${ART}/audit.fixture.jsonl" "${ART}/audit.truncated.jsonl"
cp "${ART}/audit.fixture.jsonl.tip" "${ART}/audit.truncated.jsonl.tip"
uv run python - "${ART}/audit.truncated.jsonl" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(lines) < 2:
    raise SystemExit("verify: fixture must have at least two records")
path.write_text(lines[0] + "\n", encoding="utf-8")
PY
set +e
uv run capitalcite audit verify --path "${ART}/audit.truncated.jsonl" \
  >"${ART}/audit.removed.out" 2>"${ART}/audit.removed.err"
audit_removed_rc=$?
set -e
write audit.removed.exit "${audit_removed_rc}"
if [[ "${audit_removed_rc}" -eq 0 ]]; then
  log "verify: audit verify exited 0 after deleting the last record"
  cat "${ART}/audit.removed.out" >&2 || true
  exit 1
fi
if ! grep -q "audit fail" "${ART}/audit.removed.err" "${ART}/audit.removed.out"; then
  log "verify: truncated audit verify did not print audit fail"
  cat "${ART}/audit.removed.out" >&2 || true
  cat "${ART}/audit.removed.err" >&2 || true
  exit 1
fi
if ! grep -q "tip hash mismatch" "${ART}/audit.removed.err" "${ART}/audit.removed.out"; then
  log "verify: truncated audit verify did not report tip hash mismatch"
  cat "${ART}/audit.removed.out" >&2 || true
  cat "${ART}/audit.removed.err" >&2 || true
  exit 1
fi
log "verify: capitalcite audit verify detected removed last record"
cat "${ART}/audit.removed.err" || true

log "verify: capitalcite ingest --help"
set +e
uv run capitalcite ingest --help >"${ART}/ingest.help.out" 2>"${ART}/ingest.help.err"
ingest_help_rc=$?
set -e
write ingest.help.exit "${ingest_help_rc}"
if [[ "${ingest_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite ingest --help failed"
  cat "${ART}/ingest.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/ingest.help.out" | grep -q -- "--tickers" \
  || ! plain "${ART}/ingest.help.out" | grep -q -- "--forms" \
  || ! plain "${ART}/ingest.help.out" | grep -q -- "--years"; then
  log "verify: ingest --help missing required flags"
  cat "${ART}/ingest.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite ingest --help ok"

INGEST_DIR="${ART}/data"
mkdir -p "${INGEST_DIR}"
if [[ "${SMALL}" -eq 1 ]]; then
  write ingest.skipped "--small uses tests/fixtures/chunking/corpus; live EDGAR was not called"
  write golden.against-raw.skipped "--small: --against-raw needs the twelve-filing corpus"
  log "verify: ingest skipped (--small fixture corpus)"
else
INGEST_CMD=(uv run capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K --years 2023-2025 --output-dir "${INGEST_DIR}")
printf '%s\n' "${INGEST_CMD[*]}" >"${ART}/ingest.cmd"
log "verify: ${INGEST_CMD[*]}"
set +e
"${INGEST_CMD[@]}" >"${ART}/ingest.out" 2>"${ART}/ingest.err"
ingest_rc=$?
set -e
write ingest.exit "${ingest_rc}"
if [[ "${ingest_rc}" -eq 2 ]]; then
  write ingest.unreachable "live EDGAR unavailable (exit 2). command: ${INGEST_CMD[*]}"
  log "verify: live EDGAR ingest unreachable (exit 2); not mocked"
  cat "${ART}/ingest.err" >&2 || true
elif [[ "${ingest_rc}" -ne 0 ]]; then
  log "verify: capitalcite ingest failed with exit ${ingest_rc}"
  cat "${ART}/ingest.out" >&2 || true
  cat "${ART}/ingest.err" >&2 || true
  exit 1
else
  uv run python - "${INGEST_DIR}" "${ART}/ingest.files.txt" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
listing = Path(sys.argv[2])
sidecars = sorted(root.glob("raw/edgar/**/metadata.json"))
required = {
    (ticker, period)
    for ticker in ("JPM", "BAC", "WFC", "C")
    for period in ("2023-12-31", "2024-12-31", "2025-12-31")
}
seen: set[tuple[str, str]] = set()
errors: list[str] = []
lines: list[str] = []
for path in sidecars:
    meta = json.loads(path.read_text(encoding="utf-8"))
    ticker = str(meta.get("ticker", ""))
    period = str(meta.get("period", ""))
    form = str(meta.get("form", ""))
    accession = str(meta.get("accession", ""))
    url = str(meta.get("url", ""))
    lines.append(f"{ticker} {form} {period} {accession} {path}")
    if form != "10-K":
        errors.append(f"{path}: form={form!r}")
    if not accession:
        errors.append(f"{path}: missing accession")
    if "sec.gov/Archives/edgar/data/" not in url:
        errors.append(f"{path}: url={url!r}")
    doc = path.with_name(str(meta.get("primary_document", "")))
    if not doc.is_file() or doc.stat().st_size == 0:
        errors.append(f"{path}: missing primary document {doc.name}")
    if ticker == "WFC" and doc.stat().st_size < 5_000_000:
        errors.append(f"{path}: WFC primary {doc.name} is {doc.stat().st_size} bytes (shell?)")
    if ticker == "WFC" and period == "2025-12-31" and doc.name.endswith("_d2.htm"):
        errors.append(f"{path}: WFC FY2025 still using cover-page {doc.name}")
    seen.add((ticker, period))
listing.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
missing = sorted(required - seen)
if missing:
    errors.append("missing " + ", ".join(f"{t} {p}" for t, p in missing))
if len(sidecars) != 12:
    errors.append(f"expected 12 metadata.json files, found {len(sidecars)}")
if errors:
    sys.stderr.write("verify: ingest store checks failed:\n  " + "\n  ".join(errors) + "\n")
    sys.exit(1)
print(f"ingest files {len(sidecars)}")
PY
  log "verify: ingest stored 12 expected 10-K sidecars"

  log "verify: capitalcite ingest rerun (idempotent)"
  set +e
  "${INGEST_CMD[@]}" >"${ART}/ingest.rerun.out" 2>"${ART}/ingest.rerun.err"
  ingest_rerun_rc=$?
  set -e
  write ingest.rerun.exit "${ingest_rerun_rc}"
  if [[ "${ingest_rerun_rc}" -ne 0 ]]; then
    log "verify: ingest rerun failed"
    cat "${ART}/ingest.rerun.err" >&2 || true
    exit 1
  fi
  if ! grep -q "downloaded=0" "${ART}/ingest.rerun.out" || ! grep -q "skipped=12" "${ART}/ingest.rerun.out"; then
    log "verify: ingest rerun was not idempotent"
    cat "${ART}/ingest.rerun.out" >&2 || true
    exit 1
  fi
  log "verify: ingest rerun skipped=12"

  log "verify: capitalcite chunk --input-dir ${INGEST_DIR} --chunker section"
  set +e
  uv run capitalcite chunk --input-dir "${INGEST_DIR}" --output-dir "${INGEST_DIR}" --chunker section \
    >"${ART}/golden.chunk.out" 2>"${ART}/golden.chunk.err"
  golden_chunk_rc=$?
  set -e
  write golden.chunk.exit "${golden_chunk_rc}"
  if [[ "${golden_chunk_rc}" -ne 0 ]]; then
    log "verify: chunk of ingested corpus failed"
    cat "${ART}/golden.chunk.out" >&2 || true
    cat "${ART}/golden.chunk.err" >&2 || true
    exit 1
  fi

  log "verify: uv run capitalcite golden validate --path evals/golden --against-raw ${INGEST_DIR}"
  set +e
  uv run capitalcite golden validate --path evals/golden --against-raw "${INGEST_DIR}" \
    >"${ART}/golden.against-raw.out" 2>"${ART}/golden.against-raw.err"
  against_rc=$?
  set -e
  write golden.against-raw.exit "${against_rc}"
  if [[ "${against_rc}" -ne 0 ]]; then
    log "verify: golden validate --against-raw failed"
    cat "${ART}/golden.against-raw.out" >&2 || true
    cat "${ART}/golden.against-raw.err" >&2 || true
    exit 1
  fi
  if ! grep -q "aligned=" "${ART}/golden.against-raw.out" \
    || ! grep -q "recall=handwritten-only" "${ART}/golden.against-raw.out"; then
    log "verify: golden --against-raw stdout missing alignment counts"
    cat "${ART}/golden.against-raw.out" >&2 || true
    exit 1
  fi
  log "verify: golden validate --against-raw ok"
  cat "${ART}/golden.against-raw.out"
fi
fi

if [[ -f "${ART}/ingest.unreachable" && ! -f "${ART}/golden.against-raw.exit" ]]; then
  write golden.against-raw.skipped "live ingest unreachable; --against-raw needs the corpus"
  log "verify: golden --against-raw UNVERIFIED (no live corpus)"
fi

log "verify: capitalcite chunk --help"
set +e
uv run capitalcite chunk --help >"${ART}/chunk.help.out" 2>"${ART}/chunk.help.err"
chunk_help_rc=$?
set -e
write chunk.help.exit "${chunk_help_rc}"
if [[ "${chunk_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite chunk --help failed"
  cat "${ART}/chunk.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/chunk.help.out" | grep -q -- "--chunker" \
  || ! plain "${ART}/chunk.help.out" | grep -q -- "--size" \
  || ! plain "${ART}/chunk.help.out" | grep -q -- "--overlap"; then
  log "verify: chunk --help missing required flags"
  cat "${ART}/chunk.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite chunk --help ok"

CHUNK_IN="${ROOT}/tests/fixtures/chunking/corpus"
CHUNK_OUT="${ART}/chunk"
mkdir -p "${CHUNK_OUT}"
CHUNK_CMD=(uv run capitalcite chunk --input-dir "${CHUNK_IN}" --output-dir "${CHUNK_OUT}" --chunker section)
printf '%s\n' "${CHUNK_CMD[*]}" >"${ART}/chunk.cmd"
log "verify: ${CHUNK_CMD[*]}"
set +e
"${CHUNK_CMD[@]}" >"${ART}/chunk.out" 2>"${ART}/chunk.err"
chunk_rc=$?
set -e
write chunk.exit "${chunk_rc}"
if [[ "${chunk_rc}" -ne 0 ]]; then
  log "verify: capitalcite chunk failed with exit ${chunk_rc}"
  cat "${ART}/chunk.out" >&2 || true
  cat "${ART}/chunk.err" >&2 || true
  exit 1
fi
uv run python - "${CHUNK_OUT}" "${ART}/chunk.files.txt" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
listing = Path(sys.argv[2])
chunk_paths = sorted(root.glob("parsed/edgar/**/chunks.jsonl"))
errors: list[str] = []
if not chunk_paths:
    listing.write_text("", encoding="utf-8")
    sys.stderr.write("verify: chunk checks failed:\n  missing chunks.jsonl\n")
    sys.exit(1)

table_row = re.compile(r"^\|.+\|$")
table_sep = re.compile(r"^\|\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|$")
required = ("doc_id", "section", "start", "end", "offset", "source_url", "text")
lines: list[str] = []
short_ok = False
long_table_ok = False
total_rows = 0


def markdown_tables(text: str) -> list[str]:
    tables: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if table_row.match(line.strip()):
            current.append(line.strip())
        elif current:
            tables.append("\n".join(current))
            current = []
    if current:
        tables.append("\n".join(current))
    return tables


def valid_table(block: str) -> bool:
    rows = block.splitlines()
    if len(rows) < 3:
        return False
    if table_sep.match(rows[0]) or not table_row.match(rows[0]):
        return False
    if not table_sep.match(rows[1]):
        return False
    return all(table_row.match(row) and row.strip() for row in rows)


for chunks_path in chunk_paths:
    rows = [
        json.loads(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    total_rows += len(rows)
    document_path = chunks_path.with_name("document.md")
    stats_path = chunks_path.with_name("stats.json")
    if not document_path.is_file():
        errors.append(f"{chunks_path}: missing document.md")
        continue
    document = document_path.read_text(encoding="utf-8")
    sections: set[str] = set()
    accession = chunks_path.parent.name
    item7_tables = 0
    for row in rows:
        missing = [key for key in required if key not in row]
        if missing:
            errors.append(f"{chunks_path}: chunk missing {missing}")
            continue
        start = int(row["start"])
        end = int(row["end"])
        offset = int(row["offset"])
        prefix = str(row.get("header_prefix") or "")
        original = document[start:end]
        expected = f"{prefix}{original}" if prefix else original
        if str(row["text"]) != expected:
            errors.append(f"{chunks_path}: text is not document[{start}:{end}]")
        if original.strip():
            leading = len(original) - len(original.lstrip())
            if offset != start + leading:
                errors.append(f"{chunks_path}: offset {offset} is not first non-whitespace")
        elif offset != start:
            errors.append(f"{chunks_path}: empty span offset {offset} != start {start}")
        lines.append(f"{row['doc_id']} {row['section']} {row['offset']} {row['source_url']}")
        sections.add(str(row["section"]))
        if "sec.gov/Archives/edgar/data/" not in str(row["source_url"]):
            errors.append(f"source_url={row['source_url']!r}")
        for table in markdown_tables(str(row["text"])):
            if not valid_table(table):
                errors.append(f"{chunks_path}: invalid markdown table in {row['section']}")
            if row["section"] == "Item 7":
                item7_tables += 1
        if (
            row["section"] == "Item 7"
            and "CET1 ratio" in str(row["text"])
            and "15.1%" in str(row["text"])
        ):
            short_ok = True
    if "Item 7" not in sections:
        errors.append(f"{chunks_path}: missing Item 7 in {sorted(sections)}")
    if "Item 8" not in sections:
        errors.append(f"{chunks_path}: missing Item 8 in {sorted(sections)}")
    if accession == "0000019617-25-000271":
        if item7_tables < 2:
            errors.append(f"{chunks_path}: long table was not split into valid Item 7 pieces")
        if not any("Capital metric 80" in str(row["text"]) for row in rows):
            errors.append(f"{chunks_path}: missing Capital metric 80")
        long_table_ok = item7_tables >= 2
    if not stats_path.is_file():
        errors.append(f"{chunks_path}: missing stats.json")
    else:
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        if stats.get("chunker") != "section":
            errors.append(f"{stats_path}: chunker={stats.get('chunker')!r}")
        if not isinstance(stats.get("count"), int) or stats["count"] < 1:
            errors.append(f"{stats_path}: count={stats.get('count')!r}")
        if not isinstance(stats.get("mean_tokens"), (int, float)) or stats["mean_tokens"] <= 0:
            errors.append(f"{stats_path}: mean_tokens={stats.get('mean_tokens')!r}")

listing.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
if len(chunk_paths) < 2:
    errors.append(f"expected 2 chunks.jsonl files, found {len(chunk_paths)}")
if not short_ok:
    errors.append("Item 7 chunk does not contain CET1 markdown table")
if not long_table_ok:
    errors.append("long-table fixture did not yield valid split Item 7 tables")
if errors:
    sys.stderr.write("verify: chunk checks failed:\n  " + "\n  ".join(errors) + "\n")
    sys.exit(1)
print(f"chunk files {len(chunk_paths)} rows {total_rows}")
PY
log "verify: chunk wrote cited rows with Item 7 table, Item 8, and a split long table"

log "verify: capitalcite mcp --help"
set +e
uv run capitalcite mcp --help >"${ART}/mcp.help.out" 2>"${ART}/mcp.help.err"
mcp_help_rc=$?
set -e
write mcp.help.exit "${mcp_help_rc}"
if [[ "${mcp_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite mcp --help failed"
  cat "${ART}/mcp.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/mcp.help.out" | grep -qi "stdio"; then
  log "verify: mcp --help missing stdio"
  cat "${ART}/mcp.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite mcp --help ok"

MCP_QDRANT="${ART}/mcp-qdrant"
MCP_AUDIT="${ART}/mcp.audit.jsonl"
MCP_SPANS="${ART}/mcp.spans.jsonl"
mkdir -p "${MCP_QDRANT}"
write mcp.qdrant.path "${MCP_QDRANT}"
write mcp.audit.path "${MCP_AUDIT}"
write mcp.spans.path "${MCP_SPANS}"
MCP_INDEX_CMD=(env QDRANT_PATH="${MCP_QDRANT}" uv run capitalcite index --input-dir "${CHUNK_OUT}" --chunker section)
printf '%s\n' "${MCP_INDEX_CMD[*]}" >"${ART}/mcp.index.cmd"
log "verify: ${MCP_INDEX_CMD[*]}"
set +e
"${MCP_INDEX_CMD[@]}" >"${ART}/mcp.index.out" 2>"${ART}/mcp.index.err"
mcp_index_rc=$?
set -e
write mcp.index.exit "${mcp_index_rc}"
if [[ "${mcp_index_rc}" -ne 0 ]]; then
  log "verify: MCP fixture index failed"
  cat "${ART}/mcp.index.out" >&2 || true
  cat "${ART}/mcp.index.err" >&2 || true
  exit 1
fi
log "verify: MCP fixture index ok"

log "verify: official MCP client against capitalcite mcp"
set +e
env \
  QDRANT_PATH="${MCP_QDRANT}" \
  AUDIT_LOG_PATH="${MCP_AUDIT}" \
  CAPITALCITE_TRACE_FILE="${MCP_SPANS}" \
  ASK_MODEL=fake \
  LANGFUSE_TRACING_ENABLED=false \
  uv run python "${ROOT}/scripts/mcp_probe.py" --artifacts "${ART}" --root "${ROOT}" \
  >"${ART}/mcp.probe.out" 2>"${ART}/mcp.probe.err"
mcp_probe_rc=$?
set -e
write mcp.probe.exit "${mcp_probe_rc}"
if [[ "${mcp_probe_rc}" -ne 0 ]]; then
  log "verify: MCP probe failed"
  cat "${ART}/mcp.probe.out" >&2 || true
  cat "${ART}/mcp.probe.err" >&2 || true
  exit 1
fi
if [[ ! -f "${ART}/mcp.tools.json" ]] \
  || [[ ! -f "${ART}/mcp.search.body.json" ]] \
  || [[ ! -f "${ART}/mcp.search.extra.body.json" ]] \
  || [[ ! -f "${ART}/mcp.calculator.body.json" ]] \
  || [[ ! -f "${ART}/mcp.ask.body.json" ]]; then
  log "verify: MCP probe missing JSON bodies"
  exit 1
fi
if [[ ! -f "${MCP_SPANS}" ]]; then
  log "verify: MCP span file missing"
  exit 1
fi
set +e
uv run capitalcite audit verify --path "${MCP_AUDIT}" \
  >"${ART}/mcp.audit.out" 2>"${ART}/mcp.audit.err"
mcp_audit_rc=$?
set -e
write mcp.audit.exit "${mcp_audit_rc}"
if [[ "${mcp_audit_rc}" -ne 0 ]]; then
  log "verify: MCP audit verify failed"
  cat "${ART}/mcp.audit.out" >&2 || true
  cat "${ART}/mcp.audit.err" >&2 || true
  exit 1
fi
if ! grep -q "audit ok records=" "${ART}/mcp.audit.out"; then
  log "verify: MCP audit verify stdout missing records"
  cat "${ART}/mcp.audit.out" >&2 || true
  exit 1
fi
log "verify: MCP stdio client ok"
cat "${ART}/mcp.probe.out"

log "verify: capitalcite index --help"
set +e
uv run capitalcite index --help >"${ART}/index.help.out" 2>"${ART}/index.help.err"
index_help_rc=$?
set -e
write index.help.exit "${index_help_rc}"
if [[ "${index_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite index --help failed"
  cat "${ART}/index.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/index.help.out" | grep -q -- "--embedder" \
  || ! plain "${ART}/index.help.out" | grep -q -- "--input-dir" \
  || ! plain "${ART}/index.help.out" | grep -q -- "--chunker"; then
  log "verify: index --help missing required flags"
  cat "${ART}/index.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite index --help ok"

post_search() {
  local name="$1"
  local body="$2"
  printf '%s\n' "${body}" >"${ART}/${name}.request.json"
  set +e
  local http
  http="$(
    curl -sS --max-time 180 \
      -D "${ART}/${name}.headers" \
      -o "${ART}/${name}.body.json" \
      -w "%{http_code}" \
      -H 'content-type: application/json' \
      -d "${body}" \
      "http://127.0.0.1:${PORT}/search"
  )"
  local rc=$?
  set -e
  write "${name}.exit" "${rc}"
  write "${name}.http" "${http}"
  if [[ "${rc}" -ne 0 ]]; then
    log "verify: POST /search ${name} curl exit ${rc}"
    cat "${ART}/${name}.body.json" >&2 || true
    exit 1
  fi
  if [[ "${http}" != "200" ]]; then
    log "verify: POST /search ${name} HTTP ${http}"
    cat "${ART}/${name}.body.json" >&2 || true
    exit 1
  fi
}

post_ask() {
  local name="$1"
  local body="$2"
  printf '%s\n' "${body}" >"${ART}/${name}.request.json"
  set +e
  local http
  http="$(
    curl -sS --max-time 180 \
      -D "${ART}/${name}.headers" \
      -o "${ART}/${name}.body.json" \
      -w "%{http_code}" \
      -H 'content-type: application/json' \
      -d "${body}" \
      "http://127.0.0.1:${PORT}/ask"
  )"
  local rc=$?
  set -e
  write "${name}.exit" "${rc}"
  write "${name}.http" "${http}"
  if [[ "${rc}" -ne 0 ]]; then
    log "verify: POST /ask ${name} curl exit ${rc}"
    cat "${ART}/${name}.body.json" >&2 || true
    exit 1
  fi
  if [[ "${http}" != "200" ]]; then
    log "verify: POST /ask ${name} HTTP ${http}"
    cat "${ART}/${name}.body.json" >&2 || true
    exit 1
  fi
}

post_ask_stream() {
  local name="$1"
  local body="$2"
  printf '%s\n' "${body}" >"${ART}/${name}.request.json"
  set +e
  local http
  http="$(
    curl -sS --max-time 180 \
      -D "${ART}/${name}.headers" \
      -o "${ART}/${name}.body" \
      -w "%{http_code}" \
      -H 'content-type: application/json' \
      -H 'accept: text/event-stream' \
      -d "${body}" \
      "http://127.0.0.1:${PORT}/ask/stream"
  )"
  local rc=$?
  set -e
  write "${name}.exit" "${rc}"
  write "${name}.http" "${http}"
  if [[ "${rc}" -ne 0 ]]; then
    log "verify: POST /ask/stream ${name} curl exit ${rc}"
    cat "${ART}/${name}.body" >&2 || true
    exit 1
  fi
  if [[ "${http}" != "200" ]]; then
    log "verify: POST /ask/stream ${name} HTTP ${http}"
    cat "${ART}/${name}.body" >&2 || true
    exit 1
  fi
  if ! grep -qi "text/event-stream" "${ART}/${name}.headers"; then
    log "verify: POST /ask/stream ${name} missing text/event-stream"
    cat "${ART}/${name}.headers" >&2 || true
    exit 1
  fi
}

check_ask_stream() {
  local stream_name="$1"
  local ask_name="$2"
  uv run python - "${ART}" "${stream_name}" "${ask_name}" <<'PY'
import json
import sys
from pathlib import Path

art = Path(sys.argv[1])
stream_name = sys.argv[2]
ask_name = sys.argv[3]
text = (art / f"{stream_name}.body").read_text(encoding="utf-8")
events: list[dict[str, object]] = []
for line in text.splitlines():
    if line.startswith("data: "):
        payload = json.loads(line.removeprefix("data: "))
        if not isinstance(payload, dict):
            sys.stderr.write(f"verify: {stream_name} data is not an object\n")
            sys.exit(1)
        events.append(payload)
if not events:
    sys.stderr.write(f"verify: {stream_name} produced no SSE data frames\n")
    sys.exit(1)
names = [str(event.get("event") or "") for event in events]
if names[-1] != "done":
    sys.stderr.write(f"verify: {stream_name} last event is {names[-1]!r}, not done\n")
    sys.exit(1)
if "error" in names:
    sys.stderr.write(f"verify: {stream_name} included an error event\n")
    sys.exit(1)
ask_path = art / f"{ask_name}.body.json"
if ask_path.is_file():
    ask = json.loads(ask_path.read_text(encoding="utf-8"))
    kind = str(ask.get("kind") or "")
    matches = [event for event in events if event.get("event") == kind]
    if len(matches) != 1:
        sys.stderr.write(f"verify: {stream_name} expected one {kind} event, got {len(matches)}\n")
        sys.exit(1)
    if matches[0].get("response") != ask:
        sys.stderr.write(f"verify: {stream_name} {kind}.response != POST /ask\n")
        sys.exit(1)
(art / f"{stream_name}.events.json").write_text(
    json.dumps(events, indent=2) + "\n", encoding="utf-8"
)
print(f"stream {stream_name} events={'/'.join(names)}")
PY
}

if [[ "${SMALL}" -eq 1 ]]; then
  INDEX_CMD=(
    uv run capitalcite index
    --input-dir "${CHUNK_OUT}"
    --chunker section
    --embedder bge-small
  )
  printf '%s\n' "${INDEX_CMD[*]}" >"${ART}/index.cmd"
  log "verify: stopping uvicorn so capitalcite index can write the embedded store"
  stop_main_uvicorn
  log "verify: ${INDEX_CMD[*]} (--small fixture index)"
  set +e
  "${INDEX_CMD[@]}" >"${ART}/index.1.out" 2>"${ART}/index.1.err"
  index_1_rc=$?
  set -e
  write index.1.exit "${index_1_rc}"
  if [[ "${index_1_rc}" -ne 0 ]]; then
    log "verify: capitalcite index of fixture chunks failed"
    cat "${ART}/index.1.out" >&2 || true
    cat "${ART}/index.1.err" >&2 || true
    exit 1
  fi
  log "verify: restarting uvicorn on the indexed store"
  start_main_uvicorn
  ASK_FIXTURE='{"question":"What CET1 ratio did JPMorgan disclose?","k":10,"ticker":"JPM","model":"fake"}'
  ASK_FIXTURE_REFUSE='{"question":"What CET1 ratio did Wells Fargo disclose for year-end 2026?","k":10,"ticker":"WFC","model":"fake"}'
  ASK_FIXTURE_AGENT='{"question":"What CET1 ratio did JPMorgan disclose for 2024?","k":10,"path":"agent","model":"fake"}'
  log "verify: POST /ask fixture JPM model=fake"
  post_ask ask.fixture "${ASK_FIXTURE}"
  log "verify: POST /ask fixture WFC 2026 model=fake"
  post_ask ask.fixture.refused "${ASK_FIXTURE_REFUSE}"
  log "verify: POST /ask/stream fixture JPM model=fake"
  post_ask_stream stream.fixture "${ASK_FIXTURE}"
  check_ask_stream stream.fixture ask.fixture
  log "verify: POST /ask/stream fixture WFC 2026 model=fake"
  post_ask_stream stream.fixture.refused "${ASK_FIXTURE_REFUSE}"
  check_ask_stream stream.fixture.refused ask.fixture.refused
  log "verify: POST /ask/stream fixture agent model=fake"
  post_ask_stream stream.fixture.agent "${ASK_FIXTURE_AGENT}"
  check_ask_stream stream.fixture.agent ""
  uv run python - "${ART}/stream.fixture.agent.body" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
names = [
    json.loads(line.removeprefix("data: ")).get("event")
    for line in text.splitlines()
    if line.startswith("data: ")
]
if "plan" not in names or "tool_call" not in names:
    sys.stderr.write(f"verify: fixture agent stream missing plan/tool_call: {names}\n")
    sys.exit(1)
if names[-1] != "done":
    sys.stderr.write(f"verify: fixture agent stream last event is {names[-1]!r}\n")
    sys.exit(1)
print("stream fixture agent has plan/tool_call/done")
PY
  cp "${ART}/ask.fixture.body.json" "${ART}/ask.body.json"
  cp "${ART}/ask.fixture.http" "${ART}/ask.http"
  cp "${ART}/ask.fixture.exit" "${ART}/ask.exit"
  cp "${ART}/stream.fixture.body" "${ART}/stream.body"
  cp "${ART}/stream.fixture.http" "${ART}/stream.http"
  cp "${ART}/stream.fixture.exit" "${ART}/stream.exit"
  cp "${ART}/stream.fixture.events.json" "${ART}/stream.events.json"
  write search.unreachable "--small fixture index; gold span search needs the ingested 10-Ks"
  write agent.unreachable "--small fixture index is JPM-only; comparison/multi-hop items need BAC"
  write ask.openai.unverified "--small does not probe live OpenAI"
  write agent.openai.unverified "--small does not probe live OpenAI"
  write audit.agent.skipped "--small ran a one-bank fixture agent stream; one-record check needs cc-h-055/061"
  log "verify: POST /search gold items UNVERIFIED (--small fixture index)"
  log "verify: POST /ask cc-h-013 / cc-u-001 UNVERIFIED (--small fixture index)"
  log "verify: POST /ask path=agent cc-h-055 / cc-h-061 UNVERIFIED (--small fixture index)"
  log "verify: POST /ask/stream fixture path ok"
  log "verify: concurrent /ask/stream burst"
  set +e
  uv run python - "${PORT}" "${ART}" <<'PY'
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

port = sys.argv[1]
art = Path(sys.argv[2])
payload = json.dumps({"question": "What is the tier 1 capital buffer?", "model": "fake"}).encode()


def one(index: int) -> tuple[int, int, bytes]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ask/stream",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return index, resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return index, exc.code, exc.read()


with ThreadPoolExecutor(max_workers=6) as pool:
    results = [future.result() for future in as_completed(pool.submit(one, i) for i in range(6))]
codes = [status for _, status, _ in sorted(results)]
(art / "concurrent.stream.codes").write_text(" ".join(str(code) for code in codes) + "\n", encoding="utf-8")
if codes != [200] * 6:
    sys.stderr.write(f"verify: concurrent /ask/stream codes={codes}\n")
    sys.exit(1)
print("concurrent_stream_http_200=6")
PY
  concurrent_rc=$?
  set -e
  write concurrent.stream.exit "${concurrent_rc}"
  if [[ "${concurrent_rc}" -ne 0 ]]; then
    log "verify: concurrent /ask/stream burst failed"
    exit 1
  fi
  log "verify: concurrent /ask/stream burst ok"
  if [[ -f "${ART}/spans.jsonl" && -s "${ART}/spans.jsonl" ]]; then
    log "verify: span JSONL after fixture /ask"
    uv run python - "${ART}/spans.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
records = [
    json.loads(line)
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
names = {str(row.get("name")) for row in records}
if "ask" not in names:
    sys.stderr.write(f"verify: span names {sorted(names)} missing ask\n")
    sys.exit(1)
print(f"spans ask n={len(records)}")
PY
  else
    write spans.skipped "fixture /ask did not write span JSONL"
    log "verify: ask span tree UNVERIFIED (empty file)"
  fi
elif [[ -f "${ART}/ingest.unreachable" ]]; then
  write search.unreachable "live ingest unreachable; embedded index needs JPM FY24 and WFC FY25 chunks"
  write ask.unreachable "POST /search was unverified; POST /ask needs the same index"
  write stream.unreachable "POST /search was unverified; POST /ask/stream needs the same index"
  write agent.unreachable "POST /search was unverified; POST /ask path=agent needs the same index"
  write spans.skipped "POST /ask was unverified; span JSONL needs a fake /ask"
  write audit.agent.skipped "POST /ask path=agent was unverified; live one-record check needs traces"
  log "verify: POST /search UNVERIFIED (no ingested corpus)"
  log "verify: POST /ask UNVERIFIED (no ingested corpus)"
  log "verify: POST /ask/stream UNVERIFIED (no ingested corpus)"
  log "verify: POST /ask path=agent UNVERIFIED (no ingested corpus)"
  log "verify: ask span tree UNVERIFIED (no ingested corpus)"
else
  INDEX_CORPUS="${ART}/index-corpus"
  mkdir -p \
    "${INDEX_CORPUS}/parsed/edgar/JPM/10-K" \
    "${INDEX_CORPUS}/parsed/edgar/WFC/10-K" \
    "${INDEX_CORPUS}/parsed/edgar/BAC/10-K"
  cp -a "${INGEST_DIR}/parsed/edgar/JPM/10-K/2024-12-31" "${INDEX_CORPUS}/parsed/edgar/JPM/10-K/"
  cp -a "${INGEST_DIR}/parsed/edgar/WFC/10-K/2025-12-31" "${INDEX_CORPUS}/parsed/edgar/WFC/10-K/"
  cp -a "${INGEST_DIR}/parsed/edgar/BAC/10-K/2024-12-31" "${INDEX_CORPUS}/parsed/edgar/BAC/10-K/"
  INDEX_CMD=(
    uv run capitalcite index
    --input-dir "${INDEX_CORPUS}"
    --chunker section
    --embedder bge-small
  )
  printf '%s\n' "${INDEX_CMD[*]}" >"${ART}/index.cmd"
  log "verify: stopping uvicorn so capitalcite index can write the embedded store"
  stop_main_uvicorn
  log "verify: ${INDEX_CMD[*]} (1/2)"
  set +e
  "${INDEX_CMD[@]}" >"${ART}/index.1.out" 2>"${ART}/index.1.err"
  index_1_rc=$?
  set -e
  write index.1.exit "${index_1_rc}"
  if [[ "${index_1_rc}" -ne 0 ]]; then
    log "verify: capitalcite index failed"
    cat "${ART}/index.1.out" >&2 || true
    cat "${ART}/index.1.err" >&2 || true
    exit 1
  fi
  log "verify: restarting uvicorn on the indexed store"
  start_main_uvicorn

  Q013='{"query":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","mode":"hybrid","k":5}'
  post_search search.repro.1 "${Q013}"

  log "verify: stopping uvicorn so capitalcite index can write the embedded store"
  stop_main_uvicorn
  log "verify: ${INDEX_CMD[*]} (2/2)"
  set +e
  "${INDEX_CMD[@]}" >"${ART}/index.2.out" 2>"${ART}/index.2.err"
  index_2_rc=$?
  set -e
  write index.2.exit "${index_2_rc}"
  if [[ "${index_2_rc}" -ne 0 ]]; then
    log "verify: capitalcite index rerun failed"
    cat "${ART}/index.2.out" >&2 || true
    cat "${ART}/index.2.err" >&2 || true
    exit 1
  fi
  log "verify: restarting uvicorn on the reindexed store"
  start_main_uvicorn

  post_search search.repro.2 "${Q013}"
  post_search search.cc-h-013.hybrid "${Q013}"
  post_search search.cc-h-013.hybrid-jpm \
    '{"query":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","mode":"hybrid","k":10,"ticker":"JPM"}'
  post_search search.cc-h-013.dense \
    '{"query":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","mode":"dense","k":5}'
  post_search search.cc-h-013.bm25 \
    '{"query":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","mode":"bm25","k":5}'
  post_search search.cc-h-063.hybrid \
    '{"query":"What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?","mode":"hybrid","k":5}'
  post_search search.cc-h-063.hybrid-wfc \
    '{"query":"What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?","mode":"hybrid","k":10,"ticker":"WFC"}'

  uv run python - "${ART}" "${INDEX_CORPUS}" evals/golden/v1.jsonl <<'PY'
import json
import sys
from pathlib import Path

art = Path(sys.argv[1])
ingest = Path(sys.argv[2])
items = {
    json.loads(line)["id"]: json.loads(line)
    for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()
    if line.strip()
}
sidecars = sorted((ingest / "index").glob("*.json"))
if len(sidecars) != 1:
    sys.stderr.write(f"verify: expected 1 index sidecar, found {len(sidecars)}\n")
    sys.exit(1)
receipt = json.loads(sidecars[0].read_text(encoding="utf-8"))
for key in ("config_hash", "collection_name", "point_count"):
    if key not in receipt:
        sys.stderr.write(f"verify: sidecar missing {key}\n")
        sys.exit(1)
if receipt["point_count"] < 1:
    sys.stderr.write("verify: index point_count is 0\n")
    sys.exit(1)

required = ("doc_id", "section", "start", "end", "source_url", "text")
errors: list[str] = []
report: list[str] = []


def load(name: str) -> dict:
    body = json.loads((art / f"{name}.body.json").read_text(encoding="utf-8"))
    hits = body.get("hits")
    if body.get("config_hash") != receipt["config_hash"]:
        errors.append(f"{name}: config_hash mismatch")
    if body.get("collection_name") != receipt["collection_name"]:
        errors.append(f"{name}: collection_name mismatch")
    if not isinstance(hits, list) or not hits:
        errors.append(f"{name}: no hits")
        return body
    if not isinstance(body.get("point_count"), int) or body["point_count"] < 1:
        errors.append(f"{name}: empty point_count")
    for hit in hits:
        if not isinstance(hit, dict):
            errors.append(f"{name}: hit was not an object")
            continue
        chunk = hit.get("chunk")
        if not isinstance(hit.get("score"), (int, float)) or not isinstance(chunk, dict):
            errors.append(f"{name}: missing score or chunk")
            continue
        missing = [key for key in required if key not in chunk]
        if missing:
            errors.append(f"{name}: missing {missing}")
    return body


def hit_ids(body: dict) -> list[tuple[object, object, object]]:
    out: list[tuple[object, object, object]] = []
    for hit in body.get("hits") or []:
        chunk = hit.get("chunk") or {}
        out.append((chunk.get("doc_id"), chunk.get("start"), chunk.get("end")))
    return out


def overlaps(body: dict, item_id: str) -> bool:
    item = items[item_id]
    for hit in body.get("hits") or []:
        chunk = hit.get("chunk") or {}
        for row in item.get("evidence") or []:
            start = row.get("start")
            end = row.get("end")
            if start is None or end is None:
                continue
            if chunk.get("doc_id") != row.get("doc_id"):
                continue
            if int(chunk["start"]) < int(end) and int(start) < int(chunk["end"]):
                return True
    return False


def tickers(body: dict) -> set[str]:
    found: set[str] = set()
    for hit in body.get("hits") or []:
        found.add(str((hit.get("chunk") or {}).get("ticker") or ""))
    return found


def dump(name: str, body: dict) -> None:
    report.append(f"## {name}")
    report.append(f"config_hash {body.get('config_hash')}")
    report.append(f"collection {body.get('collection_name')}")
    report.append(f"point_count {body.get('point_count')}")
    for index, hit in enumerate(body.get("hits") or [], start=1):
        chunk = hit.get("chunk") or {}
        report.append(
            f"{index}. score={hit.get('score')} doc_id={chunk.get('doc_id')} "
            f"section={chunk.get('section')} start={chunk.get('start')} "
            f"end={chunk.get('end')} ticker={chunk.get('ticker')}"
        )
    report.append("")


first = load("search.repro.1")
second = load("search.repro.2")
if hit_ids(first) != hit_ids(second):
    errors.append("index rerun changed top-k ids")
dump("search.repro.1", first)
dump("search.repro.2", second)

def gold_doc(item_id: str) -> str:
    return str(items[item_id]["evidence"][0]["doc_id"])


def has_doc(body: dict, item_id: str) -> bool:
    want = gold_doc(item_id)
    return any((hit.get("chunk") or {}).get("doc_id") == want for hit in body.get("hits") or [])


span_cases = (
    ("search.cc-h-013.hybrid-jpm", "cc-h-013", "JPM"),
    ("search.cc-h-063.hybrid-wfc", "cc-h-063", "WFC"),
)
doc_cases = (
    ("search.cc-h-013.hybrid", "cc-h-013", None),
    ("search.cc-h-013.dense", "cc-h-013", None),
    ("search.cc-h-013.bm25", "cc-h-013", None),
    ("search.cc-h-063.hybrid", "cc-h-063", None),
)
for name, item_id, ticker in span_cases:
    body = load(name)
    dump(name, body)
    if not overlaps(body, item_id):
        errors.append(f"{name}: gold span for {item_id} missed top-k")
    if ticker is not None and tickers(body) - {ticker, ""}:
        errors.append(f"{name}: hits outside ticker {ticker}: {sorted(tickers(body))}")
for name, item_id, ticker in doc_cases:
    body = load(name)
    dump(name, body)
    if not has_doc(body, item_id):
        errors.append(f"{name}: gold doc_id {gold_doc(item_id)} missed top-k")
    if ticker is not None and tickers(body) - {ticker, ""}:
        errors.append(f"{name}: hits outside ticker {ticker}: {sorted(tickers(body))}")

(art / "search.top5.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
(art / "index.receipt.json").write_text(
    json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
if errors:
    sys.stderr.write("verify: search checks failed:\n  " + "\n  ".join(errors) + "\n")
    sys.exit(1)
print(
    "search "
    f"config_hash={receipt['config_hash']} "
    f"collection={receipt['collection_name']} "
    f"points={receipt['point_count']}"
)
PY
  log "verify: POST /search returned cited hits with gold span overlap"
  cat "${ART}/search.top5.txt"
  cp "${ART}/search.cc-h-013.hybrid.body.json" "${ART}/search.body.json"
  cp "${ART}/search.cc-h-013.hybrid.http" "${ART}/search.http"
  cp "${ART}/search.cc-h-013.hybrid.exit" "${ART}/search.exit"

  ASK013='{"question":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","k":10,"ticker":"JPM","model":"fake"}'
  ASK_U001='{"question":"What CET1 ratio did Wells Fargo disclose for year-end 2026?","k":10,"ticker":"WFC","model":"fake"}'
  SEARCH_U001='{"query":"What CET1 ratio did Wells Fargo disclose for year-end 2026?","mode":"hybrid","k":10,"ticker":"WFC"}'
  log "verify: POST /ask cc-h-013 model=fake"
  post_ask ask.cc-h-013 "${ASK013}"
  log "verify: POST /ask cc-u-001 model=fake"
  post_ask ask.cc-u-001 "${ASK_U001}"
  log "verify: POST /ask/stream cc-h-013 model=fake"
  post_ask_stream stream.cc-h-013 "${ASK013}"
  check_ask_stream stream.cc-h-013 ask.cc-h-013
  log "verify: POST /ask/stream cc-u-001 model=fake"
  post_ask_stream stream.cc-u-001 "${ASK_U001}"
  check_ask_stream stream.cc-u-001 ask.cc-u-001
  cp "${ART}/stream.cc-h-013.body" "${ART}/stream.body"
  cp "${ART}/stream.cc-h-013.http" "${ART}/stream.http"
  cp "${ART}/stream.cc-h-013.exit" "${ART}/stream.exit"
  cp "${ART}/stream.cc-h-013.events.json" "${ART}/stream.events.json"
  post_search search.cc-u-001.hybrid-wfc "${SEARCH_U001}"

  ASK_OPENAI='{"question":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","k":10,"ticker":"JPM","model":"openai"}'
  KEY_PRESENT="$(uv run python -c 'from capitalcite.config import Settings; key=(Settings().openai_api_key or "").strip(); print("yes" if key else "no")')"
  write ask.openai.key "${KEY_PRESENT}"
  printf '%s\n' "${ASK_OPENAI}" >"${ART}/ask.openai.request.json"
  log "verify: POST /ask cc-h-013 model=openai (key ${KEY_PRESENT})"
  set +e
  ask_openai_http="$(
    curl -sS --max-time 180 \
      -D "${ART}/ask.openai.headers" \
      -o "${ART}/ask.openai.body.json" \
      -w "%{http_code}" \
      -H 'content-type: application/json' \
      -d "${ASK_OPENAI}" \
      "http://127.0.0.1:${PORT}/ask"
  )"
  ask_openai_rc=$?
  set -e
  write ask.openai.exit "${ask_openai_rc}"
  write ask.openai.http "${ask_openai_http}"

  uv run python - "${ART}" <<'PY'
import json
import sys
from pathlib import Path

art = Path(sys.argv[1])
errors: list[str] = []


def load(name: str) -> dict:
    return json.loads((art / f"{name}.body.json").read_text(encoding="utf-8"))


def schema(name: str, body: dict) -> None:
    kind = body.get("kind")
    if kind not in ("answered", "refused"):
        errors.append(f"{name}: kind={kind!r}")
        return
    if not isinstance(body.get("retrieval_score"), (int, float)):
        errors.append(f"{name}: retrieval_score={body.get('retrieval_score')!r}")
    if not isinstance(body.get("config_hash"), str) or len(body["config_hash"]) != 64:
        errors.append(f"{name}: config_hash missing")
    if not isinstance(body.get("collection_name"), str) or not body["collection_name"]:
        errors.append(f"{name}: collection_name missing")
    if not isinstance(body.get("point_count"), int) or body["point_count"] < 0:
        errors.append(f"{name}: point_count={body.get('point_count')!r}")
    citations = body.get("citations")
    if not isinstance(citations, list):
        errors.append(f"{name}: citations was not a list")
        return
    if kind == "answered":
        answer = body.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            errors.append(f"{name}: answered without an answer")
        if not citations:
            errors.append(f"{name}: answered without citations")
        if body.get("refusal_reason") is not None:
            errors.append(f"{name}: answered included refusal_reason")
    else:
        if body.get("answer") is not None:
            errors.append(f"{name}: refused included an answer")
        if citations:
            errors.append(f"{name}: refused included citations")
        reason = body.get("refusal_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{name}: refused without a reason")


def same_index(name: str, body: dict, search: dict) -> None:
    for key in ("config_hash", "collection_name", "point_count"):
        if body.get(key) != search.get(key):
            errors.append(f"{name}: {key} does not match {search.get(key)!r}")


def ground(name: str, body: dict, search: dict) -> None:
    spans: dict[tuple[object, object, object], dict] = {}
    for hit in search.get("hits") or []:
        chunk = hit.get("chunk") or {}
        spans[(chunk.get("doc_id"), chunk.get("start"), chunk.get("end"))] = chunk
    for cite in body.get("citations") or []:
        if not isinstance(cite, dict):
            errors.append(f"{name}: citation was not an object")
            continue
        key = (cite.get("doc_id"), cite.get("start"), cite.get("end"))
        chunk = spans.get(key)
        if chunk is None:
            errors.append(f"{name}: citation {key} is not a retrieved hit")
            continue
        quote = cite.get("quote")
        text = chunk.get("text")
        if not isinstance(quote, str) or not isinstance(text, str) or quote not in text:
            errors.append(f"{name}: quote is not in the cited chunk")
        if cite.get("section") != chunk.get("section"):
            errors.append(f"{name}: section does not match the hit")
        if cite.get("source_url") != chunk.get("source_url"):
            errors.append(f"{name}: source_url does not match the hit")
        quote_start = cite.get("quote_start")
        quote_end = cite.get("quote_end")
        if not isinstance(quote_start, int) or not isinstance(quote_end, int):
            errors.append(f"{name}: quote_start/quote_end missing")
        elif quote_end < quote_start:
            errors.append(f"{name}: quote_end is before quote_start")


answered = load("ask.cc-h-013")
answered_search = load("search.cc-h-013.hybrid-jpm")
schema("ask.cc-h-013", answered)
same_index("ask.cc-h-013", answered, answered_search)
if answered.get("kind") != "answered":
    errors.append("ask.cc-h-013: fake model did not answer")
if not (answered.get("citations") or []):
    errors.append("ask.cc-h-013: no citations")
ground("ask.cc-h-013", answered, answered_search)

unanswerable = load("ask.cc-u-001")
unanswerable_search = load("search.cc-u-001.hybrid-wfc")
schema("ask.cc-u-001", unanswerable)
same_index("ask.cc-u-001", unanswerable, unanswerable_search)
if unanswerable.get("kind") != "refused":
    errors.append("ask.cc-u-001: expected refused with the fake model")
if unanswerable.get("citations"):
    ground("ask.cc-u-001", unanswerable, unanswerable_search)

key = (art / "ask.openai.key").read_text(encoding="utf-8").strip()
status = (art / "ask.openai.http").read_text(encoding="utf-8").strip()
if key == "no":
    if status != "503":
        errors.append(f"ask openai without a key returned HTTP {status}, expected 503")
    else:
        (art / "ask.openai.unverified").write_text(
            "OPENAI_API_KEY is empty. POST /ask with model openai returned HTTP 503. "
            "Live generation was not run.\n",
            encoding="utf-8",
        )
elif status != "200":
    (art / "ask.openai.unverified").write_text(
        f"OPENAI_API_KEY is set. POST /ask with model openai returned HTTP {status}. "
        "Live generation is UNVERIFIED.\n",
        encoding="utf-8",
    )
else:
    live = load("ask.openai")
    schema("ask.openai", live)
    same_index("ask.openai", live, answered_search)
    if live.get("citations"):
        ground("ask.openai", live, answered_search)

if errors:
    sys.stderr.write("verify: ask checks failed:\n  " + "\n  ".join(errors) + "\n")
    sys.exit(1)
print(f"ask cc-h-013 kind={answered.get('kind')} citations={len(answered.get('citations') or [])}")
print(f"ask cc-u-001 kind={unanswerable.get('kind')} citations={len(unanswerable.get('citations') or [])}")
PY
  cp "${ART}/ask.cc-h-013.body.json" "${ART}/ask.body.json"
  cp "${ART}/ask.cc-h-013.http" "${ART}/ask.http"
  cp "${ART}/ask.cc-h-013.exit" "${ART}/ask.exit"
  if [[ -f "${ART}/ask.openai.unverified" ]]; then
    log "verify: POST /ask openai UNVERIFIED"
    cat "${ART}/ask.openai.unverified"
  else
    log "verify: POST /ask openai returned a grounded response"
  fi
  log "verify: POST /ask fake path ok"

  log "verify: span JSONL after fake /ask"
  uv run python - "${ART}/spans.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.stat().st_size == 0:
    sys.stderr.write("verify: spans.jsonl missing after POST /ask\n")
    sys.exit(1)
records = [
    json.loads(line)
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
names = {str(row.get("name")) for row in records}
if "ask" not in names or "retrieve" not in names:
    sys.stderr.write(f"verify: span names {sorted(names)} missing ask or retrieve\n")
    sys.exit(1)
retrieve = next(row for row in records if row.get("name") == "retrieve")
attrs = retrieve.get("attributes") or {}
if not isinstance(attrs.get("capitalcite.hit_count"), int):
    sys.stderr.write("verify: retrieve span missing capitalcite.hit_count\n")
    sys.exit(1)
blob = json.dumps(records)
for needle in ("Bearer ", "sk-lf-", "Authorization"):
    if needle in blob:
        sys.stderr.write(f"verify: span JSONL contained {needle!r}\n")
        sys.exit(1)
print(f"spans ask+retrieve n={len(records)}")
PY
  log "verify: span JSONL has ask and retrieve"

  AGENT055='{"question":"Which bank had the higher Standardized CET1 ratio at year-end 2024, JPMorgan or Bank of America?","k":10,"path":"agent","model":"fake"}'
  AGENT061='{"question":"Did JPMorgan'\''s year-end 2024 Standardized CET1 ratio exceed its 12.3% requirement including buffers?","k":10,"path":"agent","model":"fake"}'
  log "verify: POST /ask path=agent cc-h-055 model=fake"
  post_ask agent.cc-h-055 "${AGENT055}"
  log "verify: POST /ask path=agent cc-h-061 model=fake"
  post_ask agent.cc-h-061 "${AGENT061}"
  log "verify: POST /ask/stream path=agent cc-h-055 model=fake"
  post_ask_stream stream.agent.cc-h-055 "${AGENT055}"
  check_ask_stream stream.agent.cc-h-055 agent.cc-h-055

  AGENT_OPENAI='{"question":"Which bank had the higher Standardized CET1 ratio at year-end 2024, JPMorgan or Bank of America?","k":10,"path":"agent","model":"openai"}'
  write agent.openai.key "${KEY_PRESENT}"
  printf '%s\n' "${AGENT_OPENAI}" >"${ART}/agent.openai.request.json"
  log "verify: POST /ask path=agent cc-h-055 model=openai (key ${KEY_PRESENT})"
  set +e
  agent_openai_http="$(
    curl -sS --max-time 180 \
      -D "${ART}/agent.openai.headers" \
      -o "${ART}/agent.openai.body.json" \
      -w "%{http_code}" \
      -H 'content-type: application/json' \
      -d "${AGENT_OPENAI}" \
      "http://127.0.0.1:${PORT}/ask"
  )"
  agent_openai_rc=$?
  set -e
  write agent.openai.exit "${agent_openai_rc}"
  write agent.openai.http "${agent_openai_http}"

  uv run python - "${ART}" <<'PY'
import json
import sys
from pathlib import Path

art = Path(sys.argv[1])
errors: list[str] = []


def load(name: str) -> dict:
    return json.loads((art / f"{name}.body.json").read_text(encoding="utf-8"))


def schema(name: str, body: dict) -> None:
    if body.get("path") != "agent":
        errors.append(f"{name}: path={body.get('path')!r}")
    kind = body.get("kind")
    if kind not in ("answered", "refused"):
        errors.append(f"{name}: kind={kind!r}")
    plan = body.get("plan")
    traces = body.get("traces")
    if not isinstance(plan, list) or not plan:
        errors.append(f"{name}: plan missing")
        return
    if not isinstance(traces, list) or not traces:
        errors.append(f"{name}: traces missing")
    steps = body.get("steps_used")
    if not isinstance(steps, int) or steps < 2:
        errors.append(f"{name}: steps_used={steps!r}")
    tools = [step.get("tool") for step in plan if isinstance(step, dict)]
    if "search_filings" not in tools:
        errors.append(f"{name}: plan did not search filings")


comparison = load("agent.cc-h-055")
schema("agent.cc-h-055", comparison)
searches = [
    step
    for step in comparison.get("plan") or []
    if isinstance(step, dict) and step.get("tool") == "search_filings"
]
tickers = {str((step.get("args") or {}).get("ticker") or "") for step in searches}
if len(searches) < 2:
    errors.append(f"agent.cc-h-055: expected at least two search_filings, got {len(searches)}")
if not {"JPM", "BAC"} <= tickers:
    errors.append(f"agent.cc-h-055: plan tickers={tickers!r}")

multihop = load("agent.cc-h-061")
schema("agent.cc-h-061", multihop)

key = (art / "agent.openai.key").read_text(encoding="utf-8").strip()
status = (art / "agent.openai.http").read_text(encoding="utf-8").strip()
if key == "no":
    if status != "503":
        errors.append(f"agent openai without a key returned HTTP {status}, expected 503")
    else:
        (art / "agent.openai.unverified").write_text(
            "OPENAI_API_KEY is empty. POST /ask path=agent with model openai returned HTTP 503. "
            "Live generation was not run.\n",
            encoding="utf-8",
        )
elif status != "200":
    (art / "agent.openai.unverified").write_text(
        f"OPENAI_API_KEY is set. POST /ask path=agent with model openai returned HTTP {status}. "
        "Live generation is UNVERIFIED.\n",
        encoding="utf-8",
    )

if errors:
    sys.stderr.write("verify: agent checks failed:\n  " + "\n  ".join(errors) + "\n")
    sys.exit(1)
print(
    "agent cc-h-055 kind="
    f"{comparison.get('kind')} plan={len(comparison.get('plan') or [])} "
    f"traces={len(comparison.get('traces') or [])}"
)
print(
    "agent cc-h-061 kind="
    f"{multihop.get('kind')} plan={len(multihop.get('plan') or [])} "
    f"traces={len(multihop.get('traces') or [])}"
)
PY
  if [[ -f "${ART}/agent.openai.unverified" ]]; then
    log "verify: POST /ask path=agent openai UNVERIFIED"
    cat "${ART}/agent.openai.unverified"
  else
    log "verify: POST /ask path=agent openai returned a response"
  fi
  log "verify: POST /ask path=agent fake path ok"

  log "verify: live audit log vs agent traces"
  set +e
  uv run python - "${ART}" "${AUDIT_LOG_PATH}" <<'PY'
import json
import sys
from pathlib import Path

from capitalcite.audit import AuditRecord, verify

art = Path(sys.argv[1])
log_path = Path(sys.argv[2])
if not log_path.is_file():
    sys.stderr.write(f"verify: live audit log missing: {log_path}\n")
    sys.exit(1)
report = verify(log_path)
if not report.ok:
    reasons = "; ".join(issue.reason for issue in report.issues)
    sys.stderr.write(f"verify: live audit chain failed: {reasons}\n")
    sys.exit(1)
records = [
    AuditRecord.model_validate_json(line)
    for line in log_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
for row in records:
    if row.decision == "denied" and not row.reason:
        sys.stderr.write(f"verify: denied seq={row.seq} missing reason\n")
        sys.exit(1)
    if row.decision == "allowed" and row.reason is not None:
        sys.stderr.write(f"verify: allowed seq={row.seq} has reason={row.reason!r}\n")
        sys.exit(1)
expected = 0
for name in ("agent.cc-h-055.body.json", "agent.cc-h-061.body.json"):
    body = json.loads((art / name).read_text(encoding="utf-8"))
    expected += len(body.get("traces") or [])
stream_events_path = art / "stream.agent.cc-h-055.events.json"
if stream_events_path.is_file():
    for event in json.loads(stream_events_path.read_text(encoding="utf-8")):
        if event.get("event") in {"answered", "refused"}:
            expected += len((event.get("response") or {}).get("traces") or [])
openai_unverified = (art / "agent.openai.unverified").is_file()
if openai_unverified:
    if len(records) != expected:
        sys.stderr.write(
            f"verify: live audit records={len(records)} traces={expected}\n"
        )
        sys.exit(1)
elif len(records) < expected:
    sys.stderr.write(
        f"verify: live audit records={len(records)} traces={expected}\n"
    )
    sys.exit(1)
if any(not row.trace_id.strip() for row in records):
    sys.stderr.write("verify: live audit record missing trace_id\n")
    sys.exit(1)
spans_path = art / "spans.jsonl"
if not spans_path.is_file() or spans_path.stat().st_size == 0:
    sys.stderr.write("verify: spans.jsonl missing for audit trace_id check\n")
    sys.exit(1)
ask_ids = {
    str(row.get("trace_id") or "")
    for line in spans_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
    for row in [json.loads(line)]
    if row.get("name") == "ask" and not row.get("parent_id")
}
ask_ids.discard("")
missing = sorted({row.trace_id for row in records if row.trace_id not in ask_ids})
if missing:
    sys.stderr.write(
        "verify: audit trace_id not an ask root span: " + ", ".join(missing) + "\n"
    )
    sys.exit(1)
print(f"audit live records={len(records)} traces={expected} ask_spans={len(ask_ids)}")
PY
  audit_agent_rc=$?
  set -e
  if [[ "${audit_agent_rc}" -ne 0 ]]; then
    log "verify: live audit vs traces failed"
    exit 1
  fi
  uv run capitalcite audit verify --path "${AUDIT_LOG_PATH}" \
    >"${ART}/audit.agent.out" 2>"${ART}/audit.agent.err"
  write audit.agent.exit "$?"
  log "verify: live audit vs traces ok"
  cat "${ART}/audit.agent.out"
fi

log "verify: quote-index sanity"
set +e
uv run python -m capitalcite.retrieval.sanity \
  >"${ART}/quote-sanity.out" 2>"${ART}/quote-sanity.err"
sanity_rc=$?
set -e
write quote-sanity.exit "${sanity_rc}"
if [[ "${sanity_rc}" -eq 2 ]]; then
  write quote-sanity.skipped "$(cat "${ART}/quote-sanity.err")"
  log "verify: quote-index sanity UNVERIFIED"
elif [[ "${sanity_rc}" -ne 0 ]]; then
  log "verify: quote-index sanity failed"
  cat "${ART}/quote-sanity.err" >&2 || true
  exit 1
else
  if ! grep -q "recall_at_5=" "${ART}/quote-sanity.out" \
    || ! grep -q "label=sanity" "${ART}/quote-sanity.out"; then
    log "verify: quote-index sanity output missing recall_at_5"
    cat "${ART}/quote-sanity.out" >&2 || true
    exit 1
  fi
  log "verify: quote-index sanity (not the 12-filing corpus eval)"
  cat "${ART}/quote-sanity.out"
fi

log "verify: capitalcite eval --help"
set +e
uv run capitalcite eval --help >"${ART}/eval.help.out" 2>"${ART}/eval.help.err"
eval_help_rc=$?
set -e
write eval.help.exit "${eval_help_rc}"
if [[ "${eval_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite eval --help failed"
  cat "${ART}/eval.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/eval.help.out" | grep -q -- "--suite" \
  || ! plain "${ART}/eval.help.out" | grep -q -- "--small" \
  || ! plain "${ART}/eval.help.out" | grep -q -- "metrics"; then
  log "verify: eval --help missing --suite, --small, or metrics"
  cat "${ART}/eval.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite eval --help ok"

log "verify: capitalcite check --help"
set +e
uv run capitalcite check --help >"${ART}/check.help.out" 2>"${ART}/check.help.err"
check_help_rc=$?
set -e
write check.help.exit "${check_help_rc}"
if [[ "${check_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite check --help failed"
  cat "${ART}/check.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/check.help.out" | grep -q -- "--baseline" \
  || ! plain "${ART}/check.help.out" | grep -q -- "--live"; then
  log "verify: check --help missing --baseline or --live"
  cat "${ART}/check.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite check --help ok"

log "verify: capitalcite eval --suite golden --small"
set +e
uv run capitalcite eval --suite golden --small --out "${ART}/eval-small" \
  >"${ART}/eval-small.out" 2>"${ART}/eval-small.err"
eval_small_rc=$?
set -e
write eval-small.exit "${eval_small_rc}"
if [[ "${eval_small_rc}" -ne 0 ]]; then
  log "verify: capitalcite eval --small failed"
  cat "${ART}/eval-small.out" >&2 || true
  cat "${ART}/eval-small.err" >&2 || true
  exit 1
fi
if ! grep -q "Recall@5" "${ART}/eval-small.out" \
  || ! grep -q "items=" "${ART}/eval-small.out"; then
  log "verify: eval --small stdout missing Recall@5 table"
  cat "${ART}/eval-small.out" >&2 || true
  exit 1
fi
log "verify: capitalcite eval --small ok"
cat "${ART}/eval-small.out"

build_ci_warehouse
export WAREHOUSE_PATH="${ROOT}/dbt/target/ci.duckdb"
write warehouse.path "${WAREHOUSE_PATH}"

log "verify: capitalcite check"
set +e
WAREHOUSE_PATH="${WAREHOUSE_PATH}" METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake \
  uv run capitalcite check --out "${ART}/check" \
  >"${ART}/check.out" 2>"${ART}/check.err"
check_rc=$?
set -e
write check.exit "${check_rc}"
if [[ "${check_rc}" -ne 0 ]]; then
  log "verify: capitalcite check failed"
  cat "${ART}/check.out" >&2 || true
  cat "${ART}/check.err" >&2 || true
  exit 1
fi
if ! grep -q "ok=true" "${ART}/check.out" \
  || ! grep -q "redteam_failed=0" "${ART}/check.out" \
  || ! grep -q "agent_exact=" "${ART}/check.out" \
  || ! grep -q "v1_mart=" "${ART}/check.out"; then
  log "verify: check stdout missing ok=true, redteam_failed=0, or metrics rates"
  cat "${ART}/check.out" >&2 || true
  exit 1
fi
if [[ ! -f "${ART}/check/check.json" ]]; then
  log "verify: check did not write check.json"
  exit 1
fi
log "verify: capitalcite check ok"
cat "${ART}/check.out"

log "verify: capitalcite check --live"
set +e
WAREHOUSE_PATH="${WAREHOUSE_PATH}" METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake \
  uv run capitalcite check --live --out "${ART}/check-live" \
  >"${ART}/check.live.out" 2>"${ART}/check.live.err"
check_live_rc=$?
set -e
write check.live.exit "${check_live_rc}"
KEY_FOR_CHECK="$(uv run python -c 'import os; print("yes" if os.environ.get("OPENAI_API_KEY","").strip() else "no")')"
if [[ "${KEY_FOR_CHECK}" == "no" ]]; then
  if [[ "${check_live_rc}" -ne 0 ]]; then
    log "verify: check --live without a key should exit 0 as UNVERIFIED"
    cat "${ART}/check.live.out" >&2 || true
    cat "${ART}/check.live.err" >&2 || true
    exit 1
  fi
  if ! grep -q "unverified" "${ART}/check.live.out" "${ART}/check.live.err"; then
    log "verify: check --live without a key did not print unverified"
    cat "${ART}/check.live.out" >&2 || true
    cat "${ART}/check.live.err" >&2 || true
    exit 1
  fi
  write check.live.unverified "OPENAI_API_KEY is empty. capitalcite check --live skipped the OpenAI contract."
  log "verify: capitalcite check --live UNVERIFIED (no OPENAI_API_KEY)"
  cat "${ART}/check.live.unverified"
else
  if [[ "${check_live_rc}" -ne 0 ]]; then
    log "verify: capitalcite check --live failed"
    cat "${ART}/check.live.out" >&2 || true
    cat "${ART}/check.live.err" >&2 || true
    exit 1
  fi
  log "verify: capitalcite check --live ok"
  cat "${ART}/check.live.out"
fi

log "verify: capitalcite eval --suite metrics"
set +e
WAREHOUSE_PATH="${WAREHOUSE_PATH}" METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake \
  uv run capitalcite eval --suite metrics --out "${ART}/eval-metrics" \
  >"${ART}/eval-metrics.out" 2>"${ART}/eval-metrics.err"
eval_metrics_rc=$?
set -e
write eval-metrics.exit "${eval_metrics_rc}"
if [[ "${eval_metrics_rc}" -ne 0 ]] \
  || ! grep -q "suite=metrics" "${ART}/eval-metrics.out" \
  || ! grep -q "agent_exact=" "${ART}/eval-metrics.out" \
  || ! grep -q "agent_refusal=" "${ART}/eval-metrics.out" \
  || ! grep -q "rag_exact=" "${ART}/eval-metrics.out" \
  || ! grep -q "rag_refusal=" "${ART}/eval-metrics.out" \
  || ! grep -q "v1_mart=" "${ART}/eval-metrics.out"; then
  log "verify: eval --suite metrics failed"
  cat "${ART}/eval-metrics.out" >&2 || true
  cat "${ART}/eval-metrics.err" >&2 || true
  exit 1
fi
log "verify: capitalcite eval --suite metrics ok"
cat "${ART}/eval-metrics.out"

log "verify: capitalcite graph --help"
set +e
uv run capitalcite graph --help >"${ART}/graph.help.out" 2>"${ART}/graph.help.err"
graph_help_rc=$?
set -e
write graph.help.exit "${graph_help_rc}"
if [[ "${graph_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite graph --help failed"
  cat "${ART}/graph.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/graph.help.out" | grep -q "load" \
  || ! plain "${ART}/graph.help.out" | grep -q "query"; then
  log "verify: graph --help missing load or query"
  cat "${ART}/graph.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite graph --help ok"

log "verify: capitalcite graph load --backend memory"
set +e
uv run capitalcite graph load --backend memory >"${ART}/graph.load.out" 2>"${ART}/graph.load.err"
graph_load_rc=$?
set -e
write graph.load.exit "${graph_load_rc}"
if [[ "${graph_load_rc}" -ne 0 ]] || ! grep -q "backend=memory" "${ART}/graph.load.out" \
  || ! grep -q "banks=8" "${ART}/graph.load.out"; then
  log "verify: graph load --backend memory failed"
  cat "${ART}/graph.load.out" >&2 || true
  cat "${ART}/graph.load.err" >&2 || true
  exit 1
fi
log "verify: capitalcite graph load ok"
cat "${ART}/graph.load.out"

log "verify: capitalcite graph query --kind subsidiaries --ticker JPM"
set +e
uv run capitalcite graph query --kind subsidiaries --ticker JPM \
  >"${ART}/graph.query.out" 2>"${ART}/graph.query.err"
graph_query_rc=$?
set -e
write graph.query.exit "${graph_query_rc}"
if [[ "${graph_query_rc}" -ne 0 ]] || ! grep -q "852218" "${ART}/graph.query.out"; then
  log "verify: graph query subsidiaries JPM failed"
  cat "${ART}/graph.query.out" >&2 || true
  cat "${ART}/graph.query.err" >&2 || true
  exit 1
fi
log "verify: capitalcite graph query ok"
cat "${ART}/graph.query.out"

log "verify: capitalcite eval --suite graph --small"
set +e
uv run capitalcite eval --suite graph --small --out "${ART}/eval-graph" \
  >"${ART}/eval-graph-small.out" 2>"${ART}/eval-graph-small.err"
eval_graph_rc=$?
set -e
write eval-graph-small.exit "${eval_graph_rc}"
if [[ "${eval_graph_rc}" -ne 0 ]] \
  || ! grep -q "graph_exact=" "${ART}/eval-graph-small.out" \
  || ! grep -q "fixture_substring_hit_rate=" "${ART}/eval-graph-small.out"; then
  log "verify: eval --suite graph --small failed"
  cat "${ART}/eval-graph-small.out" >&2 || true
  cat "${ART}/eval-graph-small.err" >&2 || true
  exit 1
fi
log "verify: capitalcite eval --suite graph --small ok"
cat "${ART}/eval-graph-small.out"

log "verify: capitalcite metrics extract --help"
set +e
uv run capitalcite metrics extract --help >"${ART}/metrics.help.out" 2>"${ART}/metrics.help.err"
metrics_help_rc=$?
set -e
write metrics.help.exit "${metrics_help_rc}"
if [[ "${metrics_help_rc}" -ne 0 ]]; then
  log "verify: capitalcite metrics extract --help failed"
  cat "${ART}/metrics.help.err" >&2 || true
  exit 1
fi
if ! plain "${ART}/metrics.help.out" | grep -q -- "--fixtures" \
  || ! plain "${ART}/metrics.help.out" | grep -q -- "--tickers"; then
  log "verify: metrics extract --help missing --fixtures or --tickers"
  cat "${ART}/metrics.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite metrics extract --help ok"

log "verify: capitalcite metrics extract --fixtures"
set +e
uv run capitalcite metrics extract --tickers JPM,BAC,WFC,C --fixtures \
  --output-dir "${ART}/metrics" \
  >"${ART}/metrics.out" 2>"${ART}/metrics.err"
metrics_rc=$?
set -e
write metrics.exit "${metrics_rc}"
if [[ "${metrics_rc}" -ne 0 ]] \
  || ! grep -q "metrics extract xbrl=4 financials=4" "${ART}/metrics.out"; then
  log "verify: metrics extract --fixtures failed"
  cat "${ART}/metrics.out" >&2 || true
  cat "${ART}/metrics.err" >&2 || true
  exit 1
fi
find "${ART}/metrics" -type f | sort >"${ART}/metrics.files.txt"
uv run python - "${ART}/metrics" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
leads_path = root / "raw" / "metrics" / "lead_banks.json"
if not leads_path.is_file():
    sys.stderr.write("verify: lead_banks.json missing\n")
    sys.exit(1)
leads = json.loads(leads_path.read_text(encoding="utf-8"))
if len(leads) != 4:
    sys.stderr.write("verify: lead_banks.json does not list four tickers\n")
    sys.exit(1)
for link in leads:
    payloads = (
        root / "raw" / "xbrl" / f"{link['ticker']}.json",
        root / "raw" / "fdic" / "financials" / f"{link['cert']}.json",
    )
    for payload in payloads:
        meta_path = payload.with_name(f"{payload.stem}.metadata.json")
        if not payload.is_file() or not meta_path.is_file():
            sys.stderr.write(f"verify: missing snapshot for {payload.name}\n")
            sys.exit(1)
        body = payload.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(body).hexdigest()
        if meta.get("sha256") != digest or meta.get("bytes") != len(body):
            sys.stderr.write(f"verify: sha256 does not match {payload.name}\n")
            sys.exit(1)
PY
log "verify: capitalcite metrics extract --fixtures ok"
cat "${ART}/metrics.out"

export DBT_PROJECT_DIR="${ROOT}/dbt"

build_ci_warehouse

log "verify: dbt docs generate --target ci"
set +e
uv run --frozen --group metrics dbt docs generate --project-dir dbt --profiles-dir dbt --target ci \
  >"${ART}/dbt-docs.out" 2>"${ART}/dbt-docs.err"
dbt_docs_rc=$?
set -e
write dbt-docs.exit "${dbt_docs_rc}"
if [[ "${dbt_docs_rc}" -ne 0 ]]; then
  log "verify: dbt docs generate --target ci failed"
  cat "${ART}/dbt-docs.out" >&2 || true
  cat "${ART}/dbt-docs.err" >&2 || true
  exit 1
fi
log "verify: dbt docs generate --target ci ok"

log "verify: dbt fixture shape"
set +e
uv run --frozen --group metrics python - "${ROOT}/dbt/target/ci.duckdb" \
  >"${ART}/dbt-shape.out" 2>"${ART}/dbt-shape.err" <<'PY'
import sys

import duckdb

con = duckdb.connect(sys.argv[1], read_only=True)
holding = con.execute(
    """
    select assets_usd, deposits_usd, net_income_quarter_usd
    from fct_bank_financials_quarterly
    where bank_key = 'JPM|holding_company' and quarter_id = date '2026-06-30'
    """
).fetchone()
bank = con.execute(
    """
    select assets_usd_thousands, net_income_ytd_usd_thousands
    from fct_bank_financials_quarterly
    where bank_key = 'JPM|bank_subsidiary' and quarter_id = date '2026-06-30'
    """
).fetchone()
if holding != (5015069000000.0, None, None):
    raise SystemExit(f"holding shape {holding}")
if bank != (4091315000.0, 31335000.0):
    raise SystemExit(f"bank shape {bank}")
print("holding assets_usd=5015069000000 deposits_usd=null net_income_quarter_usd=null")
print("bank assets_usd_thousands=4091315000 net_income_ytd_usd_thousands=31335000")
PY
shape_rc=$?
set -e
write dbt-shape.exit "${shape_rc}"
if [[ "${shape_rc}" -ne 0 ]]; then
  log "verify: dbt fixture shape failed"
  cat "${ART}/dbt-shape.out" >&2 || true
  cat "${ART}/dbt-shape.err" >&2 || true
  exit 1
fi
log "verify: dbt fixture shape ok"
cat "${ART}/dbt-shape.out"

export DBT_PROFILES_DIR="${ROOT}/dbt"
export DBT_TARGET=ci

log "verify: mf validate-configs (compiled semantic_manifest.json)"
set +e
uv run --frozen --group metrics mf validate-configs \
  >"${ART}/mf-validate.out" 2>"${ART}/mf-validate.err"
mf_validate_rc=$?
set -e
write mf-validate.exit "${mf_validate_rc}"
if [[ "${mf_validate_rc}" -ne 0 ]]; then
  log "verify: mf validate-configs failed"
  cat "${ART}/mf-validate.out" >&2 || true
  cat "${ART}/mf-validate.err" >&2 || true
  exit 1
fi
log "verify: mf validate-configs ok"

log "verify: mf query total_deposits,deposits_qoq_growth"
set +e
uv run --frozen --group metrics mf query \
  --quiet \
  --order metric_time__quarter,bank__ticker \
  --metrics total_deposits,deposits_qoq_growth \
  --group-by metric_time__quarter,bank__ticker \
  --where "{{ Dimension('bank__entity_level') }} = 'bank_subsidiary'" \
  --decimals 10 \
  >"${ART}/mf-query.out" 2>"${ART}/mf-query.err"
mf_query_rc=$?
set -e
write mf-query.exit "${mf_query_rc}"
if [[ "${mf_query_rc}" -ne 0 ]] \
  || ! grep -q "total_deposits" "${ART}/mf-query.out" \
  || ! grep -q "deposits_qoq_growth" "${ART}/mf-query.out"; then
  log "verify: mf query failed"
  cat "${ART}/mf-query.out" >&2 || true
  cat "${ART}/mf-query.err" >&2 || true
  exit 1
fi
log "verify: mf query ok"

for mf_saved in lead_bank_deposits_qoq lead_bank_balance_sheet lead_bank_earnings holding_company_assets; do
  log "verify: mf query --saved-query ${mf_saved}"
  set +e
  uv run --frozen --group metrics mf query --quiet --saved-query "${mf_saved}" \
    >"${ART}/mf-saved-${mf_saved}.out" 2>"${ART}/mf-saved-${mf_saved}.err"
  mf_saved_rc=$?
  set -e
  write "mf-saved-${mf_saved}.exit" "${mf_saved_rc}"
  if [[ "${mf_saved_rc}" -ne 0 ]]; then
    log "verify: mf saved query ${mf_saved} failed"
    cat "${ART}/mf-saved-${mf_saved}.out" >&2 || true
    cat "${ART}/mf-saved-${mf_saved}.err" >&2 || true
    exit 1
  fi
  log "verify: mf saved query ${mf_saved} ok"
done

log "verify: pytest -m mf"
set +e
uv run --frozen --group metrics --group dev pytest -m mf \
  >"${ART}/mf-pytest.out" 2>"${ART}/mf-pytest.err"
mf_pytest_rc=$?
set -e
write mf-pytest.exit "${mf_pytest_rc}"
if [[ "${mf_pytest_rc}" -ne 0 ]]; then
  log "verify: pytest -m mf failed"
  cat "${ART}/mf-pytest.out" >&2 || true
  cat "${ART}/mf-pytest.err" >&2 || true
  exit 1
fi
log "verify: pytest -m mf ok"
cat "${ART}/mf-pytest.out"

log "verify: metric_query JPM total_deposits and catalog bytes"
set +e
uv run python - <<'PY' >"${ART}/metric-query.out" 2>"${ART}/metric-query.err"
import os
from pathlib import Path

from capitalcite.metric_query import open_metric_query, run_metric_query
from capitalcite.metric_query.backends import compile_catalog

root = Path.cwd()
backend, citations = open_metric_query(
    backend_name="live",
    warehouse=root / "dbt" / "target" / "ci.duckdb",
    cwd=root,
    env=os.environ,
)
result = run_metric_query(
    {
        "metric": "total_deposits",
        "group_by": "metric_time__quarter,bank__ticker",
        "ticker": "JPM",
        "period": "2026-06-30",
    },
    backend=backend,
    citations=citations,
)
if not result.ok or len(result.hits) != 1:
    raise SystemExit(f"metric_query failed {result.detail}")
quote = result.hits[0].chunk.text
if result.value not in quote:
    raise SystemExit("figure missing from quote")
if not quote.startswith(f"total_deposits for JPM at 2026-06-30 is {result.value}."):
    raise SystemExit(f"quote grammar {quote}")
if "Unit is thousands of USD." not in quote or "FDIC REPDTE" not in quote:
    raise SystemExit(f"quote source {quote}")
print(f"metric_query ok figure={result.value} hits=1")
compiled = root / "dbt" / "target" / "catalog.compiled.json"
compile_catalog(compiled, cwd=root, env=os.environ)
committed = root / "src" / "capitalcite" / "metric_query" / "catalog.json"
if compiled.read_bytes() != committed.read_bytes():
    raise SystemExit("stale catalog")
print("catalog bytes match")
PY
metric_query_rc=$?
set -e
write metric-query.exit "${metric_query_rc}"
if [[ "${metric_query_rc}" -ne 0 ]]; then
  log "verify: metric_query failed"
  cat "${ART}/metric-query.out" >&2 || true
  cat "${ART}/metric-query.err" >&2 || true
  exit 1
fi
log "verify: metric_query ok"
cat "${ART}/metric-query.out"

export WAREHOUSE_PATH="${ROOT}/dbt/target/ci.duckdb"
write warehouse.path "${WAREHOUSE_PATH}"
log "verify: restarting uvicorn with WAREHOUSE_PATH for metric_query /ask/stream"
stop_main_uvicorn
start_main_uvicorn

ASK_METRIC='{"question":"What were JPM'\''s total deposits in 2026Q2?","path":"agent","model":"fake"}'
log "verify: POST /ask path=agent metric_query JPM 2026Q2 deposits model=fake"
post_ask metric-query-ask "${ASK_METRIC}"
log "verify: POST /ask/stream path=agent metric_query JPM 2026Q2 deposits model=fake"
post_ask_stream metric-query-stream "${ASK_METRIC}"
check_ask_stream metric-query-stream metric-query-ask
uv run python - "${ART}" <<'PY'
import json
import sys
from pathlib import Path

art = Path(sys.argv[1])
ask = json.loads((art / "metric-query-ask.body.json").read_text(encoding="utf-8"))
if ask.get("kind") != "answered":
    sys.stderr.write(f"verify: metric_query ask kind={ask.get('kind')!r}\n")
    sys.exit(1)
citations = ask.get("citations") or []
if len(citations) != 1:
    sys.stderr.write(f"verify: metric_query ask citations={len(citations)}\n")
    sys.exit(1)
cite = citations[0]
quote = str(cite.get("quote") or "")
if "total_deposits for JPM at 2026-06-30 is" not in quote:
    sys.stderr.write(f"verify: metric_query quote {quote!r}\n")
    sys.exit(1)
if "FDIC REPDTE" not in quote or "http" not in str(cite.get("source_url") or ""):
    sys.stderr.write(f"verify: metric_query missing FDIC citation {cite!r}\n")
    sys.exit(1)
if str(cite.get("section") or "") != "total_deposits":
    sys.stderr.write(f"verify: metric_query section {cite.get('section')!r}\n")
    sys.exit(1)
answer = str(ask.get("answer") or "")
if "total_deposits for JPM at 2026-06-30 is" not in answer:
    sys.stderr.write(f"verify: metric_query answer {answer!r}\n")
    sys.exit(1)
events = json.loads((art / "metric-query-stream.events.json").read_text(encoding="utf-8"))
names = [str(event.get("event") or "") for event in events]
if "plan" not in names or "tool_call" not in names or "answered" not in names:
    sys.stderr.write(f"verify: metric_query stream events {names}\n")
    sys.exit(1)
called = False
for event in events:
    if event.get("event") != "tool_call":
        continue
    step = event.get("step") or {}
    args = step.get("args") if isinstance(step, dict) else {}
    if (
        isinstance(step, dict)
        and step.get("tool") == "metric_query"
        and isinstance(args, dict)
        and args.get("metric") == "total_deposits"
        and args.get("period") == "2026-06-30"
    ):
        called = True
        break
if not called:
    sys.stderr.write(f"verify: metric_query stream never called the tool {events!r}\n")
    sys.exit(1)
print("metric_query stream answered with FDIC citation")
print(f"answer={answer}")
print(f"quote={quote}")
print(f"source_url={cite.get('source_url')}")
PY
log "verify: POST /ask/stream metric_query ok"

log "verify: eval tags on span JSONL"
uv run python - "${ART}/spans.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.stat().st_size == 0:
    sys.stderr.write("verify: spans.jsonl missing after eval --small\n")
    sys.exit(1)
records = [
    json.loads(line)
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
evals = [row for row in records if row.get("name") == "eval"]
if not evals:
    sys.stderr.write("verify: no eval span in spans.jsonl\n")
    sys.exit(1)
root = evals[-1]
attrs = root.get("attributes") or {}
if not attrs.get("capitalcite.git_sha"):
    sys.stderr.write("verify: eval span missing capitalcite.git_sha\n")
    sys.exit(1)
trace_id = root.get("trace_id")
arm_hashes = [
    (row.get("attributes") or {}).get("capitalcite.config_hash")
    for row in records
    if row.get("name") == "eval-arm" and row.get("trace_id") == trace_id
]
config_hash = attrs.get("capitalcite.config_hash") or next(
    (value for value in arm_hashes if value),
    None,
)
if not config_hash:
    sys.stderr.write("verify: eval span missing capitalcite.config_hash\n")
    sys.exit(1)
print(
    "eval span "
    f"git_sha={attrs['capitalcite.git_sha']} "
    f"config_hash={config_hash}"
)
PY
log "verify: eval span tagged with git_sha and config_hash"

if [[ -f "${ART}/ingest.unreachable" || -f "${ART}/ingest.skipped" ]]; then
  write eval.skipped "live ingest unreachable or --small; corpus eval needs the twelve 10-Ks"
  log "verify: corpus eval UNVERIFIED (no live corpus)"
else
  EVAL_OUT="${ART}/eval"
  mkdir -p "${EVAL_OUT}"
  EVAL_CMD=(
    uv run capitalcite eval --suite golden
    --input-dir "${INGEST_DIR}"
    --out "${EVAL_OUT}"
    --chunker section
    --chunker fixed
    --embedder bge-small
    --k 5
  )
  printf '%s\n' "${EVAL_CMD[*]}" >"${ART}/eval.cmd"
  log "verify: ${EVAL_CMD[*]}"
  set +e
  "${EVAL_CMD[@]}" >"${ART}/eval.out" 2>"${ART}/eval.err"
  eval_rc=$?
  set -e
  write eval.exit "${eval_rc}"
  if [[ "${eval_rc}" -eq 2 ]]; then
    write eval.skipped "corpus eval unverified (exit 2). command: ${EVAL_CMD[*]}"
    log "verify: corpus eval UNVERIFIED (exit 2)"
    cat "${ART}/eval.err" >&2 || true
  elif [[ "${eval_rc}" -ne 0 ]]; then
    log "verify: capitalcite eval failed with exit ${eval_rc}"
    cat "${ART}/eval.out" >&2 || true
    cat "${ART}/eval.err" >&2 || true
    exit 1
  else
    if ! grep -q "Recall@5" "${ART}/eval.out" \
      || ! grep -q "cc-h-013" "${ART}/eval.out"; then
      log "verify: corpus eval stdout missing Recall@5 or cc-h-013 probe"
      cat "${ART}/eval.out" >&2 || true
      exit 1
    fi
    cp "${ART}/eval.out" "${ART}/eval.table.md"
    log "verify: corpus eval ok"
    cat "${ART}/eval.out"
  fi
fi

log "verify: live Langfuse :3000"
set +e
langfuse_http="$(
  curl -sS -o "${ART}/langfuse.health.out" -w "%{http_code}" \
    --connect-timeout 1 --max-time 2 \
    "http://127.0.0.1:3000" 2>"${ART}/langfuse.health.err"
)"
langfuse_rc=$?
set -e
write langfuse.health.exit "${langfuse_rc}"
write langfuse.health.http "${langfuse_http:-}"
if [[ "${langfuse_rc}" -eq 0 && "${langfuse_http}" =~ ^(200|302|307|308)$ ]]; then
  write langfuse.reachable "http://127.0.0.1:3000 HTTP ${langfuse_http}. UI screenshot still UNVERIFIED (this run did not capture the Langfuse UI)."
  log "verify: Langfuse :3000 reachable; live UI screenshot UNVERIFIED"
else
  write langfuse.skipped "nothing healthy on 127.0.0.1:3000 (curl_exit=${langfuse_rc} http=${langfuse_http:-}). live trace ingestion UNVERIFIED"
  log "verify: Langfuse not listening; live ingestion UNVERIFIED"
fi

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
if "qdrant/qdrant:v1.16.2" not in text:
    errors.append("missing qdrant/qdrant:v1.16.2")
compose_src = Path("docker-compose.yml").read_text(encoding="utf-8")
if "neo4j:5.26-community" not in compose_src:
    errors.append("missing neo4j:5.26-community in docker-compose.yml")
if re.search(r"langfuse/langfuse:4\s*$", text, re.M) or re.search(
    r"langfuse/langfuse-worker:4\s*$", text, re.M
):
    errors.append("floating :4 tag still present")
# Long-form compose config is host_ip, then optional target, then published.
for published in ("6333", "6334", "9090"):
    block = re.search(
        rf"host_ip:\s*127\.0\.0\.1(?:\n[^\n]+){{0,4}}\n\s*published:\s*[\"']?{published}[\"']?",
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

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker)
else
  DOCKER=()
fi
if [[ ${#DOCKER[@]} -eq 0 ]]; then
  write image.skipped "UNVERIFIED: docker is not installed or this user cannot talk to the daemon"
  log "verify: image UNVERIFIED (no usable docker)"
else
  log "verify: make image IMAGE=capitalcite:verify"
  set +e
  make image IMAGE=capitalcite:verify >"${ART}/image.build.out" 2>"${ART}/image.build.err"
  image_build_rc=$?
  set -e
  write image.build.exit "${image_build_rc}"
  if [[ "${image_build_rc}" -ne 0 ]]; then
    log "verify: make image failed"
    cat "${ART}/image.build.err" >&2 || true
    exit 1
  fi
  log "verify: CLI help from the image"
  set +e
  "${DOCKER[@]}" run --rm --entrypoint capitalcite capitalcite:verify --help \
    >"${ART}/image.help.out" 2>"${ART}/image.help.err"
  image_help_rc=$?
  set -e
  write image.help.exit "${image_help_rc}"
  if [[ "${image_help_rc}" -ne 0 ]]; then
    log "verify: image capitalcite --help failed"
    cat "${ART}/image.help.err" >&2 || true
    exit 1
  fi
  if ! plain "${ART}/image.help.out" | grep -q "ingest"; then
    log "verify: image --help missing ingest"
    exit 1
  fi
  IMAGE_PORT="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  write image.port "${IMAGE_PORT}"
  image_cid="$("${DOCKER[@]}" run -d --name "capitalcite-verify-${TS}" \
    -e SEC_USER_AGENT='capitalcite-verify you@example.com' \
    -p "127.0.0.1:${IMAGE_PORT}:8000" \
    capitalcite:verify)"
  image_cleanup() {
    "${DOCKER[@]}" stop "${image_cid}" >/dev/null 2>&1 || true
    "${DOCKER[@]}" rm -f "${image_cid}" >/dev/null 2>&1 || true
  }
  trap 'image_cleanup; cleanup' EXIT INT TERM
  image_ready=0
  for _ in $(seq 1 40); do
    if body="$(curl -sf "http://127.0.0.1:${IMAGE_PORT}/health")"; then
      printf '%s\n' "$body" >"${ART}/image.health.body"
      image_ready=1
      break
    fi
    sleep 1
  done
  if [[ "${image_ready}" -ne 1 ]]; then
    "${DOCKER[@]}" logs "${image_cid}" >"${ART}/image.health.log" 2>&1 || true
    image_cleanup
    trap cleanup EXIT INT TERM
    log "verify: image /health did not return 200"
    exit 1
  fi
  image_cleanup
  trap cleanup EXIT INT TERM
  uv run python - "${ART}/image.health.body" <<'PY'
import json
import sys
from pathlib import Path

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
errors = []
sha = body.get("git_sha")
count = body.get("point_count")
if body.get("status") != "ok":
    errors.append(f"status={body.get('status')!r}")
if not isinstance(sha, str) or not sha.strip():
    errors.append(f"git_sha={sha!r}")
if isinstance(count, bool) or not isinstance(count, int) or count < 1:
    errors.append(f"point_count={count!r}")
if errors:
    sys.stderr.write("verify: image /health failed: " + ", ".join(errors) + "\n")
    sys.exit(1)
print(json.dumps({"git_sha": sha, "point_count": count}))
PY
  log "verify: image /health ok"
fi

web_ask() {
  if [[ -f "${ART}/stream.unreachable" ]]; then
    write web.unreachable "POST /ask/stream was unverified"
    log "verify: web ask UNVERIFIED (stream unreachable)"
    return 0
  fi
  if ! command -v pnpm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
    write web.unreachable "pnpm or node is missing"
    log "verify: web ask UNVERIFIED (pnpm or node missing)"
    return 0
  fi

  log "verify: pnpm --dir web install --frozen-lockfile"
  if ! pnpm --dir web install --frozen-lockfile >"${ART}/web.install.log" 2>&1; then
    log "verify: web install failed"
    cat "${ART}/web.install.log" >&2 || true
    exit 1
  fi

  local web_port
  web_port="$(
    uv run python - <<'PY'
import socket

while True:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    if port != 3000:
        print(port)
        break
PY
  )"
  write web.port "${web_port}"
  log "verify: pnpm --dir web build"
  if ! CAPITALCITE_API_ORIGIN="http://127.0.0.1:${PORT}" ASK_MODEL=fake \
    pnpm --dir web build >"${ART}/web.build.log" 2>&1; then
    log "verify: web build failed"
    cat "${ART}/web.build.log" >&2 || true
    exit 1
  fi

  log "verify: launching Next on 127.0.0.1:${web_port}"
  CAPITALCITE_API_ORIGIN="http://127.0.0.1:${PORT}" ASK_MODEL=fake \
    pnpm --dir web exec next start --hostname 127.0.0.1 --port "${web_port}" \
    >"${ART}/web.dev.log" 2>&1 &
  NEXT_PID=$!

  local ready=0
  local i
  for i in $(seq 1 180); do
    if curl -sf -o /dev/null "http://127.0.0.1:${web_port}/"; then
      ready=1
      break
    fi
    if ! kill -0 "${NEXT_PID}" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if [[ "${ready}" -ne 1 ]]; then
    log "verify: Next did not answer on 127.0.0.1:${web_port}"
    cat "${ART}/web.dev.log" >&2 || true
    stop_next
    exit 1
  fi

  if ! curl -sf "http://127.0.0.1:${web_port}/" >"${ART}/web.page.html"; then
    log "verify: web page curl failed"
    stop_next
    exit 1
  fi
  if ! grep -q "Ask a filing question" "${ART}/web.page.html"; then
    log "verify: web page missing heading"
    stop_next
    exit 1
  fi
  if ! grep -q "deterministic demo model" "${ART}/web.page.html"; then
    log "verify: web page missing demo label"
    stop_next
    exit 1
  fi

  set +e
  node web/scripts/verify-browser.mjs \
    --base-url "http://127.0.0.1:${web_port}" \
    --out "${ART}" \
    >"${ART}/web.browser.log" 2>&1
  local browser_rc=$?
  set -e
  write web.exit "${browser_rc}"
  if [[ "${browser_rc}" -eq 2 ]]; then
    write web.browser.unverified "browser driver unavailable"
    log "verify: web ask UNVERIFIED (browser)"
    cat "${ART}/web.browser.log" >&2 || true
    stop_next
    return 0
  fi
  if [[ "${browser_rc}" -ne 0 ]]; then
    log "verify: web ask failed"
    cat "${ART}/web.browser.log" >&2 || true
    stop_next
    exit 1
  fi
  log "verify: web ask ok"
  stop_next
}

web_ask

{
  echo "timestamp ${TS}"
  echo "writeup-numbers.exit $(cat "${ART}/writeup-numbers.exit")"
  echo "port ${PORT}"
  echo "uvicorn.pid ${UVICORN_PID}"
  echo "health.http $(cat "${ART}/health.http")"
  echo "health.exit $(cat "${ART}/health.exit")"
  echo "version ${ACTUAL_VERSION}"
  echo "version.exit $(cat "${ART}/version.exit")"
  echo "golden.help.exit $(cat "${ART}/golden.help.exit")"
  echo "golden.exit $(cat "${ART}/golden.exit")"
  echo "gate.help.exit $(cat "${ART}/gate.help.exit")"
  echo "gate.validate.exit $(cat "${ART}/gate.validate.exit")"
  echo "gate.decide.unknown.exit $(cat "${ART}/gate.decide.unknown.exit")"
  echo "gate.decide.invalid.exit $(cat "${ART}/gate.decide.invalid.exit")"
  echo "gate.decide.broken.exit $(cat "${ART}/gate.decide.broken.exit")"
  echo "gate.redteam.exit $(cat "${ART}/gate.redteam.exit")"
  echo "gate.bench.exit $(cat "${ART}/gate.bench.exit")"
  echo "gate.bench $(cat "${ART}/gate.bench.body.json")"
  echo "audit.help.exit $(cat "${ART}/audit.help.exit")"
  echo "audit.ok.exit $(cat "${ART}/audit.ok.exit")"
  echo "audit.tamper.exit $(cat "${ART}/audit.tamper.exit")"
  echo "audit.removed.exit $(cat "${ART}/audit.removed.exit")"
  if [[ -f "${ART}/audit.agent.skipped" ]]; then
    echo "audit agent skipped ($(cat "${ART}/audit.agent.skipped"))"
  elif [[ -f "${ART}/audit.agent.exit" ]]; then
    echo "audit.agent.exit $(cat "${ART}/audit.agent.exit")"
  fi
  echo "ingest.help.exit $(cat "${ART}/ingest.help.exit")"
  if [[ -f "${ART}/ingest.skipped" ]]; then
    echo "ingest skipped ($(cat "${ART}/ingest.skipped"))"
  elif [[ -f "${ART}/ingest.unreachable" ]]; then
    echo "ingest unreachable ($(cat "${ART}/ingest.unreachable"))"
  else
    echo "ingest.exit $(cat "${ART}/ingest.exit")"
    echo "ingest.rerun.exit $(cat "${ART}/ingest.rerun.exit")"
  fi
  if [[ -f "${ART}/golden.against-raw.exit" ]]; then
    echo "golden.against-raw.exit $(cat "${ART}/golden.against-raw.exit")"
  elif [[ -f "${ART}/golden.against-raw.skipped" ]]; then
    echo "golden-against-raw skipped ($(cat "${ART}/golden.against-raw.skipped"))"
  fi
  echo "chunk.help.exit $(cat "${ART}/chunk.help.exit")"
  echo "chunk.exit $(cat "${ART}/chunk.exit")"
  echo "mcp.help.exit $(cat "${ART}/mcp.help.exit")"
  echo "mcp.index.exit $(cat "${ART}/mcp.index.exit")"
  echo "mcp.probe.exit $(cat "${ART}/mcp.probe.exit")"
  echo "mcp.audit.exit $(cat "${ART}/mcp.audit.exit")"
  echo "index.help.exit $(cat "${ART}/index.help.exit")"
  if [[ -f "${ART}/index.1.exit" ]]; then
    echo "index.1.exit $(cat "${ART}/index.1.exit")"
  fi
  if [[ -f "${ART}/index.2.exit" ]]; then
    echo "index.2.exit $(cat "${ART}/index.2.exit")"
  fi
  if [[ -f "${ART}/search.unreachable" ]]; then
    echo "search unreachable ($(cat "${ART}/search.unreachable"))"
  else
    echo "search.http $(cat "${ART}/search.http")"
    echo "search.exit $(cat "${ART}/search.exit")"
  fi
  if [[ -f "${ART}/ask.unreachable" ]]; then
    echo "ask unreachable ($(cat "${ART}/ask.unreachable"))"
  elif [[ -f "${ART}/ask.http" ]]; then
    echo "ask.http $(cat "${ART}/ask.http")"
    echo "ask.exit $(cat "${ART}/ask.exit")"
  fi
  if [[ -f "${ART}/stream.unreachable" ]]; then
    echo "stream unreachable ($(cat "${ART}/stream.unreachable"))"
  elif [[ -f "${ART}/stream.http" ]]; then
    echo "stream.http $(cat "${ART}/stream.http")"
    echo "stream.exit $(cat "${ART}/stream.exit")"
  fi
  if [[ -f "${ART}/web.unreachable" ]]; then
    echo "web unreachable ($(cat "${ART}/web.unreachable"))"
  elif [[ -f "${ART}/web.browser.unverified" ]]; then
    echo "web browser unverified ($(cat "${ART}/web.browser.unverified"))"
  elif [[ -f "${ART}/web.exit" ]]; then
    echo "web.exit $(cat "${ART}/web.exit")"
  fi
  if [[ -f "${ART}/ask.openai.unverified" ]]; then
    echo "ask openai unverified ($(cat "${ART}/ask.openai.unverified"))"
  elif [[ -f "${ART}/ask.openai.http" ]]; then
    echo "ask.openai.http $(cat "${ART}/ask.openai.http")"
  fi
  if [[ -f "${ART}/agent.unreachable" ]]; then
    echo "agent unreachable ($(cat "${ART}/agent.unreachable"))"
  elif [[ -f "${ART}/agent.cc-h-055.http" ]]; then
    echo "agent.cc-h-055.http $(cat "${ART}/agent.cc-h-055.http")"
    echo "agent.cc-h-061.http $(cat "${ART}/agent.cc-h-061.http")"
  fi
  if [[ -f "${ART}/agent.openai.unverified" ]]; then
    echo "agent openai unverified ($(cat "${ART}/agent.openai.unverified"))"
  elif [[ -f "${ART}/agent.openai.http" ]]; then
    echo "agent.openai.http $(cat "${ART}/agent.openai.http")"
  fi
  if [[ -f "${ART}/quote-sanity.skipped" ]]; then
    echo "quote-sanity skipped ($(cat "${ART}/quote-sanity.skipped"))"
  else
    echo "quote-sanity.exit $(cat "${ART}/quote-sanity.exit")"
  fi
  echo "eval.help.exit $(cat "${ART}/eval.help.exit")"
  echo "eval-small.exit $(cat "${ART}/eval-small.exit")"
  echo "check.help.exit $(cat "${ART}/check.help.exit")"
  echo "check.exit $(cat "${ART}/check.exit")"
  if [[ -f "${ART}/check.live.unverified" ]]; then
    echo "check live unverified ($(cat "${ART}/check.live.unverified"))"
  else
    echo "check.live.exit $(cat "${ART}/check.live.exit")"
  fi
  echo "graph.help.exit $(cat "${ART}/graph.help.exit")"
  echo "graph.load.exit $(cat "${ART}/graph.load.exit")"
  echo "graph.query.exit $(cat "${ART}/graph.query.exit")"
  echo "eval-graph-small.exit $(cat "${ART}/eval-graph-small.exit")"
  echo "metrics.help.exit $(cat "${ART}/metrics.help.exit")"
  echo "metrics.exit $(cat "${ART}/metrics.exit")"
  echo "dbt.exit $(cat "${ART}/dbt.exit")"
  echo "dbt-docs.exit $(cat "${ART}/dbt-docs.exit")"
  echo "dbt-shape.exit $(cat "${ART}/dbt-shape.exit")"
  echo "mf-validate.exit $(cat "${ART}/mf-validate.exit")"
  echo "mf-query.exit $(cat "${ART}/mf-query.exit")"
  echo "mf-saved-lead_bank_deposits_qoq.exit $(cat "${ART}/mf-saved-lead_bank_deposits_qoq.exit")"
  echo "mf-saved-lead_bank_balance_sheet.exit $(cat "${ART}/mf-saved-lead_bank_balance_sheet.exit")"
  echo "mf-saved-lead_bank_earnings.exit $(cat "${ART}/mf-saved-lead_bank_earnings.exit")"
  echo "mf-saved-holding_company_assets.exit $(cat "${ART}/mf-saved-holding_company_assets.exit")"
  echo "mf-pytest.exit $(cat "${ART}/mf-pytest.exit")"
  if [[ -f "${ART}/eval.skipped" ]]; then
    echo "eval skipped ($(cat "${ART}/eval.skipped"))"
  else
    echo "eval.exit $(cat "${ART}/eval.exit")"
  fi
  if [[ -f "${ART}/spans.jsonl" ]]; then
    echo "spans.jsonl $(wc -l <"${ART}/spans.jsonl" | tr -d ' ') lines"
  elif [[ -f "${ART}/spans.skipped" ]]; then
    echo "spans skipped ($(cat "${ART}/spans.skipped"))"
  fi
  if [[ -f "${ART}/langfuse.skipped" ]]; then
    echo "langfuse skipped ($(cat "${ART}/langfuse.skipped"))"
  elif [[ -f "${ART}/langfuse.reachable" ]]; then
    echo "langfuse reachable ($(cat "${ART}/langfuse.reachable"))"
  fi
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
  echo "cors.allowed.exit $(cat "${ART}/cors.allowed.exit")"
  echo "limit.ask.3.http $(cat "${ART}/limit.ask.3.http")"
  echo "limit.stream.http $(cat "${ART}/limit.stream.http")"
  echo "limit.health.after.http $(cat "${ART}/limit.health.after.http")"
  if [[ -f "${ART}/concurrent.stream.codes" ]]; then
    echo "concurrent.stream.codes $(cat "${ART}/concurrent.stream.codes")"
    echo "concurrent.stream.exit $(cat "${ART}/concurrent.stream.exit")"
  fi
  if [[ -f "${ART}/image.skipped" ]]; then
    echo "image skipped ($(cat "${ART}/image.skipped"))"
  else
    echo "image.help.exit $(cat "${ART}/image.help.exit")"
    echo "image.health $(cat "${ART}/image.health.body")"
  fi
  echo "artifacts ${ART}"
} | tee "${ART}/summary.txt"

log "verify: OK"
