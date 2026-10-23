"""Print ``create_app().openapi()`` as JSON on stdout."""

from __future__ import annotations

import json
import os


def main() -> None:
    os.environ.setdefault("SEC_USER_AGENT", "capitalcite-openapi generate@example.com")
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    from capitalcite.api.app import create_app

    print(json.dumps(create_app().openapi()))


if __name__ == "__main__":
    main()
