# 0020. Web UI for cited answers

Date: 2026-10-23

Status: Accepted

`POST /ask/stream` already sends a plan, tool trace, citations, and
one terminal answer. A page that shows that stream has to keep each
sentence attached to a filing span, and it has to hide the API host
from the browser. This record is why that page is a Next.js App
Router app in `web/`, why the browser uses `fetch`, and why session
state stores a `CitedClaim` instead of a string.

## App Router, not a separate SPA

The page is one screen. The piece that must stay off the client is
`CAPITALCITE_API_ORIGIN`. A Vite SPA would either bake that host into
the browser bundle or need a second proxy process beside the dev
server. The App Router puts a route handler next to the page. The
handler reads the origin on the server, default
`http://127.0.0.1:8000`. The browser posts to `/api/ask/stream` on
the same origin. There is no `NEXT_PUBLIC_` copy of the origin, and
FastAPI does not grow a CORS middleware.

Port 3001 leaves port 3000 for the local Langfuse compose stack.
`next.config.ts` sets `compress` to false so the dev server does not
gzip `text/event-stream` and hold the body until the run ends.

A hand-written client in another framework could call the same
handler. The App Router wins because the proxy and the page ship as
one Node app, with the same `pnpm --dir web` scripts the rest of the
repo can typecheck.

## fetch, not EventSource

`EventSource` only sends GET. ADR 0021 did not add a GET twin,
because the body is `AskRequest`. The client is `fetch` plus
`ReadableStream`. The route handler forwards `request.body` unread
and forwards `request.signal`, so a browser abort closes the uvicorn
connection. It copies the upstream status and `content-type`. It
does not parse SSE and it does not buffer the body into a string.
`GET /api/ask/stream` returns 405, the same as the API.

A WebSocket would add a handshake for a one-way stream. Long-poll
would hide each frame behind another request. Neither matches the
SSE the API already writes.

## A reducer, not fields that drift

`Session` is a discriminated union. `idle` has nothing to paint.
`asking` with phase `citing` has spans and no claim, so chips can
render and the paragraph cannot. `asking` with phase `writing`, and
`answered`, hold a `CitedClaim`. `refused` holds a reason and has no
claim. `failed` and `cancelled` keep a claim only when one already
existed. No variant has an `answer` string.

`citedClaim` is the only constructor. It returns undefined when the
trimmed text is empty or the span list is empty. The reducer never
stores the in-flight buffer. `askStream` keeps that buffer in a local
named `pendingText`. It emits `span`, then `claim`, only once both
the buffer and the span list are non-empty. On the wire `answered`
frame it discards the buffer and rebuilds the claim from
`response.answer` and `response.citations`. On `refused` it drops the
buffer, so a refusal cannot keep a preview. `AnswerBody` mounts only
when a `CitedClaim` exists, and it renders `claim.text` and
`claim.spans` together.

The hook gives each submit its own `AbortController` and run id.
`publish` writes React state only while that id is still current, so
a slow fold from the previous submit does not land on the next one.
Cancel only aborts. The generator yields `cancelled`.

An ad hoc set of `useState` booleans would let a paragraph render
before any span arrived. The union makes that state unrepresentable.
Tests fold `reduceSession` without a browser.

## Generated types, not a second wire model

`scripts/dump_openapi.py` prints `create_app().openapi()`.
`web/scripts/generate-api.mjs` runs that dump through
`openapi-typescript` and writes `web/src/api/schema.ts`. `check:api`
writes a temp file and diffs the committed schema. CI runs the same
check. The FastAPI app is the source. The committed file is the
derived copy. Generation does not open a port.

`askStream.ts` is the only module allowed to import `schema.ts`.
ESLint `no-restricted-imports` fails the build on any other import.
`CITATION_KEYS` has to be the same set as the generated `Citation`
fields, so a renamed citation field fails `tsc`. The frame switch is
exhaustive on the generated event names. A new event fails the build
until that switch handles it. Hand-written interfaces would drift the
first time the Pydantic model gained a field.

`tool_denied` reasons on the timeline are the five audited values.
`gate_error` is not one of them. A frame that carries it becomes a
transport fault. The timeline type cannot store it.

## What the page shows

The heading is "Ask a filing question". When the `model` this page
sends is `fake`, the page says `deterministic demo model` before the
question is submitted. `ASK_MODEL=openai` renders no label. `GET
/health` stays `status`, `service`, and `version`. The page does not
read `/health` to discover the model, because that route does not
name one.

The path toggle is labeled Retrieval and Agent. The values sent are
`rag` and `agent`. Ticker and period inputs are hidden on the agent
path, because that path ignores them. The span viewer shows `docId`,
section, and quote, and it highlights `quoteStart` and `quoteEnd`.
Those offsets are the quote in the document. `start` and `end` are
the chunk.

## What this leaves out

A metric chart, a model picker, the `form` field, the `k` field, a
GET stream, and a FastAPI CORS change stay out. `localhost` and
`127.0.0.1` are different browser origins. The dev server and the
verify helper bind `127.0.0.1`, which is the host uvicorn uses.

The verify handle `web ask` starts Next against the uvicorn that same
run already launched, curls the page, and drives the browser when
Chromium is available. A missing Node toolchain writes
`web.unreachable`. A failed Chromium launch writes
`web.browser.unverified`. Neither fails the Python checks.
