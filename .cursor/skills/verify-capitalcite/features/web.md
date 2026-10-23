# Web app

The page at `http://127.0.0.1:3001` asks a filing question and shows a cited answer, a tool timeline, or a refusal. The browser posts to the Next server on that same origin. Next forwards `POST /api/ask/stream` to the uvicorn this run started. `ASK_MODEL=fake` shows the text `deterministic demo model` before you submit.

## Sub-features

- `web-label` shows `deterministic demo model` on the page before submit when the Next process was started with `ASK_MODEL=fake`.
- `web-answered` asks `What CET1 ratio did JPMorgan disclose?` on Retrieval with ticker `JPM`. The answer text is non-empty, a citation chip opens, and the viewer shows a non-empty doc id, section, and quote.
- `web-agent` asks `What CET1 ratio did JPMorgan disclose for 2024?` on Agent. The timeline includes `search_filings`.
- `web-refused` asks `What CET1 ratio did Wells Fargo disclose for year-end 2026?` with ticker `WFC`. The refusal region has text. The answer node is absent.
- `web-cancel` holds `/api/ask/stream`, clicks Cancel, and shows the cancelled state with no answer node.

## How to get to it (user POV)

- Build the fixture index (or the ingested index) and start uvicorn on `127.0.0.1` with `ASK_MODEL=fake`, the same way `POST /ask/stream` is verified.
- From the repo root, run `pnpm --dir web install` and `pnpm --dir web build`, then `CAPITALCITE_API_ORIGIN=http://127.0.0.1:${PORT} ASK_MODEL=fake pnpm --dir web start`. Local editing can still use `pnpm --dir web dev`.
- Open `http://127.0.0.1:3001`. Choose Retrieval or Agent, type the question, and press Ask. Press Cancel while a request is in flight.

## Driving it with the browser

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200.
- The `POST /ask/stream` step for this run has already used the shared `QDRANT_PATH`. If that step wrote `stream.unreachable`, this feature is UNVERIFIED.
- `pnpm` and `node` are on `PATH`. If either is missing, this feature is UNVERIFIED.
- A browser driver that can launch Chromium. If that launch fails, this feature is UNVERIFIED.

- **Open the page.** `curl -sf http://127.0.0.1:${WEB_PORT}/` returns HTML that contains `Ask a filing question` and `deterministic demo model`.
- **Read the demo label.** Before submit, the page shows the exact text `deterministic demo model`.
- **Ask a retrieval question.** Choose Retrieval. Question `What CET1 ratio did JPMorgan disclose?`. Ticker `JPM`. Press Ask. The answer node is non-empty. At least one chip renders. The chip opens a viewer with a non-empty doc id, section, and quote.
- **Ask on the agent path.** Choose Agent. Question `What CET1 ratio did JPMorgan disclose for 2024?`. Ticker and period inputs are not on the page. Press Ask. The timeline shows `search_filings`.
- **Ask a refusal.** Choose Retrieval. Question `What CET1 ratio did Wells Fargo disclose for year-end 2026?`. Ticker `WFC`. Press Ask. The refusal region has text. The answer node is absent.
- **Cancel.** Hold `POST /api/ask/stream`, press Ask, press Cancel, and abort the held request. The page shows the cancelled state. The answer node is absent. The Playwright `e2e` job also asks the slow-retrieve question, clicks Cancel, and checks `CAPITALCITE_CANCEL_MARKER` for a cancel write that does not depend on `LOG_LEVEL`.
- **Bundled helper.** `./scripts/verify.sh` or `./scripts/verify.sh --small` runs this after the stream step, on the same index and the same uvicorn. It writes `web.exit`, `web.label.txt`, `web.answered.txt`, `web.agent.txt`, `web.refused.txt`, `web.cancel.txt`, `web.answered.png`, `web.agent.png`, and `web.refused.png`, or `web.unreachable` / `web.browser.unverified`.
- **Proof.** Keep the page HTML and the browser text files. A 200 from `POST /ask/stream` is not this feature. Pytest is not this feature.

## Gotchas

- The page listens on port 3001 in normal dev. The helper picks another free loopback port and does not use port 3000. Do not attach to a Next process this run did not start.
- `localhost` and `127.0.0.1` are different origins. The helper binds and curls `127.0.0.1`. `CAPITALCITE_API_ORIGIN` must use that same host.
- Missing `pnpm` or missing `node` writes `web.unreachable`. A failed Chromium launch writes `web.browser.unverified`. The helper still exits 0. An assertion failure fails the helper.
- Do not add CORS to FastAPI for this check. The browser talks to Next.
- `--small` refusal of the WFC 2026 question is empty hits (`no passages were retrieved`). It is not the year guard.
- `TestClient` and a unit test of the reducer are not this feature.
