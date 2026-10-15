# Parse and chunk

`capitalcite chunk` reads a stored ingest tree, parses HTML or PDF, and writes
chunks with citation metadata. The chunker is selected by `--chunker` or
`CHUNKER` (`fixed`, `recursive`, or `section`).

## Sub-features

- `chunk-help` lists `--input-dir`, `--output-dir`, `--chunker`, `--size`, and `--overlap`.
- `chunk-section` writes `chunks.jsonl` whose rows include `doc_id`, `section`, `offset`, and `source_url`.
- `chunk-table` keeps the CET1 HTML table as markdown inside the Item 7 chunk.
- `chunk-items` detects Item 7 and Item 8 and does not treat the table of contents as those sections.
- `chunk-stats` writes `stats.json` with `count` and `mean_tokens` for the selected strategy.

## How to get to it (user POV)

- Run `uv run capitalcite chunk --input-dir tests/fixtures/chunking/corpus --output-dir artifacts/verify/$TS/chunk --chunker section`.
- Override strategy with `--chunker fixed` or `--chunker recursive`.
- Default roots are `DATA_DIR` (usually `data/`). Layout: `parsed/edgar/{ticker}/{form}/{period}/{accession}/`.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `SEC_USER_AGENT` contains an `@` (the helper uses `capitalcite-verify you@example.com`).
- Doctor: `uv run capitalcite chunk --help` exits 0.
- Use the checked-in fixture tree at `tests/fixtures/chunking/corpus`. Do not require live EDGAR. Isolate writes with `--output-dir artifacts/verify/$TS/chunk`.

- **Show help.** Run `uv run capitalcite chunk --help`. Exit code is `0`. Stdout mentions `--chunker`, `--size`, and `--overlap`.
- **Chunk the fixture.** Run `uv run capitalcite chunk --input-dir tests/fixtures/chunking/corpus --output-dir artifacts/verify/$TS/chunk --chunker section`. Exit code is `0`. Stdout includes `strategy=section` and `files=1`. Under that output dir, `parsed/edgar/JPM/10-K/2024-12-31/0000019617-25-000270/chunks.jsonl` exists. Each line is JSON with `doc_id`, `section`, `offset`, and `source_url`. One row has `section`=`Item 7` and markdown containing `CET1 ratio` and `15.1%`. One row has `section`=`Item 8`. Sibling `stats.json` has `chunker`=`section`, `count` > 0, and `mean_tokens` > 0.
- **Bundled helper.** Run `./scripts/verify.sh`. `chunk.help.out`, `chunk.out` / `chunk.exit`, and `chunk.files.txt` are written under `artifacts/verify/<timestamp>/`.
- **Proof.** Keep the CLI stdout/stderr/exit, `chunks.jsonl`, and `stats.json`. A 200 from `/health` is not chunk proof. Pytest is not this feature.

## Gotchas

- The fixture is an excerpt, not a live 10-K. Live ingest HTML is tens of megabytes and is not required to prove table preservation or Item detection.
- `CHUNKER` in the environment can override a forgotten `--chunker`. The helper passes `--chunker section` explicitly.
- `CliRunner().invoke(...)` in pytest is not this feature.
- Index and `/ask` are still later issues.
