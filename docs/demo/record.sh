#!/usr/bin/env bash
# Record docs/demo/quickstart.sh as an asciinema cast. Run from the repo root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

CAST="${ROOT}/docs/demo/quickstart.cast"
if ! command -v asciinema >/dev/null 2>&1; then
  echo "record: install asciinema (uv tool install asciinema)" >&2
  exit 1
fi

script -qefc "asciinema rec --overwrite -c '${ROOT}/docs/demo/quickstart.sh' '${CAST}'" /dev/null
echo "record: wrote ${CAST}"
