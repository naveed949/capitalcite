# 0014. Optional entity graph as an agent tool

Date: 2026-10-16

Status: Accepted

Issue #16 asks for a Neo4j graph of bank holding companies, subsidiaries
(FFIEC NIC), filings, and reported metrics, plus a `graph_query` tool
for the agent. The graph is optional. Retrieval and `/health` must work
the same way when Neo4j is not configured. This record is why the graph
is a tool rather than a retrieval mode, why the query is a typed kind
rather than Cypher, and why tests use an in-memory store.

## When a knowledge graph helps RAG

Hybrid search ranks passages that are close to the question in wording
or embedding space. That wins when the answer lives in one 10-K
paragraph or table cell. It loses when the question is a join: parent
to subsidiary, ticker to RSSD, which legal entity under this BHC
reported this metric. Those facts are related by identity, not by
wording. Reciprocal rank fusion cannot walk `PARENT_OF`.

A graph earns its place on those multi-hop entity questions. It does not
earn a place on definition lookup, single-span CET1 extractive items, or
same-filing numeric comparisons. Those already have a measured hybrid
baseline. Fusing graph neighbors into every `retrieve()` would change
that baseline for questions the graph cannot help.

So `graph_query` is a fifth agent tool. `retrieve()`, `POST /search`, and
`POST /ask` with `path=rag` do not open Bolt. Blank `NEO4J_URI` means
the tool returns `graph not configured` and the rest of the process is
unchanged. `/health` stays process-local.

## Typed queries, not Cypher from the model

The planner is untrusted. A free Cypher string is prompt injection into
the database and a moving target for eval. The gate subset has no `enum`
keyword (ADR 0009). `kind` is a string. `GraphQuery` parses it. Kinds
are `subsidiaries`, `parent`, `filings`, `metric`, and `path`. The store
walks `PARENT_OF`, `FILED`, and `REPORTS`. MERGE keys are RSSD for a
bank, accession for a filing, and `(ticker, name, period)` for a metric.

`capitalcite.agent.graph` is the LangGraph loop. The entity graph lives
in `capitalcite.kg`.

## Memory is the test graph. Neo4j is optional durability

Pytest cannot open Bolt. `conftest.py` blocks httpx. There is no
official embedded Neo4j for Python. `MemoryGraphStore` loads the
committed snapshot and answers the same queries. `eval --suite graph
--small` uses that store. `Neo4jGraphStore` exists for operators who
set `NEO4J_URI`. Compose starts Community Edition only with
`--profile graph`, so `docker compose up -d` does not boot it. A VM
without Docker records live Bolt as UNVERIFIED.

The loader is MERGE/reconcile. Running it twice yields the same node
and edge counts.

The snapshot is recorded FFIEC-style identifiers for JPM, BAC, WFC, and
C plus the 10-K accessions already in the handwritten gold. CI does not
download NIC.

## Graph questions stay out of v1 recall

The 77-item handwritten matrix needs `document.md` spans. RSSD joins do
not have those spans. They live in `evals/golden/graph.jsonl`. The eval
reports hybrid substring hit rate on the `--small` fixture corpus
versus graph exact match on that set, then the delta. The published
hybrid table is not rewritten by this suite.

## Alternatives

Putting the graph inside `retrieve()` would change every search when
URI is set. The issue asked for a tool.

Using NetworkX as the only product graph would make Neo4j a decorative
export. The issue named Neo4j. The memory store is the test double, not
the production claim.

Live FFIEC NIC in CI would need network. The snapshot is recorded, like
EDGAR fixtures.
