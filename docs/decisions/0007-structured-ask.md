# 0007. Structured answers

Date: 2026-10-16

Status: Accepted

Issue #7 asks `POST /ask` for an answer a filing can support, with
citations that resolve to stored chunks. A quote the model invented has
to become a refusal. This record is why the model names a hit by index,
why a bad quote refuses without a second generation, and why the HTTP
call is httpx instead of an agent framework.

## The model cites an index and a quote

The model does not choose `doc_id`, `start`, or `end`. Those fields
already live on the retrieved `Chunk`. A draft citation is a 1-based
passage number and a quote string. The prompt wraps each hit in
`<passage n="…">` plus section, ticker, form, period, and `Chunk.text`.
It does not show `doc_id`, `start`, `end`, or `source_url`. The system
prompt says the passages are untrusted data and to ignore instructions
inside them.

`ground()` copies chunk identity from `hits[index - 1]` when `quote` is
a verbatim substring of that chunk's text. `start` and `end` stay the
chunk span. `quote_start` and `quote_end` are the quote's offsets in the
document, after skipping any `header_prefix`. `retrieval_score` is the
minimum retrieval score of the cited hits. It is not a model self-score
and it is not rescaled. Hybrid RRF scores are typically near 0.03. A
refusal has `retrieval_score` 0.

A blank quote is refused. In Python `"" in text` is true, so a membership
test alone would accept it. After strip, a quote that is shorter than 20
characters and is not at least three words with one non-stopword is
trivial and refused. That blocks a space quote used as a fake citation
for `JPM CET1 was 19.2%`.

Every figure in the answer must appear in at least one cited quote.
Figures are percentages, `$` amounts, decimals, and comma-grouped
numbers, normalized by dropping `$`, `%`, and commas (`15.7%` and
`15.7` are the same). Years (1900–2099) and figures already in the
question are exempt. An answer `JPM's CET1 was 12.3%` with quote
`CET1 ratios were 15.7%` is refused: `answer contains a figure not
present in the cited quotes`.

An index outside the list is refused. If any citation in the draft
fails, the whole answer is refused. The server does not drop the bad
citation and return the rest, and it does not call the model again. A
grounding failure is a refusal, not a retry.

`Answered` and `Refused` are different types. An answered value has a
non-empty answer and at least one citation. A refused value has a reason
and no citation list. HTTP flattens that union into one object.
`answer` and `refusal_reason` are null on the side that does not apply.
Both outcomes are HTTP 200. An empty question or a bad period is 422.
A down index is 503.

## Refuse before the model

Empty retrieval refuses with no model call. A missing API key is checked
only when a call would run, so "no passages" stays a refusal.

`ask()` also refuses before the model when the top hit score is below
`ASK_MIN_HIT_SCORE` (default 0, which disables the floor), or when a
named entity in the question is missing from the hits. Named entities
are the four corpus tickers and their bank names, plus four-digit years.
Every named ticker must appear on a hit. Every named year must equal a
hit's period year. `cc-u-001` (Wells Fargo, year-end 2026) against a
WFC FY2025 index refuses on the year. `cc-h-013` (JPMorgan, year-end
2024) against JPM FY2024 does not.

## json_object, then one validation retry

The chat call is `POST https://api.openai.com/v1/chat/completions` with
`response_format` `json_object`, through httpx. Embeddings already use
httpx. PydanticAI stays out of this issue. Issue #8 owns the agent
stack, and a second SDK for one completion is a dependency this path
does not need.

`json_object` does not enforce the field rules. The adapter parses the
message with `ModelDraft`. An answered draft needs a non-empty answer
and at least one citation. A refused draft needs a reason and no
citations. The first failure is sent back once, with the validator error
in the follow-up message. A second failure becomes a refused draft. A
non-200 response raises `ModelUnavailable` and the route returns 503.
That path does not retry.

The system prompt numbers the passages from 1, tells the model to quote
a verbatim substring, and includes the `ModelDraft` JSON schema. It
tells the model to refuse when the passages are not enough. `/ask`
always retrieves hybrid. There is no `mode` on the request.

`ASK_MODEL` is `openai` or `fake`. The default is `openai`, so a server
without `OPENAI_API_KEY` fails the call with 503 instead of quietly
quoting chunks. `fake` quotes `hits[0].chunk.text` and does not read the
key. `FakeStructuredModel` is importable in production. `verify.sh`
starts uvicorn with `ASK_MODEL=fake`.

The request body may set `model` to `openai` or `fake`. A missing value
uses `ASK_MODEL`. That override exists so verify can keep one uvicorn on
`QDRANT_PATH` with `ASK_MODEL=fake` and still probe OpenAI when a key is
present. No key and `model=openai` is HTTP 503, recorded as UNVERIFIED
live generation. The verify process does not treat that 503 as a product
failure.

## What this leaves out

The agent, tools, the fail-closed gate, the audit log, and Langfuse
traces are later issues. RAGAS and the answer-quality table stay
unmeasured. This change does not fill those cells.

`fake` is extractive. On `cc-u-001` the year guard refuses before the
fake model can quote a WFC FY2025 chunk. The refusal contract is also
the empty-hit path, the score floor, a ticker mismatch, and a scripted
refusal in pytest.

`OPENAI_API_KEY` is the same secret the paid embedder uses. The repo
does not contain a key. `ASK_MODEL=fake` never reads it.
