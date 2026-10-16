---
name: verify-capitalcite
description: "Drive capitalcite's FastAPI HTTP API (primary) and `capitalcite` CLI (secondary) the way a user does: launch uvicorn on a free port, curl /health, run `capitalcite version`, optionally check docker compose and Qdrant. Use when proving a change works, after editing API/CLI/compose, or before merging."
---

# Verify capitalcite

This skill is for the next agent, mid-task, who has never seen the app. It drives the **real** process, not pytest's `TestClient`. Unit tests live in `tests/`; they are not proof.

**Primary surface:** HTTP API — FastAPI, `uv run uvicorn capitalcite.api:app`.
**Secondary surface:** `capitalcite` CLI (`uv run capitalcite`).
**Not a surface yet:** there is no web UI. Do not open a browser.

Two instances can run side by side: pick a free loopback port per run and a unique `artifacts/verify/<timestamp>/` directory. Do not attach to an already-running uvicorn you did not start. Do not `docker compose down` a stack you did not start.

## Feature PRs

Every feature PR adds an ADR under `docs/decisions/` and one new row in [`docs/DECISIONS.md`](../../../docs/DECISIONS.md). Land the record and the index row in the same change.

## Launch

From the repo root, with no `.env` required (the helper injects a test `SEC_USER_AGENT`):

```bash
./scripts/verify.sh
```

That is the default verification launch. It:

1. Runs `uv sync --frozen` so the editable install matches `uv.lock`.
2. Binds uvicorn to `127.0.0.1` on a free port with `SEC_USER_AGENT='capitalcite-verify you@example.com'`.
3. Waits until `GET /health` returns 200 (usable process — `/health` is process-local and does not talk to Qdrant or Langfuse).
4. Drives the mapped features below, writes evidence, then tears down **only** the uvicorn it started.

Ready means: `GET /health` JSON is `{"status":"ok","service":"capitalcite","version":"<installed>"}`. A listening socket alone is not enough — Settings validation can still crash the worker on the first request if `SEC_USER_AGENT` lacks an `@`.

Manual equivalent if you must launch without the helper (still isolate on a free port):

```bash
uv sync --frozen
SEC_USER_AGENT='capitalcite-verify you@example.com' \
  uv run uvicorn capitalcite.api:app --host 127.0.0.1 --port "$PORT"
```

Ready: a log line containing `Uvicorn running on http://127.0.0.1:` plus a 200 from `/health`.

**Teardown:** kill the PID `scripts/verify.sh` recorded (the process it started and its children). Never `pkill uvicorn` / `pkill -f capitalcite`. Proof artifacts stay on disk.

Docker Compose (Qdrant + Langfuse) is **not** required to launch the API. `/health` must pass with compose down. Start compose only when verifying the optional local-stack feature.

## Doctor

Run this first whenever anything looks off. It answers "is this instance worth driving?"

Against an instance **this run started** on `$PORT` with pid `$UVICORN_PID`:

```bash
curl -sf "http://127.0.0.1:${PORT}/health"
uv run capitalcite version
# port must belong to the pid we started, not some other process
```

Pass when:

- `/health` is HTTP 200 and JSON `status` is `ok`, `service` is `capitalcite`, and `version` is a non-empty string.
- `uv run capitalcite version` exits 0 and prints `importlib.metadata.version("capitalcite")` (today `0.1.0`; do not hardcode it).
- The listener on `$PORT` is the uvicorn this run launched.

Fail (do not drive) when: curl hangs or non-200, JSON missing those keys, version command fails, or another process owns the port. `scripts/verify.sh` already performs this doctor pass after launch; re-run the same curls if a later step behaves strangely.

`docker compose config` succeeding is **not** a doctor for the API process. A 200 from Qdrant `/healthz` is **not** a doctor for uvicorn.

## Drive

Harness: `scripts/verify.sh` (HTTP via curl, CLI via `uv run capitalcite`). Prefer the helper; the raw commands below are what it runs and what you use to extend it.

Stable handles (do not invent others):

