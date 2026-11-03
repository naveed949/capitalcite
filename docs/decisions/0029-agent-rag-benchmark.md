# 0029. Agent vs RAG latency and cost bench

Date: 2026-11-03

Status: Accepted

An operator who runs CapitalCite needs to know where each answer's time and
money go: which phase is the limiter, and what one correct answer costs. An
end-to-end stopwatch cannot say that. This record is why the bench folds
`observation()` spans, why a paired bootstrap sits on the graph-vs-loop
delta, why cost per correct is the published money number, and why
`ASK_MODEL=fake` cannot prove model latency.

## Per-phase timing, not only end-to-end

A 40 ms `/ask` is one number. It does not say whether retrieve, the tool
gate, graph compile, or the generator spent it. An optimization aimed at
the wrong phase does not move the number that users feel.

The bench reuses `observation()` and `MemoryExporter`. Retrieve spans sum
to retrieval time. Tool and gate spans sum to tool time after nested
retrieve children are subtracted, so a `search_filings` call is not
counted twice. Generate spans sum to model time. A `compile` child of the
agent span is the LangGraph `StateGraph.compile()` call. Total is the
outer ask span. Time to first token is the first generate begin minus the
ask begin. There is no second clock.

With n=95, p50 and p95 are interpolated between adjacent order statistics
(Hyndman-Fan type 7). p95 is not a single sample. At this n the 95th
percentile sits near the 90th order statistic. A one-item tail still moves
it. The nightly gate therefore uses a 2x band on p95, not a 1 ms budget.
That band absorbs scheduler noise on a shared CPU runner. It still fails
when retrieve or compile doubles.

## Paired bootstrap, not an unpaired one

Graph and loop answer the same 95 items. The interesting quantity is the
per-item difference, not the difference of two independent means.

An unpaired bootstrap resamples each arm on its own. Hard items stay hard
on both arms. Resampling them separately adds that shared variance to the
interval. A paired bootstrap resamples the 95 differences. The shared
item effect cancels. The interval shrinks when the arms agree, which is
the usual case for the same tools and the same fake planner.

B=10000 and seed=20261103 are pinned. The unit test feeds a constant
delta of 0.25. The interval is [0.25, 0.25]. A second test feeds
perfectly correlated 0/1 pairs. The paired interval is narrower than the
unpaired interval on the same draws.

## Cost per correct answer, not only cost per query

Cost per query is what the vendor bills. Cost per correct is what an
operator pays for a useful answer. A cheap path that is wrong on every
item has cost per correct of infinity. A more expensive path that is
right more often can be cheaper per correct.

Fake-model cost is 0. Token counts are still real: `o200k_base` over the
prompts the adapters would send. The dated table
`evals/bench/prices/openai-2026-11-03.json` is the list price for
`gpt-4o-mini` as of 2026-11-03 from platform.openai.com/docs/pricing.
A paid run is recorded in `evals/bench/pin.json` and was not executed.

## What a graph runtime buys, and what it costs

LangGraph owns plan → act* → synthesize. State is a typed dict. Conditional
edges pick stop, act, or synthesize. A compiled graph can checkpoint and
branch. HTTP streaming observes the same nodes.

A plain while-loop calls the same `plan_step` / `act_step` /
`synthesize_step` functions. It has no checkpoint, no graph-shaped
branch table, and no compile. On the fake planner the answers match. The
Recall@5 delta and the accuracy delta are 0.000 with a 95% CI of
[0.000, 0.000]. The graph pays compile and dispatcher overhead. That is
the cost of the runtime, not a quality gain, on this model.

## The fake model cannot measure model latency

`ASK_MODEL=fake` does not call a network generator. The generate span is
the heuristic planner and a quote of a hit. Model time on that path is
not the latency of `gpt-4o-mini`. Token counts of the prompts are real.
The time to first token is time-to-first-generate-span, not a streamed
token from the API. Every number that needs a live model is UNVERIFIED
until the pinned paid command runs.

## Compile once is the applied optimization

The measured bottleneck on the graph arm is **tool** (gate plus execute,
exclusive of nested retrieve). `run_agent` previously compiled a
`StateGraph` on every question. Nodes now read model, tools, and observe
from a contextvar, so one compiled graph serves every call. Compile mean
drops. Tool remains the largest phase. The fake model still leaves
generator latency unproven.
