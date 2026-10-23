from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from huggingface_hub import snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError

from capitalcite.retrieval import fastembed_pins as pins
from capitalcite.retrieval.fastembed_pins import (
    BGE_ONNX_REPO,
    BGE_ONNX_REV,
    BM25_REPO,
    BM25_REV,
    PINNED_FILES,
    prefetch_fastembed_cache,
)


def test_pins_name_the_ci_resolved_revisions() -> None:
    assert BGE_ONNX_REPO == "Qdrant/bge-small-en-v1.5-onnx-Q"
    assert BGE_ONNX_REV == "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
    assert BM25_REPO == "Qdrant/bm25"
    assert BM25_REV == "22b8d2af71a76161e18dd432d2cee0eefa66e412"
    assert "model_optimized.onnx" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "tokenizer.json" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "config.json" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "tokenizer_config.json" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "special_tokens_map.json" in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
    assert "english.txt" in PINNED_FILES[(BM25_REPO, BM25_REV)]
    assert "config.json" in PINNED_FILES[(BM25_REPO, BM25_REV)]


def test_newline_ref_does_not_resolve_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rev = BGE_ONNX_REV
    store = tmp_path / f"models--{BGE_ONNX_REPO.replace('/', '--')}"
    snap = store / "snapshots" / rev
    snap.mkdir(parents=True)
    (snap / "ok.txt").write_text("x", encoding="utf-8")
    refs = store / "refs" / "main"
    refs.parent.mkdir(parents=True)
    refs.write_text(rev + "\n", encoding="utf-8")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(LocalEntryNotFoundError):
        snapshot_download(
            repo_id=BGE_ONNX_REPO,
            revision="main",
            cache_dir=str(tmp_path),
            local_files_only=True,
        )


def test_prefetch_cache_loads_both_repos_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dummy = b"pinned-bytes"
    digest = hashlib.sha256(dummy).hexdigest()
    patched: dict[tuple[str, str], dict[str, str]] = {
        (BGE_ONNX_REPO, BGE_ONNX_REV): {
            name: digest for name in PINNED_FILES[(BGE_ONNX_REPO, BGE_ONNX_REV)]
        },
        (BM25_REPO, BM25_REV): {name: digest for name in PINNED_FILES[(BM25_REPO, BM25_REV)]},
    }
    monkeypatch.setattr(pins, "PINNED_FILES", patched)

    def fake_snapshot(*, repo_id: str, revision: str, cache_dir: str) -> str:
        store = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}"
        snap = store / "snapshots" / revision
        snap.mkdir(parents=True, exist_ok=True)
        for name in patched[(repo_id, revision)]:
            (snap / name).write_bytes(dummy)
        if repo_id == BM25_REPO:
            (snap / "mock.file").touch()
            (snap / "tamil.txt").touch()
        return str(snap)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
    cache = prefetch_fastembed_cache(tmp_path)
    for repo, rev in ((BGE_ONNX_REPO, BGE_ONNX_REV), (BM25_REPO, BM25_REV)):
        ref = pins._repo_dir(cache, repo) / "refs" / "main"
        assert ref.read_bytes() == rev.encode("ascii")
    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot_download)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    for repo in (BGE_ONNX_REPO, BM25_REPO):
        path = snapshot_download(
            repo_id=repo,
            revision="main",
            cache_dir=str(cache),
            local_files_only=True,
        )
        assert Path(path).is_dir()
