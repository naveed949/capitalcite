"""Replay recorded EDGAR fixtures through httpx.MockTransport."""

from __future__ import annotations

from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "edgar"
PRIMARY_HTML = (FIXTURES / "docs" / "primary.html").read_bytes()
USER_AGENT = "capitalcite-tests tests@example.com"


class RecordingTransport(httpx.MockTransport):
    """MockTransport that records URLs and User-Agent headers."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.user_agents: list[str] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.user_agents.append(request.headers.get("User-Agent", ""))
        return replay_handler(request)


def replay_handler(request: httpx.Request) -> httpx.Response:
    """Serve recorded tickers, submissions, extra files, and stub documents."""
    url = str(request.url)
    path = request.url.path
    if path == "/files/company_tickers.json":
        body = (FIXTURES / "company_tickers.json").read_bytes()
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
    if path.startswith("/submissions/") and path.endswith(".json"):
        name = path.rsplit("/", 1)[-1]
        fixture = FIXTURES / "submissions" / name
        if not fixture.is_file():
            return httpx.Response(404, text=f"missing fixture {name}")
        return httpx.Response(
            200,
            content=fixture.read_bytes(),
            headers={"Content-Type": "application/json"},
        )
    if path.startswith("/Archives/edgar/data/"):
        return httpx.Response(
            200,
            content=PRIMARY_HTML,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    return httpx.Response(404, text=f"unrecorded URL {url}")


async def noop_sleep(_seconds: float) -> None:
    return None
