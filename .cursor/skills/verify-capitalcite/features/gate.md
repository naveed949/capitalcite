# Fail-closed tool gate

`capitalcite gate` loads the JSON allow-list and decides whether a planned
tool call may run. Default is deny. A missing or invalid policy file denies
every name. The command does not call EDGAR, Qdrant, or an LLM.

## Sub-features

- `gate-help` lists `validate`, `decide`, `redteam`, and `bench`.
- `gate-validate` exits 0 on the shipped policy and prints `ok tools=`.
- `gate-decide-unknown` denies `shell` with `reason` `unknown_tool`.
- `gate-decide-invalid` denies extra keys on `calculator` with `invalid_args`.
- `gate-decide-broken` denies a known tool when `--policy` points at broken JSON. Reason is `policy_load_failed`.
- `gate-redteam` exits 0 and prints `failed=0`. Injected `CALL_TOOL` text cannot run a denied tool.
- `gate-bench` prints `p95_ms` from `decide()` samples. No network.

## How to get to it (user POV)

- Run `uv run capitalcite gate validate`.
- Run `uv run capitalcite gate decide --tool <name> --args '<json>'`.
- Override the file with `--policy`. Empty `GATE_POLICY` uses the shipped allow-list.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- This path does not need uvicorn, Docker, live EDGAR, or `OPENAI_API_KEY`.
- Doctor: `uv run capitalcite gate --help` exits 0.

- **Show help.** Run `uv run capitalcite gate --help`. Exit code is `0`. Stdout lists `validate`, `decide`, `redteam`, and `bench`.
- **Validate the shipped policy.** Run `uv run capitalcite gate validate`. Exit code is `0`. Stdout includes `ok tools=` and `calculator`.
- **Deny an unknown tool.** Run `uv run capitalcite gate decide --tool shell --args '{"cmd":"ls"}'`. Exit code is `0`. JSON `kind` is `deny` and `reason` is `unknown_tool`.
- **Deny invalid args.** Run `uv run capitalcite gate decide --tool calculator --args '{"expression":"1+1","cmd":"id"}'`. Exit code is `0`. JSON `reason` is `invalid_args`.
- **Deny on a broken policy.** Write `{` to a temp file. Run `uv run capitalcite gate decide --policy <that file> --tool calculator --args '{"expression":"1+1"}'`. Exit code is `0`. JSON `reason` is `policy_load_failed`.
- **Run the red-team suite.** Run `uv run capitalcite gate redteam`. Exit code is `0`. JSON `failed` is `0`.
- **Measure decide() overhead.** Run `uv run capitalcite gate bench --n 2000`. Exit code is `0`. JSON includes `p50_ms` and `p95_ms`.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `gate.help.out`, `gate.validate.out`, `gate.decide.unknown.body.json`, `gate.decide.invalid.body.json`, `gate.decide.broken.body.json`, `gate.redteam.body.json`, and `gate.bench.body.json`.
- **Proof.** Keep the CLI stdout and exit code. Pytest is not this feature.

## Gotchas

- `decide` does not run the tool. An allow still means only "the policy permitted this call."
- `capitalcite audit verify` is a different feature (#10). Do not invent it here.
- `CliRunner().invoke(...)` in pytest is not this feature.
- A default-allow policy file is a load failure, not an open gate.
