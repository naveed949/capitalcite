# 0005. Hybrid retrieval

Date: 2026-10-16

Status: Accepted

Issue #4 embeds `Chunk.text` from `chunks.jsonl` and returns ranked
citations. The chunker already wrote the span. The index does not open
`document.md` and does not chunk again. This record is why the dense model,
the BM25 vector, the fusion rule, and the filters look the way they do.

## One collection is the index

A caller asks `retrieve` for a query, a mode, and a limit. The mode is
`dense`, `bm25`, or `hybrid`. The result is a list of hits. Each hit has a
score and the `Chunk` that was stored. `year` on the hit is computed from
`chunk.period`. It is not a second field a writer can set to a different
year.

The thing that gets stored is an `IndexConfig`. It records the embedder id,
the dense width, the chunker, the chunk size, the overlap, the collection
prefix, and the fixed schema `cosine`, `dense`, `bm25`, `idf`. `config_hash`
is the SHA-256 of that record as compact JSON with sorted keys. The Qdrant
collection name is `{prefix}-{config_hash}`. `QDRANT_COLLECTION` stays the
prefix, today `filings`. Two embedders cannot share a collection, because
the embedder id is inside the hash. BGE and MiniLM are both 384 dimensions.
A shared name would mix their points.

The point id is UUID5 of `doc_id`, the chunker, `start`, and `end`. A
rebuild of the same span upserts that id. After the upsert, the build
scrolls the collection and deletes ids this walk did not write. The sidecar
`{input_dir}/index/{collection_name}.json` is written last, through
`atomic_write_text`. A crash before that write leaves a collection the next
successful run repairs. Search does not read the sidecar. It reads Qdrant.

`capitalcite index` is the command that fills the store. `POST /search` is
the command a caller uses to read it. `/health` stays a process check. The
lifespan does not construct a Qdrant client. A search builds a client for
that request.

The store location is not part of the config hash. `QDRANT_PATH` opens
embedded Qdrant on that directory (`AsyncQdrantClient(path=...)`).
`QDRANT_URL=:memory:` opens a process-local store. Those two processes
cannot share `:memory:`. Verification indexes and serves from the same
`QDRANT_PATH`. When neither is set, the client dials `QDRANT_URL`. Path
wins if both are set. A down remote becomes HTTP 503. An empty collection
is HTTP 200 with `point_count` 0. Verification treats a missing corpus or
a 503 as unverified. A 200 whose hits lack citation fields fails.

A remote Qdrant server must be 1.16 or newer. Parameterized
`RrfQuery(rrf=Rrf(k=...))` is not on 1.15.3. Compose pins
`qdrant/qdrant:v1.16.2`. Embedded `qdrant-client` 1.19 local mode ships
its own engine and does not use that image.

## Which embedding model

The default is FastEmbed `BAAI/bge-small-en-v1.5`, selected by
`EMBEDDER=bge-small`. It is an English model, 384 dimensions, and it runs
on the machine that indexes. No API key is required. The model was trained
with a query instruction, so `embed_query` prepends that sentence to the
dense vector only. The sparse vector is counted from the raw query, so the
instruction words do not become terms.

`minilm` is `sentence-transformers/all-MiniLM-L6-v2`, also local and also
384 dimensions, without that instruction. It exists so a later eval can
compare two local models on the same chunks. The hash keeps their points
apart.

`openai-small` is `text-embedding-3-small`, 1536 dimensions. It is built
only when `OPENAI_API_KEY` is set. That variable is the secret for the paid
embedder. The repo does not contain a key. FastEmbed is the free local
default. Pytest uses `FakeEmbedder`, dense width 8, and the same term
counter as production. It does not download a model and it does not open a
socket. The test client is `AsyncQdrantClient(":memory:")`.

## BM25 and dense search answer different misses

A dense vector places a chunk near other chunks that use similar language,
even when the query and the chunk share few exact words. Cosine distance on
that vector is the score. The index underneath is HNSW, described below. A
dense search can miss a rare token that the question names exactly, because
the walk never visits that point, or because the point sits far from the
query in the embedding space.

BM25 does the opposite. It scores a chunk by how often the query's terms
appear in it, and by how rare those terms are in the collection. A term
that shows up in every 10-K is worth little. A term that shows up in one
filing is worth a lot. We store raw term counts, not precomputed weights.
The token is lowercased and hashed with blake2s to a 32-bit index, so there
is no vocabulary file to keep in sync with the points. Two different tokens
can land on the same index. Qdrant applies `Modifier.IDF` at query time,
using document frequency in the collection. That is the BM25 leg. It will
not find a paraphrase that shares no tokens.