| Surface | Handle | Proof |
|---|---|---|
| HTTP | `GET /health` | 200 + JSON body |
| CLI | `capitalcite version` | exit 0 + version string |
| CLI | `capitalcite --help` | exit 0; lists `version` and `ingest` |
| CLI | `capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K --years 2023-2025` | exit 0 + stored metadata.json files (live EDGAR; isolate `--output-dir`) |
| CLI | `capitalcite chunk --input-dir tests/fixtures/chunking/corpus --chunker section` | exit 0 + `chunks.jsonl` with citation fields (isolate `--output-dir`) |
| CLI | `capitalcite golden validate` | exit 0 + mix counts; no network |
| CLI | `capitalcite golden validate --against-raw <data_dir>` | exit 0 + `aligned=` / `recall=handwritten-only` after ingest+chunk |
| CLI | `capitalcite index --help` | exit 0; lists `--embedder` |
| HTTP | `POST /search` | 200 + citation fields + gold span overlap on `cc-h-013` / `cc-h-063`; UNVERIFIED only when ingest could not run |
| HTTP | `POST /ask` | 200 + grounded citations on `cc-h-013` with `model` `fake`; `cc-u-001` refused; live `model` `openai` is UNVERIFIED without `OPENAI_API_KEY` |
| HTTP | `POST /ask` `path=agent` | 200 + plan/traces on `cc-h-055` (comparison) and `cc-h-061` (multi-hop) with `model` `fake`; live `model` `openai` is UNVERIFIED without `OPENAI_API_KEY` |
| CLI | `capitalcite gate decide --tool shell --args '{"cmd":"ls"}'` | exit 0 + JSON `kind` `deny`; unknown tool, invalid args, and a broken policy file each deny |
| CLI | `capitalcite gate redteam` | exit 0 + `failed` 0; injection text cannot run a denied tool |
| CLI | `capitalcite gate bench` | exit 0 + `p95_ms`; no network |
| File | `CAPITALCITE_TRACE_FILE` JSONL | after `capitalcite gate decide`, JSONL has deny spans with tool and reason; after fake `POST /ask`, JSONL has `ask` + `retrieve`; after `eval --small`, an `eval` span has `git_sha` and `config_hash`. Live Langfuse `:3000` is UNVERIFIED when down |
| CLI | `capitalcite audit verify` | exit 0 on an intact chain; exit 1 after a mutated field or a removed last record; no network |
| CLI | `capitalcite check` | exit 0 + `ok=true`; quote-index sanity and fixture recall vs `evals/baselines/ci.json`; `cc-ci-002` answered; `cc-u-001` refused; red-team `failed` 0. `--live` without `OPENAI_API_KEY` is UNVERIFIED |
| CLI | `capitalcite eval --suite golden --small` | exit 0 + Recall@5 table; FakeEmbedder, no download |
| CLI | `capitalcite eval --suite golden` | exit 0 + six-row dense/bm25/hybrid × section/fixed table on the twelve 10-Ks plus an Agent vs RAG recall@k line on comparison/multi-hop items (`fake/heuristic planner`); UNVERIFIED only when ingest could not run |
| Compose (optional) | `docker compose config` | exit 0 |
| Qdrant (optional) | `GET http://127.0.0.1:6333/healthz` | 200, only if something is listening |

Read the feature map before driving: [features/README.md](features/README.md). Drive from those recipes. A proof that only hits `/health` is incomplete if you claimed to verify the CLI.

Do **not** treat `TestClient(app).get("/health")` or `CliRunner().invoke(...)` as a drive. Those are in-process tests.

## Status

Report each driven feature as `passed`, `failed`, or `UNVERIFIED`.

- `passed` only when this run executed the user path and the proof files match the expected result.
- `failed` when the path ran and the result is wrong.
- `UNVERIFIED` when the path cannot run here. Azure deploy with no credentials, live EDGAR 403, and Docker missing for the optional compose stack are `UNVERIFIED`. Embedded `POST /search` is UNVERIFIED only when ingest could not store JPM FY24 / WFC FY25. Record the attempted command and the missing precondition.

Never report `UNVERIFIED` as `passed`. `scripts/verify.sh` may exit 0 when only optional steps were skipped. Copy those skips into the report as `UNVERIFIED`. Files named `*.skipped` or `verified-unreachable` mean `UNVERIFIED`.

## Evidence

