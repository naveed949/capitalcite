from __future__ import annotations

from capitalcite.retrieval.fastembed_pins import (
    BGE_ONNX_REPO,
    BGE_ONNX_REV,
    BM25_REPO,
    BM25_REV,
    PINNED_FILES,
)


def test_pins_name_the_ci_resolved_revisions() -> None:
    assert BGE_ONNX_REPO == "Qdrant/bge-small-en-v1.5-onnx-Q"
    assert BGE_ONNX_REV == "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
    assert BM25_REPO == "Qdrant/bm25"
    assert BM25_REV == "22b8d2af71a76161e18dd432d2cee0eefa66e412"
    assert "model_optimized.onnx" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "tokenizer.json" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "english.txt" in PINNED_FILES[(BM25_REPO, BM25_REV)]
