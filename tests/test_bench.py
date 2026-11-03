from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from capitalcite.ask.endpoint import host_class_for
from capitalcite.eval.bench import (
    DEFAULT_PIN,
    RECALL_NOT_MEASURED,
    BenchPin,
    BenchReport,
    QueryRecord,
    RecallReport,
    load_pin,
)
from capitalcite.eval.bench_gate import (
    BenchBaseline,
    compare_bench,
    perturb_p95,
    snapshot_bench,
)
from capitalcite.eval.bootstrap import MeanCI
from capitalcite.eval.fixture_index import indexed_corpus
from capitalcite.eval.phases import fold_phases
from capitalcite.eval.prices import load_prices
from capitalcite.eval.runner import EvalUnverified
from capitalcite.trace.types import ObservationKind, SpanRecord


def _span(
    *,
    name: str,
    kind: ObservationKind,
    span_id: str,
    parent_id: str | None,
    duration_ms: float,
) -> SpanRecord:
    return SpanRecord(
        name=name,
        kind=kind,
        trace_id="t",
        span_id=span_id,
        parent_id=parent_id,
        duration_ms=duration_ms,
    )


def test_fold_phases_excludes_nested_retrieve_from_tool() -> None:
    spans = (
        _span(name="ask", kind=ObservationKind.ASK, span_id="a", parent_id=None, duration_ms=40),
        _span(name="tool", kind=ObservationKind.TOOL, span_id="t", parent_id="a", duration_ms=20),
        _span(
            name="retrieve",
            kind=ObservationKind.RETRIEVE,
            span_id="r",
            parent_id="t",
            duration_ms=12,
        ),
        _span(
            name="chat fake",
            kind=ObservationKind.GENERATE,
            span_id="g",
            parent_id="a",
            duration_ms=5,
        ),
        _span(
            name="compile",
            kind=ObservationKind.AGENT,
            span_id="c",
            parent_id="a",
            duration_ms=8,
        ),
    )
    begins = {"a": 0.0, "g": 0.01}
    phases = fold_phases(spans, begins)
    assert phases.retrieval_ms == 12
    assert phases.tool_ms == 8
    assert phases.model_ms == 5
    assert phases.compile_ms == 8
    assert phases.total_ms == 40
    assert phases.ttft_ms == 10
    assert phases.tool_ms + phases.retrieval_ms + phases.model_ms + phases.compile_ms <= (
        phases.total_ms
    )


def test_fold_phases_ignores_overlapping_graph_nodes() -> None:
    spans = (
        _span(name="ask", kind=ObservationKind.ASK, span_id="a", parent_id=None, duration_ms=50),
        _span(
            name="agent",
            kind=ObservationKind.AGENT,
            span_id="ag",
            parent_id="a",
            duration_ms=48,
        ),
        _span(
            name="compile", kind=ObservationKind.AGENT, span_id="c", parent_id="ag", duration_ms=3
        ),
        _span(
            name="graph.plan",
            kind=ObservationKind.AGENT,
            span_id="gp",
            parent_id="ag",
            duration_ms=6,
        ),
        _span(
            name="chat fake",
            kind=ObservationKind.GENERATE,
            span_id="g1",
            parent_id="gp",
            duration_ms=4,
        ),
        _span(
            name="graph.act",
            kind=ObservationKind.AGENT,
            span_id="ga",
            parent_id="ag",
            duration_ms=20,
        ),
        _span(name="tool", kind=ObservationKind.TOOL, span_id="t", parent_id="ga", duration_ms=18),
        _span(
            name="retrieve",
            kind=ObservationKind.RETRIEVE,
            span_id="r",
            parent_id="t",
            duration_ms=10,
        ),
        _span(
            name="graph.synthesize",
            kind=ObservationKind.AGENT,
            span_id="gs",
            parent_id="ag",
            duration_ms=8,
        ),
        _span(
            name="chat fake",
            kind=ObservationKind.GENERATE,
            span_id="g2",
            parent_id="gs",
            duration_ms=5,
        ),
        _span(
            name="graph.stop",
            kind=ObservationKind.AGENT,
            span_id="gst",
            parent_id="ag",
            duration_ms=0.1,
        ),
    )
    phases = fold_phases(spans)
    assert phases.retrieval_ms == 10
    assert phases.tool_ms == 8
    assert phases.model_ms == 9
    assert phases.compile_ms == 3
    assert phases.total_ms == 50
    assert phases.tool_ms + phases.retrieval_ms + phases.model_ms + phases.compile_ms <= (
        phases.total_ms
    )


