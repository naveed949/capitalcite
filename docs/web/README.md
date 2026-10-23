# Web app screenshots

These three frames are from `ASK_MODEL=fake` on the fixture index. Next on `127.0.0.1` proxied `POST /api/ask/stream` to that run's uvicorn. The helper is `web/scripts/verify-browser.mjs`.

| File | What it shows |
|---|---|
| [answered-span.png](answered-span.png) | Retrieval question. Citation chip open. Viewer shows `doc_id`, section, quote, and offsets. |
| [agent-timeline.png](agent-timeline.png) | Agent path. Timeline has `plan`, `search_filings` call, and the result. Ticker and period are hidden. |
| [refused.png](refused.png) | Wells Fargo 2026 on the fixture index. Refusal text. No answer node. |
