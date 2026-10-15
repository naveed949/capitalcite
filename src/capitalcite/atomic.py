"""Atomic file writes. A crash mid-write must not leave a half file at the destination."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically."""
    atomic_write_bytes(path, text.encode("utf-8"))