def _baseline(**updates: object) -> BenchBaseline:
    payload: dict[str, object] = {
        "n_items": 95,
        "model": "fake",
        "compile_once": True,
        "graph_over_rag_p95": 2.0,
        "graph_p95_total_ms": 40.0,
        "rag_p95_total_ms": 20.0,
        "mean_cost_usd": 0.0,
        "mean_compile_ms": 0.0,
    }
    payload.update(updates)
    return BenchBaseline.model_validate(payload)


def test_gate_passes_at_baseline_and_fails_when_ratio_is_tripled() -> None:
    committed = _baseline()
    assert compare_bench(committed, committed).ok is True
    blown = perturb_p95(committed, 3.0)
    report = compare_bench(blown, committed)
    assert report.ok is False
    assert any(row.metric == "graph_over_rag_p95" for row in report.failures)


def test_gate_fails_on_positive_fake_cost() -> None:
    committed = _baseline()
    current = committed.model_copy(update={"mean_cost_usd": 0.01})
    report = compare_bench(current, committed)
    assert report.ok is False
    assert any(row.metric == "mean_cost_usd" for row in report.failures)


def test_gate_fails_compile_once_when_mean_compile_ms_is_half_or_more() -> None:
    committed = _baseline()
    current = committed.model_copy(update={"mean_compile_ms": 0.5})
    report = compare_bench(current, committed)
    assert report.ok is False
    assert any(row.metric == "mean_compile_ms" for row in report.failures)


def test_gate_skips_compile_ms_when_compile_once_is_false() -> None:
    committed = _baseline(compile_once=False, mean_compile_ms=10.0)
    current = committed.model_copy(update={"mean_compile_ms": 10.0})
    report = compare_bench(current, committed)
    assert report.ok is True
    assert not any(row.metric == "mean_compile_ms" for row in report.failures)


def _ci() -> MeanCI:
    return MeanCI(point=0.0, lo=0.0, hi=0.0, B=10000, seed=20261103, n=1)


def _pin() -> BenchPin:
    return BenchPin(
        n_items=1,
        sets={"v1_recall": 1, "metrics": 0},
        openai_model="gpt-4o-mini",
        seed=20261103,
        temperature=0.0,
        tokenizer="o200k_base",
        prompt_hash="a" * 64,
        prices="evals/bench/prices/openai-2026-11-03.json",
        bootstrap_B=10000,
        bootstrap_seed=20261103,
        paid_run={"status": "UNVERIFIED"},
    )


def _row(arm: str, total: float, accurate: bool) -> QueryRecord:
    from capitalcite.eval.phases import PhaseTimes

    return QueryRecord(
        item_id="x",
        item_set="v1_recall",
        arm=arm,  # type: ignore[arg-type]
        kind="answered",
        answer="yes",
        phases=PhaseTimes(
            ttft_ms=1,
            tool_ms=0,
            retrieval_ms=2,
            model_ms=1,
            compile_ms=0,
            total_ms=total,
        ),
        tokens_in=10,
        tokens_out=1,
        cost_usd=0.0,
        recall_at_5=None,
        accurate=accurate,
    )


def test_snapshot_bench_uses_same_run_graph_over_rag() -> None:
    report = BenchReport(
        git_sha="deadbeefdead",
        compile_once=True,
        pin=_pin(),
        prices=load_prices(),
        n_items=1,
        queries=(_row("rag", 10, True), _row("graph", 30, True), _row("loop", 20, True)),
        identity_digest="b" * 64,
        recall=RecallReport(status="not_measured", reason=RECALL_NOT_MEASURED),
        accuracy_delta_graph_minus_loop=_ci(),
        accuracy_delta_graph_minus_rag=_ci(),
        bottleneck_phase="retrieval",
        estimated_paid_tokens_per_item={"tokens_in": 10.0, "tokens_out": 1.0},
    )
    snap = snapshot_bench(report)
    assert snap.graph_p95_total_ms == 30.0
    assert snap.rag_p95_total_ms == 10.0
    assert snap.graph_over_rag_p95 == 3.0
    assert snap.mean_cost_usd == 0.0
    assert snap.mean_compile_ms == 0.0
    assert snap.n_items == 1
    assert snap.schema_version == 2
    assert report.arms["graph"].recall_at_5 is None
    dumped = json.loads(report.model_dump_json())
    assert dumped["arms"]["graph"]["recall_at_5"] is None
    assert dumped["arms"]["rag"]["recall_at_5"] is None
    assert dumped["arms"]["loop"]["recall_at_5"] is None


