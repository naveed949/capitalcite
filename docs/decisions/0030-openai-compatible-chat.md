# 0030. OpenAI-compatible local chat

Date: 2026-11-03

Status: Accepted

Someone analyzing bank filings should be able to point `POST /ask` and
`eval --suite bench` at a Chat Completions server on their own machine,
and at hosted OpenAI, without a second client. A local run has to stay
labelled local, and it has to cost nothing, so a later reader cannot
treat it as a hosted `gpt-4o-mini` number.

## One HTTP seam

Ollama, and other local servers, already speak the Chat Completions JSON
that this repo posts with `httpx`. A native Ollama client would add a
second request shape, a second error path, and a third value beside
`fake` and `openai`. `ASK_MODEL=openai` stays the wire protocol. Billing
is a separate fact. The model id is whatever the server was asked to run.

`ASK_OPENAI_BASE_URL` is the prefix. Empty means
`https://api.openai.com/v1`, which still requires `OPENAI_API_KEY` and
`--paid` on the bench. Any other host is provider `local` and unbilled.
Loopback (`localhost`, `127.0.0.1`, `::1`) is recorded as the token
`localhost`. Anything else is `other`. The process never sends
`Authorization` to those hosts, even when `OPENAI_API_KEY` is set, so a
hosted secret does not leave the machine. Embeddings ignore the chat
prefix and still use `https://api.openai.com/v1/embeddings` when
`EMBEDDER=openai-small`.

`check --live` passes no base URL, so the call is hosted `gpt-4o-mini`.

The request body, the web proxy, and MCP arguments have no field for the
prefix. The API process environment is the only source.

## Local numbers stay on the serving machine

A local latency is the machine that served the model. A laptop and a
hosted API have different clocks, different GPUs, and different queues.
Quantization changes the weights. A small model such as `lfm3:latest`
also calls tools differently from `gpt-4o-mini`. Putting those rows in
the same table as hosted `gpt-4o-mini`, or over the fake
`<sha12>-bench.json` file, would make the comparison look valid.

A local bench writes `<sha12>-bench-localhost.json` or
`<sha12>-bench-other.json`. The results document records provider,
host class, model id, and pin file name. The URL stays out of the file.
`comparable_to_hosted` is false. `--baseline` and `--write-baseline`
run only when the provider is `fake`.

The price file for `lfm3:latest` has both rates at exactly 0 and
`billing` `local`. The run is unbilled. `--paid` is refused.
A later edit that puts a non-zero rate on that row fails the bench
instead of inventing a price. There is still no running dollar cap.
Hosted OpenAI keeps `--paid` and the dated list price.

`response_format` `json_object` stays on every chat call. Whether a
given local model honors it is UNVERIFIED until someone runs that
server. The committed local command against live Ollama is UNVERIFIED
for the same reason.
