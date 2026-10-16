"""BM25 probe for a few golden items. Sparse counts are term_counts."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError
from qdrant_client.models import Modifier

from capitalcite.chunking.models import Chunk
from capitalcite.eval.judge import recall_items
from capitalcite.golden.models import Evidence, GoldenItem
from capitalcite.retrieval.hashing import collection_name, tokens
from capitalcite.retrieval.sanity import span_overlap
from capitalcite.retrieval.service import RetrievalSetup, retrieve
from capitalcite.retrieval.types import Hit, RetrievalMode

PROBE_IDS = ("cc-h-013", "cc-h-063", "cc-h-001")
_SCROLL = 256


async def diagnose_bm25(
    setup: RetrievalSetup,
    items: Sequence[GoldenItem],
    *,
    probe_k: int = 2000,
) -> str:
    """Describe query tokens, gold-chunk overlap, top-5 BM25 hits, and gold rank."""
    wanted = {item.id: item for item in items if item.id in PROBE_IDS}
    ordered = [wanted[item_id] for item_id in PROBE_IDS if item_id in wanted]
    idf = await _idf_active(setup)
    indexed = await _indexed_chunks(setup)
    query_path = (
        "embed_query builds the dense vector (BGE may prefix that vector) and "
        "pairs term_counts of the raw query as the sparse vector. "
        "embed_documents pairs term_counts of chunk.text. "
        "BM25 search sends only the sparse vector."
    )
    lines = [
        "BM25 diagnostic",
        f"collection={collection_name(setup.config)} points probe_k={probe_k}",
        f"chunker={setup.config.chunker.value} embedder={setup.config.embedder_id}",
        f"idf_modifier={idf}",
        query_path,
        "tokenizer=[a-z0-9]+ after lowercasing. CET1 -> cet1.",
        f"rare_token_examples={list(rare_token_examples())}",
        "",
    ]
    for item in ordered:
        embedding = await setup.embedder.embed_query(item.question)
        query_tokens = tokens(item.question)
        unique_query = list(dict.fromkeys(query_tokens))
        gold_rows = [row for row in item.evidence if row.start is not None]
        hits = await retrieve(setup, item.question, mode=RetrievalMode.BM25, k=probe_k)
        gold_rank = _gold_rank(hits, gold_rows)
        gold_chunks = [
            chunk
            for chunk in indexed
            if any(span_overlap(chunk, row) for row in gold_rows)
        ]
        lines.append(f"## {item.id}")
        lines.append(f"question={item.question}")
        lines.append(f"query_tokens={query_tokens}")
        lines.append(
            "query_term_counts="
            + ", ".join(f"{tok}={query_tokens.count(tok)}" for tok in unique_query)
        )
        lines.append(
            f"sparse_dims={len(embedding.sparse.indices)} "
            f"(indices hashed, values are raw counts)"
        )
        for row in gold_rows:
            quote_tokens = tokens(row.quote)
            shared = [tok for tok in unique_query if tok in quote_tokens]
            missing = [tok for tok in unique_query if tok not in quote_tokens]
            lines.append(
                f"gold {row.doc_id} [{row.start},{row.end}) quote_tokens={quote_tokens}"
            )
            lines.append(f"gold_quote_has_query_tokens={shared}")
            lines.append(f"gold_quote_missing_query_tokens={missing}")
        if not gold_chunks:
            lines.append("gold_covering_chunks=none")
        for chunk in gold_chunks:
            chunk_tokens = set(tokens(chunk.text))
            present = [tok for tok in unique_query if tok in chunk_tokens]
            absent = [tok for tok in unique_query if tok not in chunk_tokens]
            rare = [tok for tok in rare_token_examples() if tok in chunk_tokens]
            snippet = " ".join(chunk.text.split())[:160]
            lines.append(
                f"gold_chunk [{chunk.start},{chunk.end}) "
                f"chars={len(chunk.text)} tokens={len(tokens(chunk.text))} "
                f"section={chunk.section!r}"
            )
            lines.append(f"gold_chunk_has_query_tokens={present}")
            lines.append(f"gold_chunk_missing_query_tokens={absent}")
            lines.append(f"gold_chunk_has_rare_tokens={rare}")
            lines.append(f"gold_chunk_snippet={snippet!r}")
        lines.append(
            f"gold_chunk_bm25_rank={gold_rank if gold_rank is not None else f'>{probe_k}'}"
        )
        lines.append("bm25_top5:")
        for index, hit in enumerate(hits[:5], start=1):
            snippet = " ".join(hit.chunk.text.split())[:160]
            overlap = any(span_overlap(hit.chunk, row) for row in gold_rows)
            lines.append(
                f"  {index}. score={hit.score:.4f} doc_id={hit.chunk.doc_id} "
                f"[{hit.chunk.start},{hit.chunk.end}) gold={overlap} {snippet!r}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def items_for_probe(items: Sequence[GoldenItem]) -> tuple[GoldenItem, ...]:
    recall = {item.id: item for item in recall_items(items)}
    return tuple(recall[item_id] for item_id in PROBE_IDS if item_id in recall)


def rare_token_examples() -> tuple[str, ...]:
    """Tokens the review named. Locked so a tokenizer change fails a test."""
    return tuple(tokens("CET1 Wells Fargo SLR 2025"))


async def _idf_active(setup: RetrievalSetup) -> str:
    name = collection_name(setup.config)
    info = await setup._client.get_collection(name)
    sparse = info.config.params.sparse_vectors or {}
    params = sparse.get(setup.config.sparse_vector)
    if params is None:
        return "missing"
    if params.modifier == Modifier.IDF:
        return "IDF"
    return str(params.modifier)


async def _indexed_chunks(setup: RetrievalSetup) -> list[Chunk]:
    name = collection_name(setup.config)
    found: list[Chunk] = []
    offset = None
    while True:
        records, offset = await setup._client.scroll(
            collection_name=name,
            limit=_SCROLL,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            break
        for record in records:
            payload = record.payload or {}
            raw = dict(payload)
            raw.pop("year", None)
            try:
                found.append(Chunk.model_validate(raw))
            except ValidationError:
                continue
        if offset is None:
            break
    return found


def _gold_rank(hits: Sequence[Hit], gold: Sequence[Evidence]) -> int | None:
    for index, hit in enumerate(hits, start=1):
        if any(span_overlap(hit.chunk, row) for row in gold):
            return index
    return None
