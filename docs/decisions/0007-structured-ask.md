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
already live on the retrieved `Chunk`. A draft citation is an index into
the hit list and a quote string. `ground()` copies `doc_id`, `start`,
`end`, `section`, and `source_url` from `hits[index]` when `quote` is a
verbatim substring of `hits[index].chunk.text`. `start` and `end` stay
the chunk span. They are not recomputed from the quote.

A blank quote is refused. In Python `"" in text` is true, so a membership
test alone would accept it. An index outside the list is refused. If any
citation in the draft fails, the whole answer is refused. The server
does not drop the bad citation and return the rest, and it does not call
the model again. A grounding failure is a refusal, not a retry.

`Answered` and `Refused` are different types. An answered value has a
non-empty answer and at least one citation. A refused value has a reason
and no citation list. HTTP flattens that union into one object.
`answer` and `refusal_reason` are null on the side that does not apply.
Both outcomes are HTTP 200. An empty question or a bad period is 422.
A down index is 503.

Empty retrieval refuses with no model call. A missing API key is checked
only when a call would run, so "no passages" stays a refusal.

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

The system prompt numbers the passages, tells the model to quote a
verbatim substring, and includes the `ModelDraft` JSON schema. It tells
the model to refuse when the passages are not enough.

`ASK_MODEL` is `openai` or `fake`. The default is `openai`, so a server
without `OPENAI_API_KEY` fails the call with 503 instead of quietly
quoting chunks. `fake` quotes `hits[0].chunk.text` and does not read the
key. `FakeStructuredModel` is importable in production. `verify.sh`
starts uvicorn with `ASK_MODEL=fake`.

The request body may set `model` to `openai` or `fake`. A missing value
uses `ASK_MODEL`. Verification always sends `fake` for the grounded
check. A second request sends `openai` when a key is visible to
`Settings`. No key and `model=openai` is HTTP 503, recorded as
UNVERIFIED live generation. The verify process does not treat that 503
as a product failure.

## What this leaves out

The agent, tools, the fail-closed gate, the audit log, and Langfuse
traces are later issues. RAGAS and the answer-quality table stay
unmeasured. This change does not fill those cells.

`fake` is extractive. On `cc-u-001`, retrieval can still return chunks,
and the fake model will quote the first one. That is not a judgment that
the question was answerable. The refusal contract is the empty-hit path
and a scripted refusal in pytest.

`OPENAI_API_KEY` is the same secret the paid embedder uses. The repo
does not contain a key. `ASK_MODEL=fake` never reads it.
