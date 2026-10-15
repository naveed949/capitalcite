"""Process logging. JSON/structured sinks come later with Langfuse traces."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str) -> None:
    """Configure root logging once at process start."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )
