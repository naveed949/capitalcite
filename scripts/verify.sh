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

log "verify: POST /search"
SEARCH_BODY='{"query":"CET1 capital ratio","mode":"hybrid","k":5}'
printf '%s\n' "${SEARCH_BODY}" >"${ART}/search.request.json"
set +e
search_http="$(
  curl -sS --max-time 20 \
    -D "${ART}/search.headers" \
    -o "${ART}/search.body.json" \
    -w "%{http_code}" \
    -H 'content-type: application/json' \
    -d "${SEARCH_BODY}" \
    "http://127.0.0.1:${PORT}/search"
)"
search_curl_rc=$?
set -e
write search.exit "${search_curl_rc}"
write search.http "${search_http}"
if [[ "${search_curl_rc}" -ne 0 ]]; then
  write search.unreachable "curl POST /search failed (exit ${search_curl_rc})"
  log "verify: POST /search UNVERIFIED (curl exit ${search_curl_rc})"
elif [[ "${search_http}" == "503" ]]; then
  write search.unreachable "POST /search HTTP 503. Qdrant is down or the index is not built."
  log "verify: POST /search UNVERIFIED (HTTP 503)"
elif [[ "${search_http}" != "200" ]]; then
  log "verify: POST /search HTTP ${search_http}"
  cat "${ART}/search.body.json" >&2 || true
  exit 1
else
  set +e
  uv run python - "${ART}/search.body.json" <<'PY'
import json
import sys
from pathlib import Path

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
point_count = body.get("point_count")
hits = body.get("hits")
if not isinstance(point_count, int):
    sys.stderr.write("verify: search body missing point_count\n")
    sys.exit(1)
if point_count == 0 or not hits:
    hit_count = 0 if not isinstance(hits, list) else len(hits)
    sys.stderr.write(f"verify: search point_count={point_count} hits={hit_count}\n")
    sys.exit(2)
required = ("doc_id", "section", "start", "end", "source_url", "text")
for hit in hits:
    if not isinstance(hit, dict):
        sys.stderr.write("verify: search hit was not an object\n")
        sys.exit(1)
    score = hit.get("score")
    chunk = hit.get("chunk")
    if not isinstance(score, (int, float)) or not isinstance(chunk, dict):
        sys.stderr.write("verify: search hit missing score or chunk\n")
        sys.exit(1)
    missing = [key for key in required if key not in chunk]
    if missing:
        sys.stderr.write("verify: search hit missing " + ", ".join(missing) + "\n")
        sys.exit(1)
print(f"search hits {len(hits)} points {point_count}")
PY
  search_check_rc=$?
  set -e
  if [[ "${search_check_rc}" -eq 2 ]]; then
    write search.unreachable "POST /search HTTP 200 with an empty index or no hits"
    log "verify: POST /search UNVERIFIED (empty index or no hits)"
  elif [[ "${search_check_rc}" -ne 0 ]]; then
    log "verify: POST /search body failed citation checks"
    exit 1
  else
    log "verify: POST /search returned cited hits"
  fi
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
  if [[ -f "${ART}/search.unreachable" ]]; then
    echo "search unreachable ($(cat "${ART}/search.unreachable"))"
  else
    echo "search.http $(cat "${ART}/search.http")"
    echo "search.exit $(cat "${ART}/search.exit")"
  fi
  if [[ -f "${ART}/quote-sanity.skipped" ]]; then
    echo "quote-sanity skipped ($(cat "${ART}/quote-sanity.skipped"))"
  else
    echo "quote-sanity.exit $(cat "${ART}/quote-sanity.exit")"
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
