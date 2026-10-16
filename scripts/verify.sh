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

# Typer/Rich may insert SGR codes between the dashes in "--against-raw".
plain() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g' "$1"
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

if [[ -f "${ART}/ingest.unreachable" ]]; then
  write search.unreachable "live ingest unreachable; embedded index needs JPM FY24 and WFC FY25 chunks"
  write ask.unreachable "POST /search was unverified; POST /ask needs the same index"
  write agent.unreachable "POST /search was unverified; POST /ask path=agent needs the same index"
  write spans.skipped "POST /ask was unverified; span JSONL needs a fake /ask"
  write audit.agent.skipped "POST /ask path=agent was unverified; live one-record check needs traces"
  log "verify: POST /search UNVERIFIED (no ingested corpus)"
  log "verify: POST /ask UNVERIFIED (no ingested corpus)"
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

  Q013='{"query":"What was JPMorgan'\''s Standardized CET1 capital ratio at year-end 2024?","mode":"hybrid","k":5}'
  post_search search.repro.1 "${Q013}"

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
print(f"audit live records={len(records)} traces={expected}")
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
  || ! plain "${ART}/eval.help.out" | grep -q -- "--small"; then
  log "verify: eval --help missing --suite or --small"
  cat "${ART}/eval.help.out" >&2 || true
  exit 1
fi
log "verify: capitalcite eval --help ok"

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

if [[ -f "${ART}/ingest.unreachable" ]]; then
  write eval.skipped "live ingest unreachable; corpus eval needs the twelve 10-Ks"
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

{
  echo "timestamp ${TS}"
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
  if [[ -f "${ART}/ingest.unreachable" ]]; then
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
  echo "index.help.exit $(cat "${ART}/index.help.exit")"
  if [[ -f "${ART}/index.1.exit" ]]; then
    echo "index.1.exit $(cat "${ART}/index.1.exit")"
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
  echo "artifacts ${ART}"
} | tee "${ART}/summary.txt"

log "verify: OK"
