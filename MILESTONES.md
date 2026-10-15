# Milestones: capitalcite

Each item below is written as a GitHub issue. Labels to use: `m1` `m2` `m3`, `rag`, `eval`, `agent`, `security`, `infra`, `docs`.

---

## M1: RAG + Evals Foundation (Week 1)
**Goal:** a measured, reproducible hybrid-RAG baseline over bank filings.

### #1 Project scaffold (async FastAPI + tooling)
- **Scope:** Python 3.12, `uv`, FastAPI (async), Pydantic v2 settings, ruff + mypy + pytest, docker-compose with Qdrant (or Postgres+pgvector) and Langfuse.
- **Acceptance criteria:**
  - [ ] `docker compose up` + `uv run uvicorn ...` serves `GET /health` → 200
  - [ ] CI runs lint, type-check, and tests on every PR
  - [ ] `.env.example` documents every required variable (including `SEC_USER_AGENT`)

### #2 EDGAR ingester for bank 10-K/10-Q
- **Scope:** look up CIKs via `company_tickers.json`, list filings via `data.sec.gov/submissions`, download primary docs, and store raw files with metadata (ticker, form, period, accession, URL).
- **Acceptance criteria:**
  - [ ] `capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K --years 2023-2025` downloads the expected filings
  - [ ] Declared User-Agent, ≤10 req/s, retry with backoff; reruns don't re-download (idempotent)
  - [ ] Unit tests use recorded fixtures, with no live network in CI

### #3 Parsing + three chunking strategies
- **Scope:** HTML/PDF → clean text, with tables kept as markdown. Chunkers: fixed-size, recursive, and section-aware (10-K Item boundaries such as Item 7 MD&A and Item 8).
- **Acceptance criteria:**
  - [ ] Each chunk carries `doc_id`, `section`, `page/offset`, and `source_url` for citations
  - [ ] The chunker is picked by config; stats (count, mean tokens) are logged per strategy
  - [ ] Tests cover table preservation and section-boundary detection

### #4 Embedding + hybrid retrieval
- **Scope:** at least 2 embedding models, a dense index (Qdrant/pgvector), and BM25. Fuse results with Reciprocal Rank Fusion; optional cross-encoder rerank.
- **Acceptance criteria:**
  - [ ] `retrieve(q, mode=dense|bm25|hybrid, k)` API with an async implementation
  - [ ] Index builds are reproducible from config and record the config hash
  - [ ] `POST /search` returns ranked chunks with scores and citations

### #5 Golden Q&A set v1
- **Scope:** about 100 items: hand-written questions over the ingested filings (with numbers checked against XBRL `companyfacts` where possible), plus a filtered FinanceBench/FinQA subset. Include unanswerable questions so refusals get tested.
- **Acceptance criteria:**
  - [ ] JSONL schema: `id, question, answer, evidence[{doc_id, section, quote}], type, answerable`
  - [ ] At least 15% of items are unanswerable or trick questions; at least 30% are numeric
  - [ ] `evals/golden/README.md` documents how items were sourced and checked

### #6 Eval harness + baseline tables
- **Scope:** RAGAS and/or DeepEval, plus custom recall@k / MRR / numeric exact-match / citation accuracy.
- **Acceptance criteria:**
  - [ ] `capitalcite eval --suite golden` writes `evals/results/<sha>.json`, with model versions recorded
  - [ ] A chunking × embedding × retrieval-mode matrix is run and committed
  - [ ] README eval tables are generated from the results file (no hand-typed numbers)

---

## M2: Agent, Fail-Closed Gate, Observability (Week 2)
**Goal:** a multi-step agent whose every action is gated, traced, and regression-tested.

### #7 Structured answer schema + `/ask` endpoint
- **Scope:** Pydantic `Answer{answer, citations[], confidence, refusal_reason}`; validation-retry on schema failure.
- **Acceptance criteria:**
  - [ ] 100% of `/ask` responses validate against the schema (enforced by tests)
  - [ ] Each citation resolves to a stored chunk; unresolvable citations get rejected
  - [ ] Insufficient evidence leads to an explicit refusal, not a guess

