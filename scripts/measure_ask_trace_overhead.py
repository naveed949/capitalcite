from __future__ import annotations

import asyncio
import cProfile
import json
import math
import os
import pstats
import statistics
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

os.environ.setdefault("SEC_USER_AGENT", "capitalcite-overhead overhead@example.com")
os.environ.setdefault("ASK_MODEL", "fake")
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")

from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from capitalcite.api.app import create_app
from capitalcite.api.routes.search import get_setup
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import FakeEmbedder, build_index, setup_retrieval
from capitalcite.trace import configure, shutdown

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "What was the Standardized CET1 ratio?"
QUOTE = "the Standardized CET1 ratio was 15.7 percent"
TIMED = 20
WARMUP = 2
ROUNDS = 2


class _Server(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server = self.server
        assert isinstance(server, _Server)
        server.bodies.append(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, template: str, *args: object) -> None:
        _ = template, args


def main() -> int:
    sha = _git_sha()
    loadavg = Path("/proc/loadavg").read_text(encoding="utf-8").strip()
    nproc = _nproc()
    uname = subprocess.check_output(["uname", "-srm"], text=True).strip()
    server = _Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            application = _app(root)
            with TestClient(application) as http:
                off_rounds: list[list[float]] = []
                on_rounds: list[list[float]] = []
                for _round in range(ROUNDS):
                    configure(_settings())
                    _warm(http)
                    off_rounds.append(_time_block(http))
                    configure(_settings(otel_exporter_otlp_endpoint=endpoint))
                    _warm(http)
                    on_rounds.append(_time_block(http))
                configure(_settings())
                profile_off = _profile(http)
                configure(_settings(otel_exporter_otlp_endpoint=endpoint))
                profile_on = _profile(http)
                shutdown()
        spans = len(server.bodies)
    finally:
        server.shutdown()
        server.server_close()
    if spans < 1:
        raise SystemExit("OTLP receiver got no posts")
    off = [sample for block in off_rounds for sample in block]
    on = [sample for block in on_rounds for sample in block]
    off_stats = _stats(off)
    on_stats = _stats(on)
    overhead_p50 = on_stats["p50_ms"] - off_stats["p50_ms"]
    overhead_p95 = on_stats["p95_ms"] - off_stats["p95_ms"]
    lo_r, hi_r = _overhead_band_ends(overhead_p50, overhead_p95)
    cited = {
        "off_p50": _cite_ms(off_stats["p50_ms"]),
        "off_p95": _cite_ms(off_stats["p95_ms"]),
        "on_p50": _cite_ms(on_stats["p50_ms"]),
        "on_p95": _cite_ms(on_stats["p95_ms"]),
        "overhead_p50": _cite_ms(overhead_p50),
        "overhead_p95": _cite_ms(overhead_p95),
        "overhead_band": f"{lo_r:.1f} to {hi_r:.1f} ms",
    }
    spread = off_stats["max_ms"] - off_stats["min_ms"]
    limiter = _limiter(profile_off, profile_on, spread, overhead_p50)
    payload = {
        "git_sha": sha,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": (
            "TestClient POST /ask, model=fake, one in-memory chunk, FakeEmbedder, "
            f"hybrid retrieve. Each round configures tracing off, runs {WARMUP} warmup "
            f"requests and {TIMED} timed requests, then configures OTLP HTTP to an "
            f"in-process receiver and repeats the warmup and timed requests. "
            f"Rounds: {ROUNDS}. The timer wraps only the POST. "
            "BatchSpanProcessor flush runs at shutdown, outside the timer. "
            "p50 is the median. p95 is the nearest rank, ceil(0.95*n)-1. "
            "Overhead is on minus off for those two percentiles. "
            "Samples from both rounds are pooled."
        ),
        "n": TIMED * ROUNDS,
        "warmup": WARMUP,
        "rounds": ROUNDS,
        "hardware": {
            "nproc": nproc,
            "cpu_count": os.cpu_count(),
            "uname": uname,
            "loadavg": loadavg,
        },
        "errors": 0,
        "answered": len(off) + len(on),
        "otlp_posts": spans,
        "off": off_stats,
        "on": on_stats,
        "overhead": {"p50_ms": overhead_p50, "p95_ms": overhead_p95},
        "round_p50_ms": {
            "off": [statistics.median(block) for block in off_rounds],
            "on": [statistics.median(block) for block in on_rounds],
        },
        "off_spread_ms": spread,
        "profile_top_off": profile_off,
        "profile_top_on": profile_on,
        "limiter": limiter,
        "cited": cited,
    }
    out = ROOT / "evals" / "results" / f"{sha[:12]}-otel-overhead.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    print(json.dumps({"cited": cited, "limiter": limiter, "otlp_posts": spans}, indent=2))
    return 0