Hybrid search runs both and fuses them. The milestone asked for that shape.
ADR 0001 already chose Qdrant because it can hold both vectors and fuse
them, instead of a second keyword store beside pgvector.

## Fusion uses ranks, and Qdrant's rank starts at zero

Cosine and IDF are not the same unit. A weighted sum needs a weight that
says how many cosine points equal one IDF point. We have not measured that
weight. Reciprocal rank fusion ignores the raw scores and adds
`1 / (k + rank)` for each list a point appears in. `RRF_K` defaults to 60,
the constant from the Cormack paper, and it is not part of the config hash
because it does not change stored vectors. There is no weighted-fusion
field.

`reciprocal_rank_fusion` in this repo is the paper formula. Rank starts at
1. The lists `a, b` and `b, c` at k 60 score `b` at `1/61 + 1/62`, `a` at
`1/61`, and `c` at `1/62`. Tests lock those numbers. Production search does
not call that function. It sends `RrfQuery` to Qdrant with `k` set from
`RRF_K`. Each hybrid prefetch asks for `max(10 * k, 50)` candidates so a
long 10-K still has room for both legs. Qdrant's rank index starts at 0, so a first-place hit from one
list scores `1/k`, not `1/(k+1)`. With the default 60, a point that is
first on both legs scores `1/30`. Tests that go through the memory client
lock that Qdrant number. We do not pass `k+1` to sneak the paper formula
into the server. `RRF_K` means Qdrant's `k`.

## Filters are payload equalities

Ticker, form, and period ride on the chunk so a search can narrow without
joining back to the ingest sidecar. `parse_filters` is the only constructor
for a filter. It uppercases ticker and form. A period is either an exact
`YYYY-MM-DD`, which matches the `period` payload, or a 4-digit year, which
matches the integer `year` stored next to the chunk. That integer is
`year_of(period)`. A chunk whose period is not a date stores a null year
and will not match a year filter. The response still derives `Hit.year`
from `period`, so the payload integer cannot disagree with the year the
caller sees.

## Rerank stays off

A cross-encoder reads the query and the passage together and can repair a
fusion mistake. It also downloads another model and adds latency on every
search. This issue has not measured that gain. `retrieve` returns Qdrant's
order, truncated to `k`. There is no `RERANKER` setting. A setting with one
legal value would be a second way to say the same thing. The README row for
hybrid plus rerank stays empty until an eval fills it.

## What HNSW is doing under the dense leg

Qdrant does not compare the query to every dense vector. It builds a
hierarchical navigable small world graph. The top layer has few points and
long edges. Each lower layer has more points and shorter edges. The bottom
layer contains every point. Search enters at the top, walks to the neighbor
closest to the query, then drops a layer and repeats. The walk is greedy.
`hnsw_ef` is the width of the candidate list kept during the walk. A larger
`ef` visits more neighbors and raises the chance of finding the true
nearest chunk. It also costs more latency. We leave Qdrant's HNSW settings
at their defaults. Choosing `ef` is an eval question, not a guess.

The result is approximate nearest neighbor under cosine distance. That is
why a dense hit can miss a chunk BM25 still finds, and why a hybrid query
asks both legs before fusion. The BM25 leg is an inverted index over the
sparse counts, not an HNSW graph.

## Section labels on fixed and recursive chunks

`section` on a chunk is the Item that contains the chunk offset, which is
the start of the window. A fixed or recursive window can begin in Item 7
and end in Item 8 and still be labeled Item 7. Section-aware chunks are cut
on Item spans first, so the label matches the span. Section is the
canonical strategy for this index. The hash records the chunker, the size,
and the overlap. A `chunks.jsonl` row whose `chunker` disagrees with the
config fails the build. We do not re-chunk, and we do not change the
chunkers.

## What this leaves open

Quote-index sanity indexes each handwritten golden quote as its own chunk
and retrieves the question with hybrid search. The number it prints is how
often the gold quote lands in the top 5 when the index contains only those
quotes. Quotes that share the question's words compete for those slots.
It is not recall over the twelve ingested filings. Issue #6 owns that
matrix, and the README eval cells stay TBD until that run exists.

One `capitalcite index` process is the writer. Two overlapping builds can
delete each other's orphans. That race is outside this contract. There is
no lock.
