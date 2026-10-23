#!/usr/bin/env bash
# Measure the API image. Writes raw command output under docs/deploy/.
# Usage, from the repo root: ./scripts/measure_image.sh
# IMAGE defaults to capitalcite:local. One run. An OOM is recorded, not hidden.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${IMAGE:-capitalcite:local}"
OUT="${ROOT}/docs/deploy"
mkdir -p "$OUT"
RAW="${OUT}/image-measure.raw.txt"
STATS="${OUT}/image-measure.stats.txt"
LOG="${OUT}/image-measure.container.log"
HEALTH="${OUT}/image-measure.health.json"
ASK1="${OUT}/image-measure.ask1.body"
METHOD="${OUT}/image-measure.md"
NAME="capitalcite-measure-$$"

: >"$RAW"
: >"$STATS"
: >"$LOG"
: >"$HEALTH"
: >"$ASK1"

log() {
  printf '%s\n' "$*" | tee -a "$RAW"
}

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker)
else
  DOCKER=()
fi

write_method() {
  python3 - "$RAW" "$METHOD" "$IMAGE" <<'PY'
import sys
from pathlib import Path

raw_path, method_path, image = sys.argv[1:]
raw = Path(raw_path).read_text(encoding="utf-8", errors="replace")
wanted = []
for line in raw.splitlines():
    if (
        line.startswith(
            (
                "Size=",
                "inspect_size_bytes=",
                "save_bytes=",
                "unpacked_",
                "cold_start",
                "first_answer",
                "health_json=",
                "ask ",
                "ask_http_",
                "concurrent_",
                "OOMKilled=",
                "peak_",
                "vmrss_kb_samples=",
                "vmrss_kb_max=",
                "UNVERIFIED",
                "host_port=",
                "disk ",
                "VmRSS:",
                "VmHWM:",
                "VmPeak:",
                "Rss:",
                "Pss:",
                "nproc=",
                "uptime=",
            )
        )
    ):
        wanted.append(line)
kept = "\n".join(wanted)
text = f"""# API image measurement

One run of `scripts/measure_image.sh`. The lines in Raw lines are copied from `image-measure.raw.txt`. This page does not round them.

## Commands

`make image IMAGE={image}` builds the image when the script runs.

```text
docker run -d --memory 512m --memory-swap 512m -p 127.0.0.1::8000 -e SEC_USER_AGENT='capitalcite-measure you@example.com' -e ASK_MODEL=fake {image}
```

`--memory-swap 512m` is set equal to `--memory` so the cgroup cannot add swap past 512m. On this machine the script uses `sudo -n docker` when `docker info` is denied.

`nproc` and `uptime` are in the raw log. This host reported `nproc=4`. `cold_start_s` is container start until the first `/health` 200, before FastEmbed loads. `first_answer_s` is that same start until the first `POST /ask/stream` finishes. `inspect_size_bytes` is `docker image inspect .Size` (uncompressed layers). `save_bytes` is `docker save | wc -c`. `unpacked_root_bytes` is `du -sb /` inside the running container. Stats samples are `image-measure.stats.txt`. Container logs are `image-measure.container.log`. The `/health` body is `image-measure.health.json`. The first `/ask/stream` body is `image-measure.ask1.body`.

## Raw lines

```text
{kept}
```
"""
Path(method_path).write_text(text, encoding="utf-8")
PY
}

