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
`--paid` on the bench. Loopback (`localhost`, `127.0.0.1`, `::1`) is
provider `local`, host class `localhost`, unbilled. `api.openai.com` is
provider `hosted`, host class `openai`, billed. Any other host is
provider `compatible`, host class `other`: a third-party Chat Completions
server whose rate is unknown. The process never sends `Authorization` to
non-OpenAI hosts, even when `OPENAI_API_KEY` is set, so a hosted secret
does not leave the machine. Embeddings ignore the chat prefix and still
use `https://api.openai.com/v1/embeddings` when `EMBEDDER=openai-small`.

`check --live` passes no base URL, so the call is hosted `gpt-4o-mini`.

The request body, the web proxy, and MCP arguments have no field for the
prefix. The API process environment is the only source.

A non-OpenAI path must end in `/v1`, so a prefix such as
`https://openrouter.ai/api/v1` is valid. `api.openai.com` still accepts
only an empty path or `/v1`. `ASK_OPENAI_TIMEOUT_S` is the httpx timeout
for that POST. Empty uses 30 seconds. A value of 0 or below fails at
Settings load.

## Hosted spans stay on the OpenAI name

ADR 0028 collectors chart `gen_ai.provider.name`. On `main`, a hosted
`gpt-4o-mini` span used the OpenTelemetry value `openai`, which
`generation_attrs` already defaults to when the model is not `fake`.
Results JSON still records provider `hosted`. The span attribute is
`openai` for hosted OpenAI, `local` for loopback, and
`openai-compatible` for a third-party host. Mixing `hosted` into that
key would split the chart from the `main` series.

## Local numbers stay on the serving machine

A local latency is the machine that served the model. A laptop and a
hosted API have different clocks, different GPUs, and different queues.
Quantization changes the weights. A small model such as `lfm3:latest`
also calls tools differently from `gpt-4o-mini`. Putting those rows in
the same table as hosted `gpt-4o-mini`, or over the fake
`<sha12>-bench.json` file, would make the comparison look valid.

A local bench writes `<sha12>-bench-localhost.json`. The results
document records provider, host class, model id, and pin file name. The
URL stays out of the file. `comparable_to_hosted` is false. `--baseline`
and `--write-baseline` run only when the provider is `fake`.

The price file for `lfm3:latest` has both rates at exactly 0 and
`billing` `local`. The run is unbilled. `--paid` is refused.
A later edit that puts a non-zero rate on that row fails the bench
instead of inventing a price. There is still no running dollar cap.
Hosted OpenAI keeps `--paid` and the dated list price.

A third-party host is not $0. It is not hosted OpenAI either. `POST /ask`
can still call it. The bench refuses it: there is no honest price, and
writing 0 would look like a local laptop run. An explicit priced pin for
that host is a later change.

The local pin's `paid_run.command` is the recipe, so it may contain
`ASK_OPENAI_BASE_URL=http://localhost:11434/v1`. Other pin fields still
reject a URL. Results JSON still rejects a URL.

`response_format` `json_object` stays on every chat call. Whether a
given local model honors it is UNVERIFIED until someone runs that
server. The committed local command against live Ollama is UNVERIFIED
for the same reason.