def _time_block(http: TestClient) -> list[float]:
    samples: list[float] = []
    for _ in range(TIMED):
        started = time.perf_counter()
        response = http.post("/ask", json={"question": QUESTION, "model": "fake", "k": 4})
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if response.status_code != 200:
            raise SystemExit(f"POST /ask HTTP {response.status_code}")
        body = response.json()
        if not isinstance(body, dict) or body.get("kind") != "answered":
            raise SystemExit(f"POST /ask kind {body!r}")
        samples.append(elapsed_ms)
    return samples


def _warm(http: TestClient) -> None:
    for _ in range(WARMUP):
        response = http.post("/ask", json={"question": QUESTION, "model": "fake", "k": 4})
        if response.status_code != 200:
            raise SystemExit(f"warmup HTTP {response.status_code}")


def _profile(http: TestClient) -> list[dict[str, object]]:
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(5):
        response = http.post("/ask", json={"question": QUESTION, "model": "fake", "k": 4})
        if response.status_code != 200:
            raise SystemExit(f"profile HTTP {response.status_code}")
    profiler.disable()
    stats = pstats.Stats(profiler)
    ranked = sorted(stats.stats.items(), key=lambda item: item[1][2], reverse=True)
    rows: list[dict[str, object]] = []
    for func, (cc, _nc, tottime, cumtime, _callers) in ranked[:8]:
        filename, line, name = _func_parts(func)
        rows.append(
            {
                "file": filename,
                "line": line,
                "name": name,
                "calls": cc,
                "tottime_s": tottime,
                "cumtime_s": cumtime,
            }
        )
    return rows


def _func_parts(func: object) -> tuple[str, int, str]:
    if isinstance(func, tuple) and len(func) == 3:
        filename, line, name = func
        return str(filename), int(line), str(name)
    return "", 0, str(func)


def _stats(samples: list[float]) -> dict[str, Any]:
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": _nearest_rank(samples, 0.95),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "samples_ms": samples,
    }


def _nearest_rank(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _cite_ms(value: float) -> str:
    return f"{value:.1f} ms"


def _overhead_band_ends(p50_ms: float, p95_ms: float) -> tuple[float, float]:
    lo = min(p50_ms, p95_ms)
    hi = max(p50_ms, p95_ms)
    lo_r = math.floor(lo * 10) / 10
    hi_r = math.ceil(hi * 10) / 10
    if hi_r < 0.5:
        hi_r = 0.5
    return lo_r, hi_r


def _limiter(
    off: list[dict[str, object]],
    on: list[dict[str, object]],
    spread_ms: float,
    overhead_p50: float,
) -> str:
    off_row = _hottest_file(off)
    on_row = _hottest_file(on)
    relation = (
        "inside the off-sample spread"
        if abs(overhead_p50) < spread_ms
        else "larger than the off-sample spread"
    )
    return (
        f"Hottest self time outside builtins was {off_row['name']} ({off_row['file']}) "
        f"with tracing off and {on_row['name']} ({on_row['file']}) with OTLP on. "
        "Five profiled requests sit under the TestClient bridge. "
        "One indexed chunk makes retrieve a small share of the POST. "
        f"p50 overhead is {relation} ({overhead_p50:.3f} ms vs off spread {spread_ms:.3f} ms). "
        "The POST timer does not include the collector round trip."
    )


def _hottest_file(rows: list[dict[str, object]]) -> dict[str, object]:
    for row in rows:
        if str(row.get("file")) not in {"", "~"}:
            return row
    if rows:
        return rows[0]
    return {"name": "unknown", "file": ""}


def _app(root: Path):
    chunk = Chunk(
        doc_id="doc-jpm",
        section="Item 7",
        start=10,
        end=10 + len(QUOTE),
        offset=10,
        source_url="https://www.sec.gov/Archives/edgar/data/19617/jpm.htm",
        text=QUOTE,
        token_count=8,
        chunker=ChunkerName.SECTION,
        ticker="JPM",
        form="10-K",
        period="2024-12-31",
        accession="doc-jpm",
    )
    folder = root / "parsed" / "edgar" / "JPM" / "10-K" / "2024-12-31" / "doc-jpm"
    folder.mkdir(parents=True)
    (folder / "chunks.jsonl").write_text(chunk.model_dump_json() + "\n", encoding="utf-8")
    client = AsyncQdrantClient(":memory:")
    settings = _settings()
    setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, root))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    application.dependency_overrides[get_settings] = lambda: settings
    return application


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "sec_user_agent": "capitalcite-overhead overhead@example.com",
        "langfuse_tracing_enabled": False,
        "chunker": ChunkerName.SECTION,
        "chunk_size": 32,
        "chunk_overlap": 4,
        "qdrant_collection": "overhead",
        "embedder": "bge-small",
        "search_k": 8,
        "rrf_k": 60,
        "ask_model": "fake",
        "otel_exporter_otlp_timeout": 0.2,
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _nproc() -> int:
    try:
        text = subprocess.check_output(["nproc"], text=True).strip()
        return int(text)
    except (OSError, ValueError):
        return os.cpu_count() or 1


if __name__ == "__main__":
    raise SystemExit(main())
