# CLI version

The `capitalcite` console script is the terminal entry point. `version` prints the installed package version. `ingest` downloads EDGAR filings (see [ingest](./ingest.md)). `--help` lists both; index and eval land later.

## Sub-features

- `cli-version` prints `importlib.metadata.version("capitalcite")` and exits 0.
- `cli-help` lists the `version` and `ingest` commands and exits 0.
- `cli-no-args` with no arguments shows help (Typer `no_args_is_help`) and is not an error for the user.

## How to get to it (user POV)

- Run `uv run capitalcite version`.
- Run `uv run capitalcite --help` or `uv run capitalcite` with no arguments.
- After install, `capitalcite` on `PATH` is the same entry point (`capitalcite.cli:app`).

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `uv run capitalcite version` is executable (does not require uvicorn).

- **Print version.** Run `uv run capitalcite version`. Exit code is `0`. Stdout, stripped, equals the output of `uv run python -c 'from importlib.metadata import version; print(version("capitalcite"))'`.
- **Show help.** Run `uv run capitalcite --help`. Exit code is `0`. Stdout mentions `version` and `ingest`.
- **Bundled helper.** Run `./scripts/verify.sh`. `version.out` and `version.exit` are written under `artifacts/verify/<timestamp>/`. `version.exit` is `0`.
- **Proof.** Keep `version.out` and `version.exit`. Do not treat a hardcoded `0.1.0` in a test or note as proof — compare to `importlib.metadata`.

## Gotchas

- Invoke via `uv run capitalcite` so the editable install from `uv.lock` is used. A globally installed older `capitalcite` is the wrong binary.
- `CliRunner().invoke(app, ["version"])` in pytest is not this feature.
- Index, eval, and audit commands are not shipped yet. A missing `index` subcommand is expected. Drive ingest from [ingest](./ingest.md), not from this file.
- Typer puts a trailing newline on `version` output. Compare stripped strings.