Location: `artifacts/verify/<UTC-timestamp>/` at the repo root (gitignored). Cleanup must not delete this directory.

Each run of `scripts/verify.sh` writes at least:

| File | What it proves |
|---|---|
| `summary.txt` | Step list, ports, pids, skip reasons |
| `health.body.json` | Raw `/health` body |
| `health.exit` | curl exit code |
| `health.headers` | HTTP status line |
| `version.out` / `version.exit` | CLI stdout and exit code |
| `uvicorn.log` | Server stdout/stderr |
| `uvicorn.pid` | PID that was started (and killed) |
| `port` | Loopback port used |
| `golden.help.out` / `golden.help.exit` | `capitalcite golden validate --help` (must list `--against-raw`) |
| `golden.out` / `golden.exit` | Offline golden-set validation |
| `golden.against-raw.out` / `golden.against-raw.exit` **or** `golden.against-raw.skipped` | Quotes aligned to parsed `document.md`, or UNVERIFIED if ingest was unreachable |
| `ingest.help.out` / `ingest.help.exit` | `capitalcite ingest --help` |
| `ingest.out` / `ingest.exit` / `ingest.files.txt` **or** `ingest.unreachable` | Live EDGAR ingest proof |
| `chunk.help.out` / `chunk.help.exit` | `capitalcite chunk --help` |
| `chunk.out` / `chunk.exit` / `chunk.files.txt` | Fixture chunk proof |
| `index.help.out` / `index.help.exit` | `capitalcite index --help` |
| `index.1.out` / `index.2.out` **or** `search.unreachable` | Two `capitalcite index` runs on the ingested corpus, or UNVERIFIED when ingest could not run |
| `search.top5.txt` / `search.*.body.json` / `search.http` **or** `search.unreachable` | Live `POST /search` on embedded Qdrant, or UNVERIFIED when ingest could not run |
| `ask.body.json` / `ask.http` / `ask.cc-u-001.body.json` **or** `ask.unreachable` | Live `POST /ask` with `ASK_MODEL=fake` on that same index. `ask.openai.unverified` when live OpenAI was not run |
| `gate.help.out` / `gate.validate.out` / `gate.decide.*.body.json` / `gate.redteam.body.json` / `gate.bench.body.json` | Offline fail-closed gate: shipped policy loads, unknown/invalid/broken deny, red-team `failed` is 0, `p95_ms` is recorded |
| `agent.cc-h-055.body.json` / `agent.cc-h-061.body.json` **or** `agent.unreachable` | Live `POST /ask` `path=agent` with the fake model on comparison and multi-hop items. `agent.openai.unverified` when live OpenAI was not run |
| `spans.jsonl` **or** `spans.skipped` | File-exporter JSONL of ask/retrieve (and eval) spans. Proof of the tree without Langfuse |
| `langfuse.skipped` | Live Langfuse UI on `:3000` was not reachable. UNVERIFIED, not a pass |
| `audit.help.out` / `audit.ok.out` / `audit.tamper.out` / `audit.removed.out` | Offline `capitalcite audit verify` on an intact chain, after a mutated field, and after deleting the last line |
| `audit.agent.out` **or** `audit.agent.skipped` | Live one-record-per-trace check after `path=agent`, or UNVERIFIED when the agent path could not run |
| `quote-sanity.out` / `quote-sanity.exit` **or** `quote-sanity.skipped` | Quote-index recall sanity, not the 12-filing corpus eval |
| `check.help.out` / `check.help.exit` | `capitalcite check --help` (must list `--baseline` and `--live`) |
| `check.out` / `check.exit` / `check/check.json` | Deterministic regression pack vs the committed baseline |
| `check.live.unverified` **or** `check.live.out` | `--live` without a key, or a grounded live run |
| `eval.help.out` / `eval.help.exit` | `capitalcite eval --help` (must list `--suite` and `--small`) |
| `eval-small.out` / `eval-small.exit` | Fixture-corpus `--small` matrix |
| `eval.out` / `eval.exit` / `eval.table.md` **or** `eval.skipped` | Real-corpus eval with embedded Qdrant + bge-small, or UNVERIFIED if ingest was unreachable |
| `compose.config.out` + `compose.config.exit` **or** `compose.skipped` | Optional compose proof |
| `qdrant.health.out` + `qdrant.health.exit` **or** `qdrant.skipped` | Optional Qdrant proof |

