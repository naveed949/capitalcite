"""Pinned FastEmbed snapshots. Build writes this cache. Runtime loads it offline."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

BGE_ONNX_REPO = "Qdrant/bge-small-en-v1.5-onnx-Q"
BGE_ONNX_REV = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
BM25_REPO = "Qdrant/bm25"
BM25_REV = "22b8d2af71a76161e18dd432d2cee0eefa66e412"

PINNED_FILES: dict[tuple[str, str], dict[str, str]] = {
    (BGE_ONNX_REPO, BGE_ONNX_REV): {
        "model_optimized.onnx": "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431",
        "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        "config.json": "13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd",
        "tokenizer_config.json": "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
        "special_tokens_map.json": (
            "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a"
        ),
    },
    (BM25_REPO, BM25_REV): {
        "english.txt": "019f104ba2ed07436d05f9cdd3383034ad66014edc27fc651f837e1a038b6451",
        "config.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    },
}


def _repo_dir(cache: Path, repo: str) -> Path:
    return cache / f"models--{repo.replace('/', '--')}"


def verify_pinned_files(cache: Path) -> None:
    for (repo, rev), files in PINNED_FILES.items():
        snap = _repo_dir(cache, repo) / "snapshots" / rev
        for name, expected in files.items():
            path = snap / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected:
                raise RuntimeError(f"{repo} {name} sha256={digest} expected={expected}")


def prefetch_fastembed_cache(cache_dir: str | Path | None = None) -> Path:
    """Download pinned revisions into the FastEmbed cache and check file hashes."""
    raw = cache_dir if cache_dir is not None else os.environ["FASTEMBED_CACHE_PATH"]
    cache = Path(raw)
    cache.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    for repo, rev in ((BGE_ONNX_REPO, BGE_ONNX_REV), (BM25_REPO, BM25_REV)):
        snapshot_download(repo_id=repo, revision=rev, cache_dir=str(cache))
        refs = _repo_dir(cache, repo) / "refs" / "main"
        refs.parent.mkdir(parents=True, exist_ok=True)
        refs.write_text(rev, encoding="utf-8")
        if repo == BM25_REPO:
            snap = _repo_dir(cache, repo) / "snapshots" / rev
            (snap / "mock.file").touch()
            (snap / "tamil.txt").touch()
    verify_pinned_files(cache)
    return cache
