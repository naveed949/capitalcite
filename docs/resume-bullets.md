# Resume bullets

Repo. [capitalcite](https://github.com/naveed949/capitalcite).
Every number below appears in `README.md`. Cells point at the results
file that backs the README line. Live Azure apply and live OpenAI
answer quality are UNVERIFIED and are not claimed here.

- Built [capitalcite](https://github.com/naveed949/capitalcite), a
  cited-answer RAG system over U.S. bank 10-Ks. Section/hybrid
  Recall@5 is 0.169 on n=77 handwritten spans
  ([`7388ac3eea43.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43.json)).
- Showed lexical BM25 beating dense on the same section chunks.
  Recall@5 0.182 versus 0.078, an eight-item gap above the 0.013
  noise floor
  ([`7388ac3eea43.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43.json)).
- Measured a plan-then-act agent against plain RAG on n=15
  comparison and multi-hop items. Recall@5 0.400 versus 0.267 with
  the fake/heuristic planner, not a live LLM
  ([`7388ac3eea43.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43.json)).
- Shipped a fail-closed tool gate (5/5 red-team), a hash-chained
  audit log (3 cases, pass rate 1.00), and `capitalcite check`
  quote-index sanity 36/77 plus fixture span-overlap 3/3
  ([`7388ac3eea43-gate.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43-gate.json),
  [`7388ac3eea43-audit.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43-audit.json),
  [`7388ac3eea43-check.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43-check.json)).
- Timed fake-model RAG at 14 ms p50 and 16 ms p95 on the 12-point
  fixture. Azure Container Apps IaC is in-repo. Live apply is
  UNVERIFIED
  ([`7388ac3eea43-latency.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43-latency.json)).
- Added an optional BHC entity graph. n=6, memory-snapshot exact
  1.000 versus fixture-substring 0.000
  ([`7388ac3eea43-graph.json`](https://github.com/naveed949/capitalcite/blob/main/evals/results/7388ac3eea43-graph.json)).

## Number map

Locators such as L79 are README line numbers. They are not metrics.

| Number | README | Results file |
| --- | --- | --- |
| 0.169 | L79 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 77 | L71 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 0.182 | L78 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 0.078 | L77 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 0.013 | L85 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 15 | L108 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 0.400 | L109 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 0.267 | L108 | [evals/results/7388ac3eea43.json](../evals/results/7388ac3eea43.json) |
| 5/5 | L199 | [evals/results/7388ac3eea43-gate.json](../evals/results/7388ac3eea43-gate.json) |
| 3 | L138 | [evals/results/7388ac3eea43-audit.json](../evals/results/7388ac3eea43-audit.json) |
| 1.00 | L138 | [evals/results/7388ac3eea43-audit.json](../evals/results/7388ac3eea43-audit.json) |
| 36/77 | L141 | [evals/results/7388ac3eea43-check.json](../evals/results/7388ac3eea43-check.json) |
| 3/3 | L141 | [evals/results/7388ac3eea43-check.json](../evals/results/7388ac3eea43-check.json) |
| 14 ms | L147 | [evals/results/7388ac3eea43-latency.json](../evals/results/7388ac3eea43-latency.json) |
| 16 ms | L147 | [evals/results/7388ac3eea43-latency.json](../evals/results/7388ac3eea43-latency.json) |
| 12-point | L151 | [evals/results/7388ac3eea43-latency.json](../evals/results/7388ac3eea43-latency.json) |
| 6 | L125 | [evals/results/7388ac3eea43-graph.json](../evals/results/7388ac3eea43-graph.json) |
| 1.000 | L126 | [evals/results/7388ac3eea43-graph.json](../evals/results/7388ac3eea43-graph.json) |
| 0.000 | L125 | [evals/results/7388ac3eea43-graph.json](../evals/results/7388ac3eea43-graph.json) |