Proof standards:

- Exercise the real user path: a live uvicorn and the installed `capitalcite` console script.
- Capture the action and the resulting state: the request (curl) **and** the body, the CLI command **and** stdout/exit.
- `/health` does not write files or rows. Ingest proof is the stored `metadata.json` plus primary document under `--output-dir`, not only CLI stdout.
- No mocks. Do not stub Settings, Qdrant, or EDGAR for a verification run. Optional Qdrant checks talk to a real Qdrant if one is already up; they must not start or mock one.
- Version proof compares CLI output to `importlib.metadata.version("capitalcite")`, not a hardcoded `0.1.0`.

After cleanup, confirm the timestamped directory still exists and still contains `health.body.json` and `version.out`.

## Cleanup

`scripts/verify.sh` registers an `EXIT` trap that:

- Sends TERM (then KILL if needed) to the uvicorn pid **it started** and that pid's children.
- Does not kill by process name.
- Does not run `docker compose down`.
- Does not delete `artifacts/verify/`.

If a run fails mid-way, run cleanup anyway (the trap should fire). If you launched uvicorn by hand, kill that pid the same way. Stray listeners on ephemeral ports waste the next agent's time.

## Helpers

`scripts/verify.sh` is executable. From repo root:

```bash
./scripts/verify.sh
```

No arguments. It prints each step to stdout (paste that into a PR) and mirrors the same facts into `artifacts/verify/<timestamp>/`. Exit 0 means required API and CLI checks passed. Optional Docker or Qdrant steps that did not run are `UNVERIFIED`, not passed. The script still exits 0 in that case.

Do not reverse-engineer the script: the Drive table and the feature files list the exact commands it wraps.

## Extending this skill (later issues)

When a later issue adds a user-facing endpoint or command, extend verification in the same PR that adds the surface — do not wait for a dedicated "update the skill" issue.

1. Add a feature file under [features/](features/) with the four H2s (`Sub-features`, `How to get to it (user POV)`, `Driving it with curl` or `Driving it with the capitalcite CLI`, `Gotchas`).
2. Link it from [features/README.md](features/README.md).
3. Add a step to `scripts/verify.sh` that invokes the new handle, writes `<name>.body` / `<name>.out` and `<name>.exit` into the same timestamped artifacts dir, and fails the script on a bad status/body.
4. If the new path needs Qdrant, an index, or a golden file, say so as a **precondition** in that feature's Gotchas. Do not weaken the journey so it passes on an empty store. Report `UNVERIFIED` and write a `verified-unreachable` artifact with the missing precondition.
5. Run `./scripts/verify.sh` and keep the new artifact files.
6. Periodic honesty pass: `/maintain-verification-skill`.

Expected later handles (do not add empty stubs now):

| Issue | Handle to add |
|---|---|
| #3 chunking | `capitalcite chunk --chunker section` (landed) |
| #4 retrieval | `POST /search` (landed) |
| #5 golden set | `capitalcite golden validate` (landed) |
| #6 evals | `capitalcite eval --suite golden` (landed) |
| #7 answers | `POST /ask` (landed) |
| #8 agent | `POST /ask` `path=agent` (landed) |
| #9 gate | `capitalcite gate decide` (landed) |
| #10 audit | `capitalcite audit verify` (landed) |
| #11 tracing | `CAPITALCITE_TRACE_FILE` JSONL after `/ask` (landed). Live Langfuse UI is UNVERIFIED without `:3000` |
| #12 eval CI | `capitalcite check` (landed) |

`./scripts/verify.sh` runs `capitalcite check` and `capitalcite eval --suite golden --small` always, and the twelve-filing bge-small matrix when ingest succeeded. A missing corpus is `UNVERIFIED`, not a pass. Live `--live` without `OPENAI_API_KEY` is UNVERIFIED.

## Offer the maintenance loop

Use `/maintain-verification-skill` when routes, CLI commands, or compose ports change, or after a few issues land. That pass re-reads the map from source and re-drives every feature.