def test_paid_requires_openai_model() -> None:
    from capitalcite.eval.bench import _bench_settings

    with pytest.raises(EvalUnverified, match="--paid requires ASK_MODEL=openai"):
        _bench_settings(_pin(), paid=True)


def test_openai_requires_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    from capitalcite.eval.bench import _bench_settings
    from capitalcite.eval.runner import EvalUnverified as Unverified

    monkeypatch.setenv("ASK_MODEL", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(Unverified, match="hosted OpenAI requires --paid"):
        _bench_settings(_pin(), paid=False)


async def test_paid_sends_pin_model_seed_and_temperature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from capitalcite.ask.models import build_structured_model
    from capitalcite.chunking.models import Chunk, ChunkerName
    from capitalcite.eval.bench import _bench_settings
    from capitalcite.retrieval.types import Hit

    warehouse = tmp_path / "ci.duckdb"
    warehouse.write_bytes(b"")
    monkeypatch.setenv("ASK_MODEL", "openai")
    monkeypatch.setenv("ASK_OPENAI_MODEL", "gpt-not-from-pin")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("METRIC_QUERY_BACKEND", "catalog")
    monkeypatch.setenv("WAREHOUSE_PATH", str(warehouse))
    pin = load_pin(DEFAULT_PIN)
    seen: list[dict[str, object]] = []
    seen_url: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_url.append(str(request.url))
        seen.append(json.loads(request.content))
        content = json.dumps(
            {
                "kind": "answered",
                "answer": "tier one capital buffer",
                "citations": [{"index": 1, "quote": "tier one capital"}],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    ready = _bench_settings(pin, paid=True)
    settings = ready.settings
    model = build_structured_model(
        "openai",
        endpoint=ready.endpoint,
        seed=settings.ask_seed,
        temperature=settings.ask_temperature,
        transport=httpx.MockTransport(handler),
    )
    hit = Hit(
        score=1.0,
        chunk=Chunk(
            doc_id="doc-jpm",
            section="Item 15",
            start=10,
            end=40,
            offset=10,
            source_url="https://www.sec.gov/Archives/edgar/data/19617/jpm.htm",
            text="tier one capital buffer",
            token_count=4,
            chunker=ChunkerName.SECTION,
            ticker="JPM",
            form="10-K",
            period="2023-12-31",
            accession="doc-jpm",
        ),
    )
    await model.complete("What buffer is described?", [hit])
    assert seen
    body = seen[0]
    assert body["model"] == pin.openai_model
    assert body["seed"] == pin.seed
    assert body["temperature"] == pin.temperature
    assert body["model"] != "gpt-not-from-pin"
    assert seen_url[0] == "https://api.openai.com/v1/chat/completions"
    assert ready.endpoint is not None
    assert ready.endpoint.label.billed is True


async def test_full_corpus_missing_filings(tmp_path: Path) -> None:
    with pytest.raises(EvalUnverified, match=r"need 12 filings|missing raw/edgar"):
        async with indexed_corpus(tmp_path):
            pass


def test_shipped_pins_keep_prompt_hash_and_host_class() -> None:
    hosted = load_pin(DEFAULT_PIN)
    local = load_pin(Path("evals/bench/pin-lfm3-local.json"))
    assert hosted.host_class is None
    assert hosted.prompt_hash == local.prompt_hash
    assert local.host_class == "localhost"
    assert local.openai_model == "lfm3:latest"
    assert local.model == "openai"
    rest = {key: value for key, value in local.paid_run.items() if key != "command"}
    assert "://" not in json.dumps(rest)


def _pin_command_env(command: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for token in command.split():
        if "=" not in token:
            break
        key, _, value = token.partition("=")
        if not key.isidentifier():
            break
        env[key] = value
    return env


def test_pin_commands_match_host_class() -> None:
    for path in (DEFAULT_PIN, Path("evals/bench/pin-lfm3-local.json")):
        pin = load_pin(path)
        command = pin.paid_run["command"]
        assert isinstance(command, str)
        env = _pin_command_env(command)
        actual = host_class_for(env.get("ASK_OPENAI_BASE_URL"))
        if pin.host_class is None:
            assert actual == "openai"
            assert "ASK_OPENAI_BASE_URL" not in env
        else:
            assert actual == pin.host_class
            assert env["ASK_OPENAI_BASE_URL"]


def _rebuild_pin(**update: object) -> BenchPin:
    payload = _pin().model_dump()
    payload.update(update)
    return BenchPin.model_validate(payload)


def test_pin_rejects_userinfo_and_non_loopback_command_urls() -> None:
    local_base = {
        "model": "openai",
        "host_class": "localhost",
        "openai_model": "lfm3:latest",
        "prices": "evals/bench/prices/lfm3-local.json",
    }
    with pytest.raises(ValidationError, match="userinfo") as leaked:
        _rebuild_pin(
            **local_base,
            paid_run={
                "status": "UNVERIFIED",
                "command": "ASK_OPENAI_BASE_URL=http://user:secret@127.0.0.1:11434/v1 uv run x",
            },
        )
    text = str(leaked.value)
    assert "secret" not in text
    assert "user:secret" not in text
    with pytest.raises(ValidationError, match="loopback"):
        _rebuild_pin(
            **local_base,
            paid_run={
                "status": "UNVERIFIED",
                "command": "ASK_OPENAI_BASE_URL=https://example.com/v1 uv run x",
            },
        )
    with pytest.raises(ValidationError, match="local pin"):
        _rebuild_pin(
            paid_run={
                "status": "UNVERIFIED",
                "command": "ASK_OPENAI_BASE_URL=http://localhost:11434/v1 uv run x",
            }
        )
    ok = _rebuild_pin(
        **local_base,
        paid_run={
            "status": "UNVERIFIED",
            "command": "ASK_OPENAI_BASE_URL=http://localhost:11434/v1 uv run x",
        },
    )
    assert "localhost:11434" in str(ok.paid_run["command"])
    quoted = _rebuild_pin(
        **local_base,
        paid_run={
            "status": "UNVERIFIED",
            "command": 'ASK_OPENAI_BASE_URL="http://localhost:11434/v1" uv run x',
        },
    )
    assert "localhost:11434" in str(quoted.paid_run["command"])


def test_ftp_command_url_is_refused_and_absent_from_results() -> None:
    from capitalcite.eval.bench import _redact_command_urls

    local_base = {
        "model": "openai",
        "host_class": "localhost",
        "openai_model": "lfm3:latest",
        "prices": "evals/bench/prices/lfm3-local.json",
    }
    ftp_tail = "# mirror ftp://remote.example:2121/weights"
    local_cmd = f"ASK_OPENAI_BASE_URL=http://localhost:11434/v1 uv run x {ftp_tail}"
    hosted_cmd = f"ASK_MODEL=openai uv run x {ftp_tail}"
    with pytest.raises(ValidationError, match="loopback"):
        _rebuild_pin(**local_base, paid_run={"status": "UNVERIFIED", "command": local_cmd})
    with pytest.raises(ValidationError, match="local pin"):
        _rebuild_pin(paid_run={"status": "UNVERIFIED", "command": hosted_cmd})
    for command in (local_cmd, hosted_cmd):
        redacted = _redact_command_urls(command)
        assert "ftp://" not in redacted
        assert "://" not in redacted
        assert "remote.example" not in redacted
        assert "2121" not in redacted


def test_local_cost_is_zero_and_not_comparable() -> None:
    from capitalcite.ask.endpoint import parse_chat_endpoint
    from capitalcite.eval.bench import bench_provenance, bench_stem_suffix, provenance_sentence
    from capitalcite.eval.prices import ModelPrice, bench_cost_usd, require_local_zero

    pin = load_pin(Path("evals/bench/pin-lfm3-local.json"))
    endpoint = parse_chat_endpoint(
        "http://127.0.0.1:11434/v1",
        "sk-hosted-secret",
        pin.openai_model,
    )
    assert endpoint.label.billed is False
    assert endpoint.headers() == {}
    provenance = bench_provenance(endpoint, pin, Path("evals/bench/pin-lfm3-local.json"))
    prices = load_prices(Path(pin.prices))
    require_local_zero(prices, pin.openai_model)
    cost = bench_cost_usd(
        prices,
        provenance,
        model_id=pin.openai_model,
        tokens_in=1_000_000,
        tokens_out=1_000_000,
    )
    assert cost == 0.0
    assert f"{cost:.6f}" == "0.000000"
    assert provenance.provider == "local"
    assert provenance.host_class == "localhost"
    assert provenance.model_id == "lfm3:latest"
    assert provenance.comparable_to_hosted is False
    assert provenance.latency_scope == "host_hardware"
    assert bench_stem_suffix(compile_once=True, provenance=provenance) == "-bench-localhost"
    sentence = provenance_sentence(provenance)
    assert "://" not in sentence
    assert "cost" not in sentence.lower()
    assert "not comparable" in sentence
    bumped = prices.model_copy(
        update={"models": {"lfm3:latest": ModelPrice(input_usd_per_1m=0.15, output_usd_per_1m=0.0)}}
    )
    with pytest.raises(ValueError, match="must be 0"):
        require_local_zero(bumped, "lfm3:latest")
    hosted_prices = load_prices()
    with pytest.raises(ValueError, match="billing local"):
        bench_cost_usd(
            hosted_prices,
            provenance,
            model_id=pin.openai_model,
            tokens_in=1,
            tokens_out=1,
        )


def test_local_pin_refuses_paid_and_a_hosted_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from capitalcite.eval.bench import _bench_settings

    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("ASK_MODEL", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    pin = load_pin(Path("evals/bench/pin-lfm3-local.json"))
    with pytest.raises(EvalUnverified, match="does not apply to a local"):
        _bench_settings(pin, paid=True)
    monkeypatch.delenv("ASK_OPENAI_BASE_URL")
    with pytest.raises(EvalUnverified, match="does not match chat host class openai"):
        _bench_settings(pin, paid=False)
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "https://example.com/v1")
    with pytest.raises(EvalUnverified, match="third-party"):
        _bench_settings(pin, paid=False)


def test_bench_provenance_refuses_a_third_party_host() -> None:
    from capitalcite.ask.endpoint import parse_chat_endpoint
    from capitalcite.eval.bench import bench_provenance

    pin = load_pin(Path("evals/bench/pin-lfm3-local.json"))
    endpoint = parse_chat_endpoint("https://example.com/v1", "sk-secret", pin.openai_model)
    with pytest.raises(EvalUnverified, match="third-party"):
        bench_provenance(endpoint, pin, Path("evals/bench/pin-lfm3-local.json"))


def test_local_report_json_has_no_url(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from capitalcite.ask.endpoint import parse_chat_endpoint
    from capitalcite.cli import app
    from capitalcite.eval.bench import bench_provenance

    pin = load_pin(Path("evals/bench/pin-lfm3-local.json"))
    assert "://" in str(pin.paid_run["command"])
    endpoint = parse_chat_endpoint("http://127.0.0.1:11434/v1", None, "lfm3:latest")
    provenance = bench_provenance(endpoint, pin, Path("evals/bench/pin-lfm3-local.json"))
    report = BenchReport(
        git_sha="abc123",
        compile_once=True,
        pin=pin,
        prices=load_prices(Path("evals/bench/prices/lfm3-local.json")),
        provenance=provenance,
        n_items=1,
        queries=(_row("rag", 10, True), _row("graph", 30, True), _row("loop", 20, True)),
        identity_digest="b" * 64,
        recall=RecallReport(status="not_measured", reason=RECALL_NOT_MEASURED),
        accuracy_delta_graph_minus_loop=_ci(),
        accuracy_delta_graph_minus_rag=_ci(),
        bottleneck_phase="retrieval",
        estimated_paid_tokens_per_item={"tokens_in": 10.0, "tokens_out": 1.0},
    )
    text = report.model_dump_json()
    assert "://" not in text
    assert "11434" not in text
    body = json.loads(text)
    command = body["pin"]["paid_run"]["command"]
    assert "ASK_OPENAI_BASE_URL=localhost" in command
    assert body["provenance"]["host_class"] == "localhost"
    assert body["provenance"]["provider"] == "local"
    assert body["provenance"]["comparable_to_hosted"] is False
    assert body["queries"][0]["cost_usd"] == 0.0
    payload = json.loads(text)
    del payload["provenance"]
    legacy = BenchReport.model_validate(payload)
    assert legacy.provenance.provider == "fake"
    assert legacy.provenance.model_id == "fake"
    path = tmp_path / "local.json"
    path.write_text(text + "\n", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--suite",
            "bench",
            "--from-results",
            str(path),
            "--baseline",
            "evals/baselines/bench.json",
        ],
    )
    assert result.exit_code == 1
    assert "fake-model" in result.output
