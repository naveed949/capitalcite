# 0027. Metrics exact-match eval

Date: 2026-10-24

Status: Accepted

An analyst who asks "What were JPM's total deposits in 2026Q2?" needs one warehouse cell, or a refusal when that cell is null. Span-overlap recall does not say whether the number was right. This record is why the metrics suite scores the printed figure, why refusal has its own denominator, and why the committed baseline is the fake model.

## Exact match is the string the tool already prints

`render_figure` quantizes a `Decimal` to ten decimal places, strips trailing zeros, and prints no exponent, no thousands separator, and no scale word. That string is the rounding policy. The suite does not add a second epsilon.

`cells_equal` compares floats with `math.isclose`. `figures_in` drops scale words. Either one would accept a rounded or rescaled answer as the same figure. Lead-bank deposits are thousands of dollars as reported. The JPM 2026Q2 cell renders as `2820284000`. The phrase "2.82 billion" is a different quantity, and a comma-grouped "2,820,284,000" is a different string. The scorer looks for the rendered token. A following digit or a decimal point that continues into a digit is not an edge, so `0` does not match inside `10` or `10.0`. A sentence period does not continue the number, so the citation quote can end the figure with one. A trailing percent sign is an edge, because NIM and the efficiency ratio are printed with one. A minus sign is part of the token, so a loss does not match the absolute value.

A reported zero stays a value. JPM's efficiency ratio for 1984Q1 is `0` in the Call Report. It is a figure to match, and the item stays in the exact-match denominator.

## Refusal is a separate rate

A null cell is a different outcome. Deposit growth for 1984Q1 has no prior quarter in the fixture. Holding-company deposits and holding-company net income are absent from the cited accession, and the fixture has no quarterly flow to derive. The correct behavior is a refusal. An answer that quotes some other number from a 10-K is a miss of a different kind.

Those items stay out of the exact-match denominator. A refusal on a null cell would otherwise look like a wrong figure, and a wrong figure on a value cell would dilute the refusal rate. Exact match counts value cells only. Refusal counts null cells only. On each path the two denominators add up to n. The committed run has 15 value cells and 3 null cells, 18 items in all.

A single score that treated "refused" as correct on a null item would hide a path that answers when it should refuse. The rates stay separate so a reviewer can see both.

## The fake model is the baseline, and it proves the tool path

`ASK_MODEL=fake` is deterministic and uses no API key. On these questions it plans one `metric_query`, the catalog SQL returns the cell, and the quote contains `render_figure` of that cell. `ground()` accepts the quote. A null cell comes back refused.

The committed run, `evals/results/3231cd4138ba-metrics.json`, measured agent exact match at 15/15 and agent refusal at 3/3. RAG exact match was 0/15. RAG refusal was 1/3. The agent row shows that this question shape reaches the tool and that the catalog cell matches the fixture derivation. The RAG row shows that hybrid search over the fixture 10-K text does not produce the same token. That is the comparison the table is for.

The same run does not show that a live planner will choose `metric_query`. It does not show that a live synthesizer will copy the figure instead of rounding it or attaching a scale word. Those models stay unwired. The suite requires `METRIC_QUERY_BACKEND=catalog` and `WAREHOUSE_PATH` pointing at `dbt/target/ci.duckdb`. It does not shell MetricFlow and it does not shell dbt. A missing warehouse exits 2 and does not rewrite hits.

## A warehouse cell is not a retrieved span

Retrieval asks whether a chunk overlaps a gold quote in a filing. The digit in that quote can be a different entity, a different period, or a rounded headline. `metric_query` accepts one metric name from the semantic layer, one ticker, and one quarter-end. The catalog SQL is parameterized. SEC dollars and FDIC thousands sit on separate `entity_level` rows and are never added. The citation is the fact row.

Coverage of handwritten numeric items in `evals/golden/v1.jsonl` asks a different question. How many of those questions name one of JPM, BAC, WFC, or C, name one mart concept in a long phrase, and resolve to a non-null cell. The runner counted 0 cells out of 56 items. Most of those questions are capital ratios, per-share amounts, or year-only net income. Year-only net income is not one quarter. The phrase list does not treat bare "assets", "deposits", or "nim" as a metric. The published fraction is that count. It is recomputed from the dispositions, not typed in as 56.

## Alternatives that stay unwired

RAGAS faithfulness and answer relevancy need a judge model and an API key. ADR 0006 left them out of the committed retrieval table for that reason. They still do not say whether the deposits token matched.

DeepEval can score a numeric answer with a tolerance, or by asking a model. A tolerance reopens the rounding question `render_figure` already closed. A model judge can accept "about 2.8 trillion" for a figure whose unit is thousands of dollars.

An LLM-as-judge can grade prose. It can also agree with a paraphrase that changed the unit. The score moves between runs, the review needs a key, and a laptop cannot rerun it. The catalog path reruns in CI with no key. A later live-model suite should use this same token rule on the same pointers. Replacing the rule with a judge would make the baseline unreproducible.

`capitalcite check` copies the five rates onto schema 2 of `evals/baselines/ci.json`. One dropped hit fails. An extra hit passes. The item budget for all five rates is zero.

Gold rows store an id, a question, and a source pointer. They have no field for the figure. `resolve` reads the fixture JSON with `Decimal` and never opens DuckDB. A results file may contain `render_figure` output from a run. The write-up checker recounts the score booleans when `suite` is `metrics`, and it does not treat a rendered figure as a number the write-up may cite.