### #8 Plan-then-act agent (LangGraph or PydanticAI)
- **Scope:** planner → tool executor → synthesizer graph. Tools: `search_filings`, `xbrl_fact`, `fdic_bankfind`, `calculator`.
- **Acceptance criteria:**
  - [ ] Handles multi-hop questions (e.g., comparing a metric across 2 banks and 2 years), with the plan exposed in the response
  - [ ] Step and token budgets enforced; graceful stop when exceeded
  - [ ] Agent-vs-RAG comparison added to the eval tables

### #9 Fail-closed tool gate
- **Scope:** policy file (allow-list per tool, argument schemas, rate/budget limits). Default deny; denied calls return a structured error to the agent.
- **Acceptance criteria:**
  - [ ] An unknown tool, invalid args, or a policy-load failure all lead to a **deny** (tested)
  - [ ] Red-team suite: prompt-injection text inside retrieved docs can't trigger disallowed calls
  - [ ] Gate decisions add < TBD ms p95 overhead (measured, not assumed)

### #10 Append-only audit log
- **Scope:** hash-chained JSONL (or a Postgres table) recording timestamp, trace id, tool, args digest, decision, and output digest.
- **Acceptance criteria:**
  - [ ] `capitalcite audit verify` detects any tampered or removed record (tested)
  - [ ] Every gated call, allowed or denied, produces exactly one record
  - [ ] Records link to Langfuse trace ids

### #11 Langfuse tracing
- **Scope:** spans for retrieval, each LLM call, each tool call, and gate decisions; cost and latency attributes.
- **Acceptance criteria:**
  - [ ] One `/ask` call gives a single trace tree with all the spans above
  - [ ] Eval runs are tagged with commit SHA and config hash
  - [ ] README includes a screenshot of a real trace

### #12 Behavioral regression tests in CI
- **Scope:** contract tests (e.g., "always cites", "refuses unanswerable", "never calls tools outside the policy") plus an eval-subset regression gate.
- **Acceptance criteria:**
  - [ ] A GitHub Action runs a fast golden subset on PRs and the full set nightly
  - [ ] The PR fails if any tracked metric drops more than a configured threshold below the committed baseline
  - [ ] Baseline updates require an explicit `eval-baseline` label

---

## M3: Ship & Showcase (Week 3)
**Goal:** a deployed demo, a polished README, and the optional extras.

### #13 Containerize + deploy to Azure Container Apps
- **Scope:** multi-stage Dockerfile, managed vector DB or a Qdrant container, secrets in Key Vault or ACA secrets, IaC (Bicep or Terraform).
- **Acceptance criteria:**
  - [ ] `make deploy` (or a GH Action) deploys from scratch, and the public `/health` endpoint works
  - [ ] No secrets in the image or repo (checked with gitleaks in CI)
  - [ ] Scale-to-zero configured; cost notes in the README

### #14 Demo + README polish
- **Scope:** fill every TBD from real eval results, add an architecture diagram, a short demo GIF or video, and a design-tradeoffs section.
- **Acceptance criteria:**
  - [ ] No `TBD` left in README eval tables; each number links to a results file
  - [ ] A "Tradeoffs & what I'd do next" section covers chunking, hybrid search, and gate design
  - [ ] A fresh clone gets to a working local quickstart in < 15 minutes (tested by following the README)

### #15 (Optional) MCP server
- **Scope:** expose `search_filings` and `ask` as MCP tools, routed through the same gate and audit log.
- **Acceptance criteria:**
  - [ ] Works from an MCP client (e.g., an IDE or desktop client)
  - [ ] MCP calls show up in the audit log and in Langfuse

### #16 (Optional) Neo4j entity graph
- **Scope:** bank holding company → subsidiaries (FFIEC NIC) → filings → reported metrics; a `graph_query` tool for the agent.
- **Acceptance criteria:**
  - [ ] Loader is idempotent; graph schema documented
  - [ ] At least 5 golden questions that need graph traversal, with an eval delta reported

### #17 Resume & write-up
- **Scope:** a blog post or LinkedIn write-up, and resume bullets that link to the repo and its eval results.
- **Acceptance criteria:**
  - [ ] The write-up includes real metrics and one failure analysis
  - [ ] Resume bullets cite only numbers that appear in the README
