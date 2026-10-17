"""Process logging. JSON/structured sinks come later with Langfuse traces."""

from __future__ import annotations

import logging
import sys
from typing import TextIO


def configure_logging(level: str, *, stream: TextIO | None = None) -> None:
    """Configure root logging once at process start."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=stream if stream is not None else sys.stdout,
        force=True,
    )
