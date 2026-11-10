# 0031. Fixture paid bench record

Date: 2026-11-10

Status: Accepted

An operator who reads the paid bench can treat 9/95 as `gpt-4o-mini`
quality and $0.0690 as the invoice. The 2026-11-10 run recorded a
fixture bill and a fixture-index refusal rate. This record is why
cost per query on that run is still a real bill, why the accuracy
is index coverage, why the pinned command repeats the fixture, and
what a twelve-filing run still needs.

## Cost per query is still a bill on the fixture

The vendor bills the completions that ran. A refusal that called the
model still costs money. The fixture still runs retrieve, the tool
gate, graph or the plain loop, and the hosted generator. Latency and
token volume on that path belong to the hosted API.

OpenAI billed 206,221 input tokens and 10,725 output tokens across
222 chat-completion calls. The pinned list price is $0.15 per 1M
input tokens and $0.60 per 1M output tokens. The invoice is $0.0374.

## Accuracy on this index is coverage

Gold lives in the twelve 10-Ks. The index used here is 12 fixture
chunks from the two JPM HTML filings under
`tests/fixtures/chunking/corpus`. `indexed_fixture()` section-chunks
those filings and embeds them with `FakeEmbedder`. Most items refuse
because the fixture has no matching passage. 276 of 285 arm-queries
refused. 174 were `no passages were retrieved`. 79 were `question
names an entity that is not in the retrieved passages`. Each arm is
9/95 (0.095). Every accurate row is a refusal. Recall@5 is not
measured, because the gold spans are not in those fixture documents.
That 9/95 is index coverage. That rate is not `gpt-4o-mini` quality.

## The pinned command repeats the fixture

`evals/bench/pin.json` has no top-level corpus field.
`paid_run.command` omits `--corpus`. `capitalcite eval --suite bench`
defaults `--corpus` to `fixture`, and that default calls
`indexed_fixture()`. Running the pinned command as written repeats
the fixture. A later reader who sees hosted `gpt-4o-mini` and 9/95
needs the pin and the README to say fixture. This pin keeps the
command unchanged and records `corpus` `fixture` on `paid_run`.

## A twelve-filing run is still UNVERIFIED

A real-corpus pin would pass `--corpus full` after ingest, chunk,
and index of the twelve 10-Ks. `indexed_corpus()` looks for at least
12 `metadata.json` files under `raw/edgar`. Gold spans have to land
in that index so Recall@5 is measured. Seed, prompt hash, prices, and
model stay the same. The results file would be new, and it would
record `corpus` `full`. Until that run exists, the twelve-filing
paid bench stays UNVERIFIED.

## Billed tokens and reconstructed tokens

Each query row stores two pairs of counts. `tokens_in` and
`tokens_out` are `o200k_base` counts of the prompts the adapters
would send, plus the local answer or refusal string. `cost_usd`
multiplies those counts by the dated list price. Those fields sum
to $0.0690.

`tokens_in_api` and `tokens_out_api` are Chat Completions usage
copied from spans. They sum to the billed 206,221 and 10,725. 70
queries have null API token fields because no chat completion ran.
Those rows still carry reconstructed `cost_usd`. That is why the
file cost exceeds the invoice.

The README `cost/query` column is the reconstructed-token list price
per query. It is not billed spend divided by 95. Publish the invoice
beside the file sum.

The `len//4` counter in `capitalcite.chunking.tokens` sizes chunks.
The bench does not use it for `cost_usd`.