if [[ ${#DOCKER[@]} -eq 0 ]]; then
  log "UNVERIFIED: docker is not installed or this user cannot talk to the daemon"
  log "missing precondition: a usable docker daemon"
  write_method
  exit 0
fi

log "nproc=$(nproc)"
log "uptime=$(uptime)"
log "command: make image IMAGE=${IMAGE}"
if ! make image "IMAGE=${IMAGE}" >>"$RAW" 2>&1; then
  log "make image failed"
  write_method
  exit 1
fi

log "command: ${DOCKER[*]} image inspect ${IMAGE} --format Size={{.Size}}"
if ! "${DOCKER[@]}" image inspect "$IMAGE" --format 'Size={{.Size}}' | tee -a "$RAW"; then
  log "image inspect failed"
  write_method
  exit 1
fi
inspect_size="$("${DOCKER[@]}" image inspect "$IMAGE" --format '{{.Size}}')"
log "inspect_size_bytes=${inspect_size}"
log "command: ${DOCKER[*]} save ${IMAGE} | wc -c"
save_bytes="$("${DOCKER[@]}" save "$IMAGE" | wc -c | tr -d ' ')"
log "save_bytes=${save_bytes}"

# memory-swap equals --memory so the cgroup cannot add swap past 512m.
RUN_CMD=(
  "${DOCKER[@]}" run -d --name "$NAME"
  --memory 512m --memory-swap 512m
  -p 127.0.0.1::8000
  -e SEC_USER_AGENT='capitalcite-measure you@example.com'
  -e ASK_MODEL=fake
  "$IMAGE"
)
log "command: ${RUN_CMD[*]}"
start_ns="$(date +%s%N)"
if ! "${RUN_CMD[@]}" >>"$RAW" 2>&1; then
  log "docker run failed"
  write_method
  exit 1
fi

cleanup() {
  if [[ ${#DOCKER[@]} -gt 0 ]]; then
    "${DOCKER[@]}" rm -f "$NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

port=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  port="$("${DOCKER[@]}" port "$NAME" 8000/tcp 2>/dev/null | awk -F: 'NR==1 {print $NF}')"
  if [[ -n "$port" ]]; then
    break
  fi
  sleep 0.2
done
log "host_port=${port:-missing}"

sample_pid=""
(
  while "${DOCKER[@]}" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; do
    "${DOCKER[@]}" stats --no-stream --format '{{.MemUsage}} {{.MemPerc}} cpu={{.CPUPerc}}' "$NAME" >>"$STATS" 2>/dev/null || true
    rss="$("${DOCKER[@]}" exec "$NAME" awk '/^VmRSS:/ {print $2}' /proc/1/status 2>/dev/null || true)"
    if [[ -n "${rss}" ]]; then
      printf 'vmrss_kb=%s\n' "${rss}" >>"$STATS"
    fi
    sleep 0.2
  done
) &
sample_pid=$!

health_ok=0
end_ns=""
for _ in $(seq 1 90); do
  running="$("${DOCKER[@]}" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)"
  if [[ "$running" != "true" ]]; then
    log "container stopped before /health 200"
    break
  fi
  if [[ -z "$port" ]]; then
    sleep 1
    continue
  fi
  code="$(curl -sS -o "$HEALTH" -w '%{http_code}' --max-time 2 "http://127.0.0.1:${port}/health" || true)"
  log "health_poll http=${code:-none}"
  if [[ "$code" == "200" ]]; then
    health_ok=1
    end_ns="$(date +%s%N)"
    break
  fi
  sleep 1
done

if [[ "$health_ok" -eq 1 ]]; then
  python3 - "$start_ns" "$end_ns" <<'PY' | tee -a "$RAW"
import sys

start = int(sys.argv[1])
end = int(sys.argv[2])
delta = end - start
print(f"cold_start_ns={delta}")
print(f"cold_start_s={delta / 1_000_000_000}")
PY
  log "health_json=$(tr -d '\n' <"$HEALTH")"
else
  log "cold_start=UNVERIFIED health never returned 200"
fi

ask_ok=0
ask_fail=0
ask_codes=""
if [[ "$health_ok" -eq 1 && -n "$port" ]]; then
  payload='{"question":"What buffer did the filing describe?","model":"fake"}'
  log "command: 20x POST /ask/stream model=fake"
  for i in $(seq 1 20); do
    running="$("${DOCKER[@]}" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)"
    if [[ "$running" != "true" ]]; then
      log "ask ${i} container stopped"
      ask_fail=$((ask_fail + 1))
      ask_codes="${ask_codes} stop"
      break
    fi
    body_file="${OUT}/image-measure.ask.body"
    code="$(curl -sS -o "$body_file" -w '%{http_code}' --max-time 180 \
      -X POST "http://127.0.0.1:${port}/ask/stream" \
      -H 'content-type: application/json' \
      -d "$payload" || true)"
    if [[ "$i" -eq 1 ]]; then
      cp "$body_file" "$ASK1"
      first_end_ns="$(date +%s%N)"
      python3 - "$start_ns" "$first_end_ns" "$code" <<'PY' | tee -a "$RAW"
import sys

start = int(sys.argv[1])
end = int(sys.argv[2])
code = sys.argv[3]
delta = end - start
print(f"first_answer_http={code}")
print(f"first_answer_ns={delta}")
print(f"first_answer_s={delta / 1_000_000_000}")
PY
    fi
    bytes="$(wc -c <"$body_file" | tr -d ' ')"
    log "ask ${i} http=${code:-none} bytes=${bytes}"
    ask_codes="${ask_codes} ${code:-none}"
    if [[ "$code" == "200" ]]; then
      ask_ok=$((ask_ok + 1))
    else
      ask_fail=$((ask_fail + 1))
    fi
  done
else
  log "asks skipped because /health did not return 200"
fi
log "ask_http_200=${ask_ok} ask_not_200=${ask_fail} codes=${ask_codes}"

if [[ "$health_ok" -eq 1 && -n "$port" ]]; then
  log "command: 6 parallel POST /ask/stream"
  python3 - "$port" "$OUT" <<'PY' | tee -a "$RAW"
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

port = sys.argv[1]
out = Path(sys.argv[2])
payload = json.dumps({"question": "What buffer did the filing describe?", "model": "fake"}).encode()


def one(index: int) -> tuple[int, str, int]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ask/stream",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read()
            return index, str(resp.status), len(body)
    except urllib.error.HTTPError as exc:
        return index, str(exc.code), len(exc.read())
    except Exception as exc:
        return index, f"err:{exc}", 0


with ThreadPoolExecutor(max_workers=6) as pool:
    rows = [future.result() for future in as_completed(pool.submit(one, i) for i in range(6))]
rows.sort()
ok = sum(1 for _, status, _ in rows if status == "200")
codes = " ".join(status for _, status, _ in rows)
(out / "image-measure.concurrent.codes").write_text(codes + "\n", encoding="utf-8")
print(f"concurrent_http_200={ok} concurrent_not_200={6 - ok} concurrent_codes={codes}")
for index, status, nbytes in rows:
    print(f"concurrent {index + 1} http={status} bytes={nbytes}")
PY
else
  log "concurrent_http_200=UNVERIFIED sequential asks did not run"
fi

log "command: ${DOCKER[*]} inspect State"
"${DOCKER[@]}" inspect "$NAME" --format 'OOMKilled={{.State.OOMKilled}} Status={{.State.Status}} ExitCode={{.State.ExitCode}} Error={{.State.Error}}' | tee -a "$RAW" || true

if "${DOCKER[@]}" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; then
  log "command: du -sb /app/fastembed /app/qdrant /app/.venv /"
  while IFS= read -r line; do
    log "disk ${line}"
  done < <("${DOCKER[@]}" exec "$NAME" du -sb /app/fastembed /app/qdrant /app/.venv / || true)
  root_bytes="$("${DOCKER[@]}" exec "$NAME" du -sb / | awk '{print $1}')"
  log "unpacked_root_bytes=${root_bytes}"
  log "command: /proc/1/status"
  "${DOCKER[@]}" exec "$NAME" cat /proc/1/status | tee -a "$RAW" || true
  if "${DOCKER[@]}" exec "$NAME" test -r /proc/1/smaps_rollup; then
    log "command: /proc/1/smaps_rollup"
    "${DOCKER[@]}" exec "$NAME" cat /proc/1/smaps_rollup | tee -a "$RAW" || true
  fi
fi

log "command: ${DOCKER[*]} logs"
"${DOCKER[@]}" logs "$NAME" >"$LOG" 2>&1 || true
log "container_log_bytes=$(wc -c <"$LOG" | tr -d ' ')"

if [[ -n "$sample_pid" ]]; then
  kill "$sample_pid" >/dev/null 2>&1 || true
  wait "$sample_pid" 2>/dev/null || true
  sample_pid=""
fi

if [[ -s "$STATS" ]]; then
  log "stats_samples:"
  tee -a "$RAW" <"$STATS"
  python3 - "$STATS" <<'PY' | tee -a "$RAW"
import re
import sys
from pathlib import Path

units = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "kB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
}
pattern = re.compile(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]+)")
best = None
best_line = ""
vmrss = []
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.startswith("vmrss_kb="):
        vmrss.append(int(line.split("=", 1)[1]))
        continue
    if "/" not in line:
        continue
    match = pattern.search(line)
    if match is None:
        continue
    number = float(match.group(1))
    unit = match.group(2)
    if unit not in units:
        continue
    value = number * units[unit]
    if best is None or value > best:
        best = value
        best_line = line
if best is None:
    print("peak_rss=UNVERIFIED stats lines did not parse")
else:
    print(f"peak_stats_parsed_bytes={int(best)}")
    print(f"peak_stats_line={best_line}")
if vmrss:
    print(f"vmrss_kb_samples={len(vmrss)}")
    print(f"vmrss_kb_max={max(vmrss)}")
else:
    print("vmrss_kb_max=UNVERIFIED no VmRSS samples")
PY
else
  log "peak_rss=UNVERIFIED no docker stats samples"
fi

write_method
log "wrote ${METHOD}"
