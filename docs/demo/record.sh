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

export COLUMNS="${COLUMNS:-120}"
export LINES="${LINES:-32}"
script -qefc "asciinema rec --overwrite --cols 120 --rows 32 -c '${ROOT}/docs/demo/quickstart.sh' '${CAST}'" /dev/null
uv run python - "${CAST}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
header = json.loads(lines[0])
header["width"] = max(int(header.get("width") or 0), 120)
header["height"] = max(int(header.get("height") or 0), 32)
path.write_text(
    json.dumps(header, separators=(",", ":")) + "\n" + "\n".join(lines[1:]) + "\n",
    encoding="utf-8",
)
print(f"record: wrote {path} {header['width']}x{header['height']}")
PY
